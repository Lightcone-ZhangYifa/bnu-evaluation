"""Offline operations. Never expose these commands through an HTTP route."""
from __future__ import annotations
import argparse, hashlib, hmac, json, os, sys
from sqlalchemy import select, text, delete
from .config import Settings
from .db import setup_database, Account, ADMIN_ID, Audit, LoginSession, AgentToken, Resource
from .security import bootstrap, canonical, password_hash, audit
from .storage import Store


def main():
    parser=argparse.ArgumentParser(description="北京师范大学学生综合测评管理系统离线运维工具")
    parser.add_argument('command',choices=['init-db','verify','recover-admin','cleanup-sessions'])
    parser.add_argument('--grant-runtime',action='store_true',help='初始化后向预先创建的 zc_app 角色授予最小运行权限')
    args=parser.parse_args(); settings=Settings.from_env()
    engine,factory=setup_database(settings,initialize=args.command=='init-db')
    try:
        if args.command=='init-db':
            # A session lock covers the independent bootstrap transaction as well.
            with engine.connect() as lock:
                if engine.dialect.name=='postgresql':lock.execute(text('SELECT pg_advisory_lock(7400231902)'))
                try:bootstrap(factory,settings,allow_create=True)
                finally:
                    if engine.dialect.name=='postgresql':lock.execute(text('SELECT pg_advisory_unlock(7400231902)'))
            if args.grant_runtime:
                if engine.dialect.name!='postgresql':raise ValueError('仅 PostgreSQL 支持角色授权。')
                with engine.begin() as c:
                    c.execute(text('GRANT USAGE ON SCHEMA public TO zc_app'))
                    c.execute(text('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO zc_app'))
                    c.execute(text('REVOKE UPDATE, DELETE ON audits, public_versions, resources FROM zc_app'))
                    c.execute(text('REVOKE INSERT, UPDATE, DELETE ON schema_version FROM zc_app'))
            print('数据库 v1 与唯一管理员已就绪。已有数据不会被清空。')
        elif args.command=='verify':
            store=Store(settings); bad=[]; count=0; resources=0
            with factory() as db:
                for row in db.scalars(select(Audit)).yield_per(500):
                    payload={k:getattr(row,k) for k in ('id','actor_id','actor_kind','target','action','details','created_at')}
                    expected=hmac.new(settings.secret.encode(),canonical(payload).encode(),hashlib.sha256).hexdigest()
                    if not hmac.compare_digest(expected,row.signature):bad.append('audit:'+row.id)
                    count+=1
                for row in db.scalars(select(Resource)).yield_per(500):
                    try:
                        p=store.path(row.sha256)
                        with p.open('rb') as f:h=hashlib.file_digest(f,'sha256').hexdigest()
                        if h!=row.sha256 or p.stat().st_size!=row.size:bad.append('resource:'+row.sha256)
                    except OSError:bad.append('missing:'+row.sha256)
                    resources+=1
            print(json.dumps({'audit_records':count,'resources':resources,'errors':bad},ensure_ascii=False,indent=2))
            if bad:return 2
        elif args.command=='recover-admin':
            # Requires host access, existing APP_SECRET, and explicit operator input.
            import getpass
            password=getpass.getpass('新的管理员密码（12—128 字符）：')
            if password!=getpass.getpass('再次输入：'):raise ValueError('两次密码不一致。')
            with factory.begin() as db:
                user=db.get(Account,ADMIN_ID)
                if not user:raise ValueError('管理员不存在；先检查恢复的数据库。')
                user.password_hash=password_hash(password);user.must_change=True;user.totp_step=-1
                db.execute(delete(LoginSession).where(LoginSession.account_id==ADMIN_ID))
                audit(db,settings,None,ADMIN_ID,'admin.offline_recovery',{})
            print('已恢复同一个管理员，未创建第二个管理员。请按新密码和当前 TOTP 登录。')
        else:
            from .db import now, LoginAttempt, UploadGrant
            from datetime import datetime,timezone,timedelta
            with factory.begin() as db:
                removed=db.execute(delete(LoginSession).where(LoginSession.expires_at<now())).rowcount
                db.execute(delete(UploadGrant).where(UploadGrant.expires_at<now()))
                cutoff=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat(timespec='microseconds')
                db.execute(delete(LoginAttempt).where(LoginAttempt.created_at<cutoff))
            print(f'已清理 {removed} 个过期会话；不删除证据、审计或项目。')
    finally:engine.dispose()
    return 0

if __name__=='__main__':
    try:sys.exit(main())
    except Exception as exc:
        print(f'操作未完成：{exc}',file=sys.stderr);sys.exit(1)
