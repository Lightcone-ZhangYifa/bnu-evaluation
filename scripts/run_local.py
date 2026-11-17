#!/usr/bin/env python3
"""A loopback-only development launcher, using the generated .env."""
from pathlib import Path
import os,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
root=Path(__file__).resolve().parents[1];os.chdir(root)
if not (root/'.env').exists():raise SystemExit('请先运行 python scripts/configure.py')
for line in (root/'.env').read_text().splitlines():
 if line and not line.startswith('#'):
  k,v=line.split('=',1);os.environ.setdefault(k,v)
if os.environ.get('ENVIRONMENT')=='production':raise SystemExit('生产模式请使用 python manage.py start，或按部署说明安装 systemd 服务。')
Path(os.environ.get('DATA_DIR','./var')).mkdir(parents=True,exist_ok=True)
import uvicorn
uvicorn.run('app.main:create_app',factory=True,host='127.0.0.1',port=8000)
