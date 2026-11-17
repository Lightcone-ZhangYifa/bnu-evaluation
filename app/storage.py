"""Private, immutable, content-addressed originals; bounded decoder and safe ZIPs."""
from pathlib import Path, PurePosixPath
from io import BytesIO
from PIL import Image, UnidentifiedImageError
import hashlib, os, tempfile, zipfile, socket, struct, warnings, re
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from .db import Resource

Image.MAX_IMAGE_PIXELS = 40_000_000
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass  # HEIC requests fail explicitly, never masquerade as a valid image.

IMAGE_TYPES = {"JPEG":"image/jpeg", "PNG":"image/png", "WEBP":"image/webp", "HEIF":"image/heic", "HEIC":"image/heic"}

class InvalidFile(ValueError): pass

def safe_zip(data: bytes, total_limit=512*1024*1024, member_limit=5000):
    try: archive = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile as exc: raise InvalidFile("压缩包格式无效。") from exc
    entries = archive.infolist()
    if len(entries) > member_limit: raise InvalidFile("压缩包成员过多。")
    total, names = 0, set()
    for entry in entries:
        name = entry.filename
        path = PurePosixPath(name)
        if (name in names or name.startswith(("/", "\\")) or "\\" in name or
                ".." in path.parts or ":" in name or "\x00" in name):
            raise InvalidFile("压缩包包含重复路径或不安全路径。")
        names.add(name)
        if (entry.external_attr >> 16) & 0o170000 == 0o120000:
            raise InvalidFile("压缩包不允许符号链接。")
        if entry.flag_bits & 1: raise InvalidFile("不接受加密 ZIP；请先在本地解密。")
        total += entry.file_size
        if entry.file_size > total_limit or total > total_limit:
            raise InvalidFile("压缩包解压后超过大小限制。")
        if entry.file_size > 1024*1024 and entry.file_size/max(entry.compress_size,1) > 200:
            raise InvalidFile("异常压缩率。")
    return archive

def sniff(data: bytes, allowed: str):
    if not data: raise InvalidFile("文件为空。")
    if allowed == "image" or data[:12].startswith((b"\xff\xd8", b"\x89PNG", b"RIFF")) or (len(data)>12 and b"ftyphei" in data[:20]):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(data)) as img:
                    if img.format not in IMAGE_TYPES: raise InvalidFile("只接受 JPEG、PNG、WebP、HEIC 图片，不接受 SVG/GIF。")
                    kind = IMAGE_TYPES[img.format]
                    if img.width < 16 or img.height < 16: raise InvalidFile("图片过小，无法作为材料。")
                    if getattr(img,"n_frames",1) != 1: raise InvalidFile("只接受静态、单页图片。")
                    img.verify()
                with Image.open(BytesIO(data)) as img: img.load()
                return kind
        except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise InvalidFile("图片无法解码，格式不支持或分辨率过大。") from exc
    if allowed in ("document", "feedback"):
        if data.startswith(b"%PDF-") and b"%%EOF" in data[-2048:]: return "application/pdf"
        if data.startswith(b"PK\x03\x04"):
            archive = safe_zip(data, total_limit=80*1024*1024, member_limit=1000)
            if "word/document.xml" in archive.namelist(): return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            if "xl/workbook.xml" in archive.namelist(): return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if allowed == "feedback":
        if len(data)>12 and data[4:8] == b"ftyp": return "video/mp4"
        if data.startswith(b"\x1a\x45\xdf\xa3"): return "video/webm"
    raise InvalidFile("文件类型不在允许范围，或文件内容与格式不符。")

class Store:
    def __init__(self, settings):
        self.settings = settings
        self.root = settings.data_dir / "objects"
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, sha):
        if not re.fullmatch(r"[a-f0-9]{64}",sha): raise InvalidFile("非法文件标识。")
        return self.root / sha[:2] / sha

    def scan(self, data):
        if not self.settings.clamav_host: return
        try:
            with socket.create_connection((self.settings.clamav_host,3310),timeout=20) as sock:
                sock.settimeout(90)
                sock.sendall(b"zINSTREAM\0")
                for offset in range(0,len(data),128*1024):
                    part=data[offset:offset+128*1024]
                    sock.sendall(struct.pack("!I",len(part))+part)
                sock.sendall(struct.pack("!I",0))
                response=sock.recv(4096)
                if b" OK" not in response: raise InvalidFile("文件未通过安全扫描。")
        except (OSError, TimeoutError) as exc:
            raise InvalidFile("安全扫描暂不可用，未接受文件，请稍后重试。") from exc

    def put(self, db, data: bytes, allowed="image"):
        limits={"image":self.settings.max_image_bytes,"document":self.settings.max_document_bytes,"feedback":self.settings.max_feedback_bytes}
        if len(data)>limits[allowed]: raise InvalidFile("文件超过大小限制。")
        self.scan(data)
        media = sniff(data,allowed)
        sha = hashlib.sha256(data).hexdigest()
        self.put_verified(db, sha, data, media)
        return {"sha256":sha,"size":len(data),"media_type":media}

    def put_verified(self, db, sha, data, media):
        if hashlib.sha256(data).hexdigest()!=sha: raise InvalidFile("资源哈希校验失败。")
        path=self.path(sha); path.parent.mkdir(parents=True,exist_ok=True)
        if not path.exists():
            fd, tmp = tempfile.mkstemp(dir=path.parent,prefix=".incoming-")
            try:
                with os.fdopen(fd,"wb") as f:
                    f.write(data); f.flush(); os.fsync(f.fileno())
                os.chmod(tmp,0o440)
                # hard-link create-if-absent: never replace an existing original.
                try: os.link(tmp,path)
                except FileExistsError: pass
                # Windows does not support opening a directory for fsync. The
                # file itself is fsynced before atomic link creation on both OSes.
                if os.name != "nt":
                    dirfd=os.open(path.parent,os.O_RDONLY)
                    try:os.fsync(dirfd)
                    finally:os.close(dirfd)
            finally:
                Path(tmp).unlink(missing_ok=True)
        # A pre-existing object must be the same bytes, not merely the same path.
        if path.stat().st_size != len(data) or hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            raise InvalidFile("已存在资源校验失败，未覆盖原件；请联系管理员核查存储。")
        insert = sqlite_insert if db.bind.dialect.name == "sqlite" else pg_insert
        db.execute(insert(Resource).values(sha256=sha,size=len(data),media_type=media).on_conflict_do_nothing(index_elements=["sha256"]))
        return sha

    def preview(self, sha):
        """Re-encode for browser display. EXIF and executable ancillary chunks removed.
        Generated previews are not independent evidence and are not archived.
        """
        data=self.path(sha).read_bytes()
        from PIL import ImageOps
        with Image.open(BytesIO(data)) as im:
            img=ImageOps.exif_transpose(im).convert("RGB")
            img.thumbnail((2600,2600))
            out=BytesIO();img.save(out,"JPEG",quality=91,optimize=True)
            return out.getvalue()
