from dataclasses import dataclass
from pathlib import Path
import os

@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_url: str
    environment: str
    secret: str
    origin: str
    admin_path: str
    bootstrap_password: str
    admin_totp: str
    clamav_host: str
    max_image_bytes: int = 20 * 1024 * 1024
    max_document_bytes: int = 40 * 1024 * 1024
    max_feedback_bytes: int = 100 * 1024 * 1024
    max_archive_bytes: int = 512 * 1024 * 1024
    max_request_bytes: int = 520 * 1024 * 1024
    max_project_evidence: int = 12000
    max_student_evidence: int = 200

    @property
    def production(self):
        return self.environment == "production"

    @classmethod
    def from_env(cls):
        directory = Path(os.environ.get("DATA_DIR", "./var")).resolve()
        obj = cls(directory, os.environ.get("DATABASE_URL", f"sqlite:///{directory}/ledger.db"),
            os.environ.get("ENVIRONMENT", "development"), os.environ.get("APP_SECRET", ""),
            os.environ.get("PUBLIC_ORIGIN", "http://localhost:8000").rstrip("/"),
            os.environ.get("ADMIN_PATH", "/control-room"), os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", ""),
            os.environ.get("ADMIN_TOTP_SECRET", ""), os.environ.get("CLAMAV_HOST", ""))
        if len(obj.secret) < 32:
            raise RuntimeError("APP_SECRET 必须为至少 32 字符的随机密钥；运行 python manage.py configure 生成。")
        if not obj.admin_path.startswith("/") or obj.admin_path in ("/", "/login", "/staff", "/student") or ".." in obj.admin_path or "?" in obj.admin_path:
            raise RuntimeError("ADMIN_PATH 必须是独立的绝对路径。")
        if obj.production:
            if not obj.origin.startswith("https://"):
                raise RuntimeError("生产模式必须配置 HTTPS PUBLIC_ORIGIN。")
            if not obj.database_url.startswith("postgresql"):
                raise RuntimeError("生产模式必须使用 PostgreSQL；SQLite 仅用于本机验证。")
            if not obj.admin_totp or not obj.clamav_host:
                raise RuntimeError("生产模式必须配置管理员 TOTP 和 CLAMAV_HOST。")
        directory.mkdir(parents=True, exist_ok=True)
        return obj
