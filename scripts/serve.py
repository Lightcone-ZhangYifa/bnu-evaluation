#!/usr/bin/env python3
"""Local and systemd entry point. Production never binds directly to a public interface."""
from pathlib import Path
import argparse, os, sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
from scripts.console import load_env
p = argparse.ArgumentParser(description='应用进程入口，由管理脚本或 systemd 调用。')
p.add_argument('--instance', default='')
p.add_argument('--native', action='store_true')
a = p.parse_args()
# systemd reads EnvironmentFile as the service manager; the unprivileged
# application user need not be able to read the root-owned secret file.
if not all(os.environ.get(k) for k in ('APP_SECRET', 'PUBLIC_ORIGIN', 'DATABASE_URL')):
    for k, v in load_env(ROOT / '.env').items():
        os.environ.setdefault(k, v)
if a.instance:
    os.environ['INSTANCE_ID'] = a.instance
from urllib.parse import urlsplit
origin = urlsplit(os.environ['PUBLIC_ORIGIN'])
if os.environ.get('ENVIRONMENT') == 'production' and not a.native:
    p.error('生产配置只能使用 --native 或 Docker Compose 启动。')
if a.native and os.environ.get('ENVIRONMENT') != 'production':
    p.error('原生服务器模式需要 production 配置。')
import uvicorn
uvicorn.run('app.main:create_app', factory=True, host='127.0.0.1',
            port=8000 if a.native else (origin.port or 8000), workers=2 if a.native else 1,
            proxy_headers=a.native, forwarded_allow_ips='127.0.0.1',
            limit_concurrency=100, timeout_keep_alive=10)
