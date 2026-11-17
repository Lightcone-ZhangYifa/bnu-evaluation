"""Interactive deployment and operations; standard library only until app commands run.

No shell interpolation, default passwords, automatic package-manager changes,
volume removal or in-place restore. Secrets are never echoed by status/check.
"""
from __future__ import annotations
from contextlib import closing
import argparse
import base64
import getpass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import tarfile
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit, unquote
import urllib.request
import venv
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SYSTEM_NAME = '北京师范大学学生综合测评管理系统'
MODES = {'local': '本机运行（Python 虚拟环境）', 'docker': 'Docker Compose 服务器部署',
         'native': 'Linux 原生服务器部署（systemd）'}
SERVICE = 'bnu-evaluation'
class OperationError(RuntimeError):
    pass


def load_env(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise OperationError(f'配置文件不存在：{path}。请先执行 configure。')
    values = {}
    for index, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, sep, value = line.partition('=')
        if not sep or not re.fullmatch(r'[A-Z][A-Z0-9_]*', key):
            raise OperationError(f'配置文件第 {index} 行格式错误；应为 KEY=value。')
        # Generated files use unquoted single-line values; accept quoted legacy values.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if '\x00' in value:
            raise OperationError('配置文件不能包含 NUL 字符。')
        values[key] = value
    return values


def write_secret(path: Path, values: dict[str, str]) -> None:
    if path.exists():
        raise OperationError(f'拒绝覆盖已有配置：{path}。已有系统不能重新生成密钥。')
    for key, value in values.items():
        if any(c in str(value) for c in '\r\n\x00'):
            raise OperationError(f'{key} 不能包含换行或 NUL 字符。')
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write('# 含系统密钥。请离线备份，不得发到群聊或提交到代码仓库。\n')
        for key, value in values.items():
            f.write(f'{key}={value}\n')


def profile(root: Path = ROOT) -> str:
    file = root / '.deployment.json'
    if file.exists():
        mode = json.loads(file.read_text(encoding='utf-8')).get('mode')
        if mode not in MODES:
            raise OperationError('部署方式配置无效。')
        return mode
    env = load_env(root / '.env')
    return 'local' if env.get('ENVIRONMENT') != 'production' else ('native' if 'DATABASE_URL' in env else 'docker')


def run(args: list[str | Path], *, root: Path = ROOT, env=None, capture=False, check=True):
    result = subprocess.run([str(x) for x in args], cwd=root, env=env,
                            text=True, capture_output=capture, check=False)
    if check and result.returncode:
        raise OperationError(f'命令未完成（退出码 {result.returncode}）。请检查上方错误，修正后重试。')
    return result


def python_path(root: Path = ROOT) -> Path:
    executable = root / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not executable.exists():
        raise OperationError('尚未安装应用依赖。请先执行 install。')
    return executable


def environment(root: Path = ROOT, migration=False) -> dict[str, str]:
    env = {**os.environ, **load_env(root / '.env')}
    if migration:
        env.update(load_env(root / '.env.migration'))
    return env


def docker(*args, root=ROOT, **kwargs):
    if not shutil.which('docker'):
        raise OperationError('未找到 Docker。请按部署说明安装 Docker Engine 与 Compose 插件。')
    return run(['docker', 'compose', *args], root=root, **kwargs)


def confirm(message: str, word='确认') -> bool:
    print(message)
    return input(f'输入“{word}”继续，其他输入取消：').strip() == word


def configure(mode: str, domain='', port=8000, database_url='', migration_url='', root: Path = ROOT):
    if mode not in MODES:
        raise OperationError('部署方式无效。')
    if (root / '.env').exists() or (root / '.env.migration').exists():
        raise OperationError('配置已存在，拒绝重新生成。升级请保留原配置、数据库和原始资源。')
    if not 1024 <= port <= 65535:
        raise OperationError('本机端口必须为 1024—65535。')
    if mode != 'local' and not re.fullmatch(r'(?=.{1,253}$)[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?', domain):
        raise OperationError('请填写实际域名，不要包含 https://、路径或端口。')
    secret = secrets.token_urlsafe(48)
    values = {'APP_SECRET': secret, 'BOOTSTRAP_ADMIN_PASSWORD': secrets.token_urlsafe(24),
              'ADMIN_PATH': '/ops-' + secrets.token_hex(12),
              'ENVIRONMENT': 'development' if mode == 'local' else 'production',
              'PUBLIC_ORIGIN': f'http://localhost:{port}' if mode == 'local' else 'https://' + domain,
              'ADMIN_TOTP_SECRET': '' if mode == 'local' else base64.b32encode(secrets.token_bytes(20)).decode().rstrip('='),
              'CLAMAV_HOST': '' if mode == 'local' else ('scanner' if mode == 'docker' else '127.0.0.1')}
    if mode == 'local':
        data = root / 'var'
        values.update(DATA_DIR=str(data), DATABASE_URL='sqlite:///' + str(data / 'ledger.db'))
    elif mode == 'docker':
        values.update(DOMAIN=domain, PG_ADMIN_PASSWORD=secrets.token_hex(24), APP_DB_PASSWORD=secrets.token_hex(24))
    else:
        if not database_url or not migration_url:
            raise OperationError('原生部署需要运行账号与迁移账号两条 PostgreSQL 连接串。不要使用同一个数据库账号。')
        for url in (database_url, migration_url):
            parsed = urlsplit(url.replace('postgresql+psycopg://', 'postgresql://'))
            if parsed.scheme != 'postgresql' or not parsed.username or not parsed.hostname or not parsed.path.strip('/'):
                raise OperationError('PostgreSQL 连接串不完整。')
            if any(c in url for c in '\r\n\x00'):
                raise OperationError('连接串不能含换行字符。')
        if urlsplit(database_url).username == urlsplit(migration_url).username:
            raise OperationError('运行账号与迁移账号必须不同。')
        if urlsplit(database_url).username != 'zc_app':
            raise OperationError('原生运行账号应为 zc_app，与最小权限授予脚本一致。')
        values.update(DOMAIN=domain, DATA_DIR=str(root / 'var'), DATABASE_URL=database_url)
    root.mkdir(parents=True, exist_ok=True)
    write_secret(root / '.env', values)
    if mode == 'native':
        write_secret(root / '.env.migration', {'DATABASE_URL': migration_url})
    (root / '.deployment.json').write_text(json.dumps({'mode': mode}, ensure_ascii=False) + '\n', encoding='utf-8')
    print('配置已创建。管理员账号：admin。初始密码与独立入口保存在 .env 中，不在屏幕显示。')
    if mode != 'local':
        print('请将 ADMIN_TOTP_SECRET 添加到认证器（SHA1、6位、30秒），再进行生产登录。')
    print('下一步：install → init-db → start。原生部署另需 native-files 与服务安装。')
    return values


def install(root: Path = ROOT, wheelhouse=None):
    mode = profile(root)
    if mode == 'docker':
        docker('config', '--quiet', root=root)
        docker('build', root=root)
        return
    if sys.version_info < (3, 11):
        raise OperationError('需要 Python 3.11 或更高版本；已测试版本见验收记录。')
    if not (root / '.venv').exists():
        print('正在创建 Python 虚拟环境。')
        venv.EnvBuilder(with_pip=True).create(root / '.venv')
    args = [python_path(root), '-m', 'pip', 'install', '-r', 'requirements.txt']
    if wheelhouse:
        directory = Path(wheelhouse).resolve()
        if not directory.is_dir():
            raise OperationError('离线依赖目录不存在。')
        args += ['--no-index', '--find-links', str(directory)]
    run(args, root=root)
    print('依赖安装命令已完成。')


def init_db(root: Path = ROOT):
    mode = profile(root)
    if mode == 'docker':
        docker('up', '-d', '--wait', 'database', root=root)
        docker('run', '--rm', '-T', 'migrate', root=root)
    else:
        env = environment(root, migration=mode == 'native')
        Path(env['DATA_DIR']).mkdir(parents=True, exist_ok=True)
        run([python_path(root), '-m', 'app.manage', 'init-db', *(['--grant-runtime'] if mode == 'native' else [])], root=root, env=env)


def health(root: Path = ROOT, external=False):
    env = load_env(root / '.env')
    mode = profile(root)
    origin = env['PUBLIC_ORIGIN'] if external or mode == 'docker' else 'http://127.0.0.1:' + str(urlsplit(env['PUBLIC_ORIGIN']).port or 8000)
    try:
        with urllib.request.urlopen(origin + '/api/health', timeout=5) as response:
            data = json.load(response)
        if data.get('status') != 'ok':
            raise OperationError('健康接口未返回正常状态。')
        return data
    except Exception as exc:
        raise OperationError('无法通过健康接口连接应用。请检查运行状态、端口、证书和日志。') from exc


def start(root: Path = ROOT):
    mode = profile(root)
    if mode == 'docker':
        docker('config', '--quiet', root=root)
        docker('up', '-d', '--wait', root=root)
        print('Compose 启动命令已完成。请再执行 health 验证外部入口。')
    elif mode == 'native':
        run(['systemctl', 'start', SERVICE], root=root)
    else:
        runtime = root / '.runtime'
        runtime.mkdir(exist_ok=True)
        record = runtime / 'process.json'
        if record.exists():
            try:
                current = health(root)
                if current.get('instance_id') == json.loads(record.read_text())['instance_id']:
                    print('应用已经运行。'); return
            except OperationError:
                pass
        port = urlsplit(load_env(root / '.env')['PUBLIC_ORIGIN']).port or 8000
        with socket.socket() as sock:
            # Match the server's normal POSIX rebind behavior after TIME_WAIT.
            # Windows requires exclusive binding to avoid taking another listener's port.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE if os.name == 'nt' else socket.SO_REUSEADDR, 1)
            try:
                sock.bind(('127.0.0.1', port))
            except OSError as exc:
                raise OperationError('端口已被占用；未终止任何现有进程。请检查配置。') from exc
        instance = secrets.token_hex(16)
        with (runtime / 'application.log').open('ab') as log:
            kwargs = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
            process = subprocess.Popen([str(python_path(root)), str(root / 'scripts/serve.py'), '--instance', instance], cwd=root,
                                       stdout=log, stderr=subprocess.STDOUT, env=environment(root), **kwargs)
        record.write_text(json.dumps({'pid': process.pid, 'instance_id': instance}), encoding='utf-8')
        for _ in range(30):
            if process.poll() is not None:
                raise OperationError('应用启动失败，请执行 logs 查看日志。')
            try:
                if health(root).get('instance_id') == instance:
                    print('应用已启动：' + load_env(root / '.env')['PUBLIC_ORIGIN']); return
            except OperationError:
                time.sleep(.5)
        raise OperationError('启动等待超时；请执行 status 和 logs 检查，不要重复生成配置。')


def process_exited(pid: int) -> bool:
    """Observe exit without sending a signal to an unverified/reused process."""
    if os.name == 'nt':
        import ctypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only
        if not handle:
            return ctypes.get_last_error() == 87  # ERROR_INVALID_PARAMETER: no such PID
        try:
            kernel.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
            return kernel.WaitForSingleObject(handle, 0) == 0
        finally:
            kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
            kernel.CloseHandle(handle)
    try:
        waited, _ = os.waitpid(pid, os.WNOHANG)
        if waited == pid:
            return True
    except ChildProcessError:
        pass
    stat = Path('/proc') / str(pid) / 'stat'
    if stat.is_file():
        try:
            if stat.read_text().rsplit(')', 1)[1].strip().split()[0] == 'Z':
                return True
        except (OSError, IndexError):
            pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def stop(root: Path = ROOT):
    mode = profile(root)
    if mode == 'docker':
        docker('stop', 'gateway', 'app', root=root)
        print('应用与入口已停止，数据库与数据卷未删除。')
    elif mode == 'native':
        run(['systemctl', 'stop', SERVICE], root=root)
    else:
        path = root / '.runtime/process.json'
        if not path.exists():
            print('没有由此脚本管理的本机进程。'); return
        process = json.loads(path.read_text(encoding='utf-8'))
        try:
            actual = health(root)
        except OperationError:
            # Never kill an unverified PID after a crash or PID reuse.
            try:
                os.kill(process['pid'], 0)
            except OSError:
                path.unlink(missing_ok=True); print('应用未运行。'); return
            raise OperationError('无法确认进程身份，未终止任何进程。请人工检查进程及日志。')
        if actual.get('instance_id') != process['instance_id']:
            raise OperationError('进程身份不匹配，拒绝停止可能属于其他系统的进程。')
        if os.name == 'nt':
            run(['taskkill', '/PID', str(process['pid']), '/T'], root=root)
        else:
            os.kill(process['pid'], signal.SIGTERM)
        for _ in range(120):
            # A closed listening socket alone does not mean outstanding writes have drained.
            if process_exited(process['pid']):
                path.unlink(missing_ok=True); print('应用已停止，数据未删除。'); return
            time.sleep(.25)
        raise OperationError('应用尚未停止；未发送强制终止命令。请检查日志。')


def status(root: Path = ROOT):
    mode = profile(root)
    print('部署方式：' + MODES[mode])
    if mode == 'docker':
        docker('ps', '-a', root=root)
    elif mode == 'native':
        run(['systemctl', '--no-pager', 'status', SERVICE], root=root, check=False)
    try:
        data = health(root)
        print(json.dumps({k: v for k, v in data.items() if k != 'instance_id'}, ensure_ascii=False, indent=2))
    except OperationError as exc:
        print(str(exc))


def logs(root: Path = ROOT, lines=100):
    mode = profile(root)
    if mode == 'docker':
        docker('logs', '--tail', str(lines), 'app', 'migrate', 'scanner', 'gateway', root=root)
    elif mode == 'native':
        run(['journalctl', '-u', SERVICE, '-n', str(lines), '--no-pager'], root=root)
    else:
        file = root / '.runtime/application.log'
        if not file.exists():
            raise OperationError('尚无运行日志。')
        from collections import deque
        with file.open(encoding='utf-8', errors='replace') as f:
            print(''.join(deque(f, maxlen=lines)))


def app_command(command: str, root: Path = ROOT):
    if profile(root) == 'docker':
        docker('exec', *([] if command == 'recover-admin' else ['-T']), 'app', 'python', '-m', 'app.manage', command, root=root)
    else:
        run([python_path(root), '-m', 'app.manage', command], root=root, env=environment(root))


def file_hash(path: Path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def pg_environment(url: str) -> dict[str, str]:
    parsed = urlsplit(url.replace('postgresql+psycopg://', 'postgresql://'))
    env = {**os.environ, 'PGHOST': parsed.hostname or '127.0.0.1', 'PGPORT': str(parsed.port or 5432),
           'PGUSER': unquote(parsed.username or ''), 'PGPASSWORD': unquote(parsed.password or ''),
           'PGDATABASE': unquote(parsed.path.lstrip('/'))}
    from urllib.parse import parse_qs
    query = parse_qs(parsed.query)
    for key in ('sslmode', 'sslrootcert', 'sslcert', 'sslkey', 'connect_timeout'):
        if key in query:
            env['PG' + key.upper()] = query[key][-1]
    return env


def backup(destination: Path, root: Path = ROOT):
    """Pause all app writers, then capture matching DB, immutable objects and secrets."""
    destination = destination.resolve()
    if destination.exists():
        raise OperationError('备份目标已存在，拒绝覆盖。请选择新的目录。')
    mode = profile(root)
    if mode == 'docker':
        running = docker('ps', '--status', 'running', '--services', root=root, capture=True).stdout.splitlines()
        was_running = 'app' in running
        destination.mkdir(parents=True, mode=0o700)
        try:
            docker('stop', 'app', root=root)
            with (destination / 'database.dump').open('wb') as f:
                result = subprocess.run(['docker', 'compose', 'exec', '-T', 'database', 'pg_dump', '-U', 'zc_migrator', '-d', 'clearledger', '-Fc'], cwd=root, stdout=f)
                if result.returncode: raise OperationError('数据库备份失败。')
            with (destination / 'objects.tar').open('wb') as f:
                result = subprocess.run(['docker', 'compose', 'run', '--rm', '-T', '--no-deps', 'app', 'tar', '-C', '/data', '-cf', '-', 'objects'], cwd=root, stdout=f)
                if result.returncode: raise OperationError('原始资源备份失败。')
            shutil.copy2(root / '.env', destination / 'environment.secret')
            names = ['database.dump', 'objects.tar', 'environment.secret']
            (destination / 'SHA256SUMS').write_text(''.join(file_hash(destination/n) + '  ' + n + '\n' for n in names), encoding='utf-8')
        finally:
            if was_running:
                docker('start', 'app', root=root)
        print('备份已写入受控目录。请加密后离线保存：' + str(destination)); return
    env = load_env(root / '.env')
    data = Path(env['DATA_DIR']).resolve()
    if data == destination or data in destination.parents:
        raise OperationError('备份目录不能放在应用数据目录内。')
    was_running = False
    try:
        current = health(root); was_running = True
        if mode == 'local':
            record = root / '.runtime/process.json'
            if not record.is_file() or json.loads(record.read_text()).get('instance_id') != current.get('instance_id'):
                raise OperationError('应用不是由当前管理脚本启动，不能安全暂停备份。请先手工停止该应用。')
    except OperationError:
        if was_running:
            raise
    # Native mode explicitly stops the known systemd service even if the HTTP check fails.
    if was_running or mode == 'native':
        stop(root)
    try:
        destination.mkdir(parents=True, mode=0o700)
        with tempfile.TemporaryDirectory(prefix='bnu-backup-') as tmp:
            staging = Path(tmp)
            if mode == 'local':
                from urllib.parse import unquote
                url = env['DATABASE_URL']
                if not url.startswith('sqlite:///'):
                    raise OperationError('本机备份仅支持 SQLite 配置。')
                database = Path(url[len('sqlite:///'):])
                if not database.is_absolute(): database = root / database
                if not database.is_file():raise OperationError('数据库不存在，请先初始化。')
                with closing(sqlite3.connect(database)) as source, closing(sqlite3.connect(staging / 'database.sqlite')) as target, target:
                    source.backup(target)
            else:
                if not shutil.which('pg_dump'):
                    raise OperationError('未安装 pg_dump。请安装与服务器数据库兼容的 PostgreSQL 客户端。')
                migration = load_env(root / '.env.migration')['DATABASE_URL']
                with (staging / 'database.dump').open('wb') as f:
                    result = subprocess.run(['pg_dump', '-Fc', '--no-owner'], stdout=f, env=pg_environment(migration))
                    if result.returncode:raise OperationError('PostgreSQL 备份未完成。')
                shutil.copy2(root / '.env.migration', staging / 'migration.secret')
            shutil.copy2(root / '.env', staging / 'environment.secret')
            archive = destination / 'system-backup.zip'
            manifest = {'format': 'bnu-system-backup-v1', 'mode': mode, 'created_at': datetime.now(timezone.utc).isoformat(), 'files': {}}
            with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
                files = [(x, x.name) for x in staging.iterdir() if x.is_file()]
                if (data / 'objects').exists():
                    files += [(x, 'objects/' + x.relative_to(data / 'objects').as_posix()) for x in (data / 'objects').rglob('*') if x.is_file()]
                for path, name in files:
                    if path.is_symlink():raise OperationError('数据目录中存在符号链接，已停止备份。')
                    manifest['files'][name] = {'size': path.stat().st_size, 'sha256': file_hash(path)}
                    z.write(path, name)
                z.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
            os.chmod(archive, 0o600)
            (destination / 'SHA256SUMS').write_text(file_hash(archive) + '  system-backup.zip\n', encoding='utf-8')
        print('备份完成：' + str(archive) + '\n含学生数据及密钥，必须加密后离线保管。')
    finally:
        if was_running:
            start(root)


def verify_docker_backup(directory: Path):
    """Validate a Docker backup before a privileged restore command reads it."""
    names = {'database.dump', 'objects.tar', 'environment.secret'}
    if directory.is_symlink() or not directory.is_dir():
        raise OperationError('Docker 备份必须为普通目录。')
    if {p.name for p in directory.iterdir()} != names | {'SHA256SUMS'}:
        raise OperationError('Docker 备份成员不完整或包含未知文件。')
    if any(p.is_symlink() or not p.is_file() for p in directory.iterdir()):
        raise OperationError('Docker 备份成员不得为链接或子目录。')
    declared = {}
    for line in (directory / 'SHA256SUMS').read_text().splitlines():
        match = re.fullmatch(r'([a-f0-9]{64})  (database.dump|objects.tar|environment.secret)', line)
        if not match or match[2] in declared:
            raise OperationError('Docker 备份校验清单无效。')
        declared[match[2]] = match[1]
    if set(declared) != names:
        raise OperationError('Docker 备份校验清单不完整。')
    for name, digest in declared.items():
        if file_hash(directory / name) != digest:
            raise OperationError('Docker 备份校验失败：' + name)
    seen = set(); count = 0
    with tarfile.open(directory / 'objects.tar', 'r:*') as tar:
        for item in tar:
            name = item.name.rstrip('/')
            if name in seen:
                raise OperationError('原始资源备份中存在重复成员。')
            seen.add(name)
            if item.isdir() and re.fullmatch(r'objects(?:/[a-f0-9]{2})?', name):
                continue
            if not item.isfile() or not re.fullmatch(r'objects/[a-f0-9]{2}/[a-f0-9]{64}', name):
                raise OperationError('原始资源备份存在不安全路径、链接或非普通文件。')
            if name.split('/')[1] != name.split('/')[2][:2]:
                raise OperationError('原始资源目录与文件标识不匹配。')
            stream = tar.extractfile(item)
            if stream is None or hashlib.file_digest(stream, 'sha256').hexdigest() != name.rsplit('/', 1)[1]:
                raise OperationError('原始资源内容与文件标识不匹配。')
            count += 1
    return {'format': 'docker-directory-backup', 'resources': count, 'files': sorted(names)}


def verify_backup(archive: Path):
    if archive.is_dir():
        return verify_docker_backup(archive)
    """Verify before extraction. No zip-slip, links, duplicates or unsigned extra paths."""
    with zipfile.ZipFile(archive) as z:
        info = z.infolist()
        if len(info) > 100000:raise OperationError('备份文件数量异常。')
        names = [x.filename for x in info]
        if len(names) != len(set(names)):raise OperationError('备份含重复路径。')
        for item in info:
            path = PurePosixPath(item.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in item.filename or ':' in item.filename or (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise OperationError('备份路径不安全。')
            if item.flag_bits & 1:raise OperationError('请先在受控环境解密备份。')
        if 'manifest.json' not in names or z.getinfo('manifest.json').file_size > 32 * 1024 * 1024:
            raise OperationError('备份清单缺失或过大。')
        manifest = json.loads(z.read('manifest.json'))
        if not isinstance(manifest,dict) or manifest.get('format') != 'bnu-system-backup-v1':raise OperationError('不支持此备份格式。')
        files = manifest.get('files')
        if manifest.get('mode') not in ('local','native') or not isinstance(files,dict):raise OperationError('备份模式或成员清单无效。')
        required = {'environment.secret', 'database.sqlite'} if manifest['mode']=='local' else {'environment.secret','migration.secret','database.dump'}
        if not required.issubset(files):raise OperationError('备份缺少必要数据库或配置文件。')
        for name, item in files.items():
            if not isinstance(name,str) or not isinstance(item,dict) or type(item.get('size')) is not int or item['size']<0 or not re.fullmatch(r'[a-f0-9]{64}',str(item.get('sha256',''))):
                raise OperationError('备份成员大小或哈希字段无效。')
        if set(names) != set(manifest['files']) | {'manifest.json'}:raise OperationError('备份成员与清单不一致。')
        for name, expected in manifest['files'].items():
            item = z.getinfo(name)
            if item.file_size != expected['size']:raise OperationError('备份文件大小不匹配。')
            with z.open(name) as f:
                digest = hashlib.file_digest(f, 'sha256').hexdigest()
            if digest != expected['sha256']:raise OperationError('备份校验失败：' + name)
        return manifest


def restore(archive: Path, root: Path = ROOT):
    if (root/'.env').is_symlink() or (root/'var').is_symlink():
        raise OperationError('目标配置或数据目录不得为符号链接。')
    if (root / '.env').exists() or ((root / 'var').exists() and any((root / 'var').iterdir())):
        raise OperationError('恢复仅允许在没有 .env、没有数据的全新代码目录进行。不会覆盖现有系统。')
    manifest = verify_backup(archive)
    if manifest['mode'] != 'local':
        raise OperationError('原生 PostgreSQL 备份请按部署说明在隔离数据库中恢复；Docker 备份使用 deploy/restore.sh。')
    if not confirm('将把备份恢复到当前全新目录。恢复后需要重新安装本机依赖。', '恢复到空目录'):
        print('已取消恢复。'); return
    allowed = {'database.sqlite', 'environment.secret'}
    for name in manifest['files']:
        if name not in allowed and not re.fullmatch(r'objects/[a-f0-9]{2}/[a-f0-9]{64}', name):
            raise OperationError('本机备份包含非预期文件，拒绝恢复。')
    with tempfile.TemporaryDirectory(dir=root, prefix='.restore-') as tmp:
        staging = Path(tmp)
        with zipfile.ZipFile(archive) as z:
            for name in manifest['files']:
                target = staging / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(name) as src, target.open('xb') as dst:
                    shutil.copyfileobj(src, dst)
        env = load_env(staging / 'environment.secret')
        env['DATA_DIR'] = str(root / 'var'); env['DATABASE_URL'] = 'sqlite:///' + str(root / 'var/ledger.db')
        env['ENVIRONMENT'] = 'development'; env['CLAMAV_HOST'] = ''
        data = root / 'var'; data.mkdir(exist_ok=True)
        shutil.move(str(staging / 'database.sqlite'), data / 'ledger.db')
        if (staging / 'objects').exists():shutil.move(str(staging / 'objects'), data / 'objects')
        write_secret(root / '.env', env)
        (root / '.deployment.json').write_text('{"mode":"local"}\n')
    print('备份已恢复。下一步：install → verify → start。未自动启动，未修改旧部署。')


def native_files(root: Path = ROOT, user='bnu-evaluation'):
    if profile(root) != 'native':raise OperationError('需要先配置 native 部署方式。')
    if os.name != 'posix' or not re.fullmatch(r'[a-z_][a-z0-9_-]{0,30}', user):
        raise OperationError('原生部署需要 Linux 与有效的专用服务账号。')
    if not re.fullmatch(r'/[A-Za-z0-9_./-]+', str(root)):
        raise OperationError('systemd 安装路径请使用不含空格和非英文字符的绝对路径，例如 /opt/bnu-evaluation。')
    env = load_env(root / '.env'); domain = env['DOMAIN']; out = root / 'deploy/generated'; out.mkdir(parents=True, exist_ok=True)
    unit = f'''[Unit]
Description=Student comprehensive evaluation management system
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={user}
Group={user}
WorkingDirectory={root}
EnvironmentFile={root}/.env
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart={root}/.venv/bin/python {root}/scripts/serve.py --native
Restart=on-failure
RestartSec=5
TimeoutStopSec=60
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths={env['DATA_DIR']}
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
'''
    caddy = f'''{domain} {{
    request_body {{
        max_size 520MB
    }}
    reverse_proxy 127.0.0.1:8000
}}
'''
    nginx = f'''# 证书由学校现有设施提供；替换下方文件路径后使用。
server {{
    listen 443 ssl;
    server_name {domain};
    ssl_certificate /etc/ssl/bnu-evaluation/fullchain.pem;
    ssl_certificate_key /etc/ssl/bnu-evaluation/privkey.pem;
    client_max_body_size 520m;
    location / {{
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_read_timeout 300s;
    }}
}}
server {{ listen 80; server_name {domain}; return 301 https://$host$request_uri; }}
'''
    for name, text in [('bnu-evaluation.service', unit), ('Caddyfile', caddy), ('nginx.conf', nginx)]:
        (out / name).write_text(text, encoding='utf-8')
    print('配置已生成于 deploy/generated。尚未安装或修改系统服务。')
    print('请按部署说明创建服务用户、初始化数据库、设置目录权限，再安装此 service 文件。')


def check(root: Path = ROOT):
    print(SYSTEM_NAME + ' — 环境检查')
    print('Python：' + sys.version.split()[0]); print('代码目录：' + str(root))
    print('Docker：' + ('已找到' if shutil.which('docker') else '未找到'))
    print('systemctl：' + ('已找到' if shutil.which('systemctl') else '未找到'))
    if not (root / '.env').exists():
        print('未配置。请先执行 configure。'); return
    mode = profile(root); env = load_env(root / '.env')
    print('部署方式：' + MODES[mode]); print('访问地址：' + env.get('PUBLIC_ORIGIN', '未配置'))
    print('APP_SECRET：' + ('已配置' if len(env.get('APP_SECRET', '')) >= 32 else '缺失或过短'))
    print('管理员动态口令：' + ('已配置' if env.get('ADMIN_TOTP_SECRET') else '未配置（仅本机模式允许）'))
    print('文件扫描：' + ('已配置；仍需检查服务' if env.get('CLAMAV_HOST') else '未配置（仅本机模式允许）'))
    if mode == 'docker':
        docker('config', '--quiet', root=root)
    elif mode == 'native':
        for tool in ('pg_dump', 'pg_restore', 'systemctl'):
            print(tool + '：' + ('已找到' if shutil.which(tool) else '未找到'))
    print('本检查不替代网络、证书、病毒扫描、并发容量和恢复验收。')


def menu():
    commands = [('环境检查', 'check'), ('首次生成配置', 'configure'), ('安装依赖 / 构建镜像', 'install'),
                ('初始化数据库', 'init-db'), ('启动', 'start'), ('查看状态', 'status'), ('查看日志', 'logs'),
                ('停止', 'stop'), ('重启', 'restart'), ('完整备份', 'backup'), ('校验备份', 'verify-backup'),
                ('在空目录恢复本机备份', 'restore'), ('校验原件与审计', 'verify'), ('恢复唯一管理员密码', 'recover-admin'),
                ('生成 Linux 服务配置', 'native-files'), ('清理过期会话', 'cleanup-sessions')]
    while True:
        print('\n' + SYSTEM_NAME + '\n部署与管理\n')
        for index, (label, _) in enumerate(commands, 1):print(f'{index:2d}. {label}')
        print(' 0. 退出')
        choice = input('请输入数字，然后按 Enter：').strip()
        if choice == '0':return 0
        if not choice.isdigit() or not 1 <= int(choice) <= len(commands):
            print('输入无效。'); continue
        command = commands[int(choice) - 1][1]
        args = [command]
        if command == 'configure':
            print('1. 本机运行\n2. Docker 服务器\n3. Linux 原生服务器')
            mode = {'1': 'local', '2': 'docker', '3': 'native'}.get(input('请选择部署方式：').strip())
            if not mode:print('已取消。');continue
            args += ['--mode', mode]
            if mode != 'local':args += ['--domain', input('实际访问域名（不含 https://）：').strip()]
        elif command in ('backup', 'restore', 'verify-backup'):
            args.append(input('备份目录路径：' if command == 'backup' else '备份ZIP或Docker备份目录完整路径：').strip().strip('"'))
        try:
            main(args)
        except (KeyboardInterrupt, EOFError):
            print('\n操作已取消。')
        input('\n按 Enter 返回菜单。')


def main(argv=None):
    p = argparse.ArgumentParser(description=SYSTEM_NAME + '：不带参数打开交互菜单。')
    sub = p.add_subparsers(dest='command')
    cfg = sub.add_parser('configure', help='首次生成配置，不覆盖既有密钥')
    cfg.add_argument('--mode', choices=MODES, default='local');cfg.add_argument('--domain', default='');cfg.add_argument('--port', type=int, default=8000)
    ins = sub.add_parser('install', help='安装依赖或构建 Docker 镜像');ins.add_argument('--wheelhouse')
    for command in ('init-db', 'start', 'stop', 'restart', 'status', 'health', 'check', 'verify', 'recover-admin', 'cleanup-sessions'):
        sub.add_parser(command)
    log = sub.add_parser('logs');log.add_argument('--lines', type=int, default=100)
    for command in ('backup', 'restore', 'verify-backup'):
        parser = sub.add_parser(command);parser.add_argument('path', type=Path)
    nf = sub.add_parser('native-files');nf.add_argument('--user', default='bnu-evaluation')
    args = p.parse_args(argv)
    try:
        if not args.command:return menu()
        if args.command == 'configure':
            runtime = migration = ''
            if args.mode == 'native':
                runtime = getpass.getpass('运行账号连接串（postgresql+psycopg://zc_app:密码@主机:5432/数据库）：')
                migration = getpass.getpass('迁移账号连接串（隐藏输入）：')
            configure(args.mode, args.domain, args.port, runtime, migration)
        elif args.command == 'install':install(wheelhouse=args.wheelhouse)
        elif args.command == 'init-db':init_db()
        elif args.command == 'start':start()
        elif args.command == 'stop':stop()
        elif args.command == 'restart':stop();start()
        elif args.command == 'status':status()
        elif args.command == 'health':print(json.dumps(health(external=True), ensure_ascii=False, indent=2))
        elif args.command == 'logs':logs(lines=max(1, min(args.lines, 10000)))
        elif args.command == 'check':check()
        elif args.command == 'backup':
            if confirm('备份将短暂停止应用写入；备份结束后恢复原运行状态。'):backup(args.path)
            else:print('已取消。')
        elif args.command == 'verify-backup':print(json.dumps({k:v for k,v in verify_backup(args.path).items() if k!='files'}, ensure_ascii=False, indent=2));print('备份成员校验通过。')
        elif args.command == 'restore':restore(args.path)
        elif args.command == 'native-files':native_files(user=args.user)
        else:app_command(args.command)
        return 0
    except (OperationError, OSError, ValueError, zipfile.BadZipFile, tarfile.TarError) as exc:
        print('操作未完成：' + str(exc), file=sys.stderr);return 1
    except (KeyboardInterrupt, EOFError):
        print('\n操作已取消。', file=sys.stderr);return 130

if __name__ == '__main__':
    raise SystemExit(main())
