from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from fastapi import Request, HTTPException
from sqlalchemy import select, func, delete, update
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError
import secrets, hashlib, hmac, json, base64, struct, time
from .db import (Account, LoginSession, LoginAttempt, AgentToken, Audit, ADMIN_ID, now, uid)

ph = PasswordHasher(time_cost=3,memory_cost=65536,parallelism=2)
DUMMY_HASH = ph.hash("not-a-real-user-"+secrets.token_urlsafe(24))

def digest(value: str): return hashlib.sha256(value.encode()).hexdigest()
def canonical(value): return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False)
def later(hours=8): return (datetime.now(timezone.utc)+timedelta(hours=hours)).isoformat(timespec="microseconds")
def password_hash(password):
    if not 12 <= len(password) <= 128: raise ValueError("密码必须为12—128字符。")
    return ph.hash(password)
def check_password(encoded,password):
    try: return ph.verify(encoded,password)
    except (VerifyMismatchError, VerificationError, InvalidHashError): return False

def totp_step(secret,code,previous=-1):
    if not code.isdigit() or len(code)!=6: return None
    try: key=base64.b32decode(secret.upper()+"="*((8-len(secret)%8)%8))
    except Exception: return None
    current=int(time.time()//30)
    for step in (current-1,current,current+1):
        if step<=previous: continue
        h=hmac.new(key,struct.pack(">Q",step),hashlib.sha1).digest()
        offset=h[-1]&15
        expected=str((struct.unpack(">I",h[offset:offset+4])[0]&0x7fffffff)%1000000).zfill(6)
        if hmac.compare_digest(code,expected): return step
    return None

@dataclass
class Principal:
    user: Account
    csrf: str | None = None
    session_hash: str | None = None
    token: AgentToken | None = None
    @property
    def kind(self): return "agent" if self.token else self.user.role
    def require_human(self):
        if self.token: raise HTTPException(403,"此操作只允许人工账户。")
    def require_teacher(self):
        self.require_human()
        if self.user.role != "teacher": raise HTTPException(403,"仅教师可操作。")
    def require_admin(self):
        self.require_human()
        if self.user.role != "admin": raise HTTPException(403,"仅运维管理员可操作。")
    def scope(self,project_id,scope):
        if self.token and (self.token.project_id!=project_id or scope not in self.token.scopes):
            raise HTTPException(403,"服务令牌不具备此工程/操作权限。")


def authenticate(request: Request, db, allow_password_change=False) -> Principal:
    auth=request.headers.get("authorization","")
    if auth:
        if not auth.startswith("Bearer "): raise HTTPException(401,"认证方式错误。")
        token=db.scalar(select(AgentToken).where(AgentToken.token_hash==digest(auth[7:])))
        if not token or token.revoked or token.expires_at<=now(): raise HTTPException(401,"服务令牌无效或已过期。")
        user=db.get(Account,token.owner_id)
        if not user or user.disabled or user.role!="teacher": raise HTTPException(401,"服务令牌已失效。")
        return Principal(user,token=token)
    cookie=request.cookies.get("zc_session")
    session=db.get(LoginSession,digest(cookie)) if cookie else None
    if not session or session.expires_at<=now(): raise HTTPException(401,"登录已过期，请重新登录。")
    user=db.get(Account,session.account_id)
    if not user or user.disabled: raise HTTPException(401,"账户不可用。")
    if request.method not in ("GET","HEAD","OPTIONS"):
        token=request.headers.get("x-csrf-token","")
        if not hmac.compare_digest(token,session.csrf): raise HTTPException(403,"安全令牌失效，请刷新页面。")
        origin=request.headers.get("origin")
        if origin and origin!=request.app.state.settings.origin: raise HTTPException(403,"请求来源不可信。")
    if user.must_change and not allow_password_change:
        raise HTTPException(428,"首次登录请先修改初始密码。")
    return Principal(user,session.csrf,session.token_hash)

def audit(db,settings,p: Principal | None,target,action,details):
    timestamp=now(); aid=uid()
    payload={"id":aid,"actor_id":p.user.id if p else ADMIN_ID,"actor_kind":p.kind if p else "system",
        "target":target,"action":action,"details":details,"created_at":timestamp}
    signature=hmac.new(settings.secret.encode(),canonical(payload).encode(),hashlib.sha256).hexdigest()
    db.add(Audit(**payload,signature=signature))

def bootstrap(factory,settings,allow_create=False):
    with factory.begin() as db:
        if not db.get(Account,ADMIN_ID):
            if settings.production and not allow_create:
                raise RuntimeError("请先运行 python -m app.manage init-db 创建唯一管理员。")
            if len(settings.bootstrap_password)<12:
                raise RuntimeError("首次启动须设置至少12字符 BOOTSTRAP_ADMIN_PASSWORD。")
            db.add(Account(id=ADMIN_ID,username="admin",name="系统管理员",role="admin",password_hash=password_hash(settings.bootstrap_password),must_change=True))
            audit(db,settings,None,"system","bootstrap",{"schema":1})

def login(request,db,payload,admin_route=False):
    settings=request.app.state.settings
    if (payload.portal=="admin") != admin_route: raise HTTPException(403,"请使用相应身份的专用入口。")
    origin=request.headers.get("origin")
    if origin and origin!=settings.origin: raise HTTPException(403,"请求来源不可信。")
    ip=request.client.host if request.client else "unknown"
    key=hmac.new(settings.secret.encode(),f"{ip}|{payload.username}".encode(),hashlib.sha256).hexdigest()
    ipkey=hmac.new(settings.secret.encode(),f"IP|{ip}".encode(),hashlib.sha256).hexdigest()
    cutoff=(datetime.now(timezone.utc)-timedelta(minutes=15)).isoformat(timespec="microseconds")
    count=db.scalar(select(func.count()).select_from(LoginAttempt).where(LoginAttempt.key==key,LoginAttempt.created_at>=cutoff))
    ipcount=db.scalar(select(func.count()).select_from(LoginAttempt).where(LoginAttempt.key==ipkey,LoginAttempt.created_at>=cutoff))
    if count>=10 or ipcount>=150: raise HTTPException(429,"登录尝试过多，请15分钟后重试。")
    user=db.scalar(select(Account).where(Account.username==payload.username))
    valid=check_password(user.password_hash if user else DUMMY_HASH,payload.password)
    valid=valid and user and not user.disabled and user.role==payload.portal
    new_step=None
    if valid and user.role=="admin" and settings.admin_totp:
        new_step=totp_step(settings.admin_totp,payload.totp,user.totp_step)
        valid=new_step is not None
    if not valid:
        db.add_all([LoginAttempt(key=key),LoginAttempt(key=ipkey)]); db.commit()
        raise HTTPException(401,"账号、密码、身份或动态验证码不正确。")
    if new_step is not None:
        changed=db.execute(update(Account).where(Account.id==user.id,Account.totp_step<new_step).values(totp_step=new_step)).rowcount
        if not changed: raise HTTPException(401,"动态验证码已使用。")
    db.execute(delete(LoginAttempt).where(LoginAttempt.key==key))
    raw=secrets.token_urlsafe(40); csrf=secrets.token_urlsafe(32)
    db.add(LoginSession(token_hash=digest(raw),account_id=user.id,csrf=csrf,expires_at=later()))
    audit(db,settings,Principal(user),user.id,"login",{"portal":payload.portal})
    db.commit()
    return user,raw,csrf
