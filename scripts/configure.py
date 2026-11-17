#!/usr/bin/env python3
"""Generate local or Docker environment without hard-coded passwords."""
import argparse,base64,os,re,secrets
from pathlib import Path
p=argparse.ArgumentParser(description='生成北京师范大学学生综合测评管理系统部署配置；绝不覆盖已有密钥。')
p.add_argument('--production',action='store_true');p.add_argument('--domain',default='');p.add_argument('--output',default='.env');a=p.parse_args()
path=Path(a.output)
if path.exists():p.error(f'{path} 已存在。请人工编辑，不要意外更换已有系统的密钥。')
if a.production and not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?',a.domain):p.error('生产模式须用 --domain 指定不含路径、端口的域名。')
secret=secrets.token_urlsafe(48);password=secrets.token_urlsafe(24);suffix=secrets.token_hex(12);totp=base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')
values={'APP_SECRET':secret,'BOOTSTRAP_ADMIN_PASSWORD':password,'ADMIN_PATH':'/ops-'+suffix,'ENVIRONMENT':'production' if a.production else 'development','PUBLIC_ORIGIN':'https://'+a.domain if a.production else 'http://localhost:8000','ADMIN_TOTP_SECRET':totp if a.production else '', 'CLAMAV_HOST':'scanner' if a.production else ''}
if a.production:values.update(DOMAIN=a.domain,PG_ADMIN_PASSWORD=secrets.token_hex(24),APP_DB_PASSWORD=secrets.token_hex(24))
else:values.update(DATA_DIR='./var',DATABASE_URL='sqlite:///./var/ledger.db')
fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as f:
 f.write('# 此文件包含密钥，请勿提交代码仓库、反馈系统或聊天群。\n')
 for k,v in values.items():f.write(k+'='+v+'\n')
print(f'已创建 {path}（权限0600）。管理员账号：admin。初始密码、独立入口和TOTP密钥只在此文件中。')
if a.production:print('将 ADMIN_TOTP_SECRET 手工添加到认证器（SHA1 / 6位 / 30秒），并离线备份。首次登录必须改密码。')
else:print('本机：python scripts/run_local.py。此模式不用作对外生产服务。')
