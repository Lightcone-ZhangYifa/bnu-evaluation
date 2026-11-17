"""Relational identities and immutable versions; one atomic aggregate per class.

Optimistic CAS on Project.revision prevents lost updates under either database.
Every mutation and its audit event commit in the same database transaction.
"""
from sqlalchemy import (create_engine, String, Integer, Boolean, JSON, Text,
    ForeignKey, CheckConstraint, UniqueConstraint, event, select)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from datetime import datetime, timezone
import uuid

ADMIN_ID = "00000000-0000-0000-0000-000000000001"
def uid(): return str(uuid.uuid4())
def now(): return datetime.now(timezone.utc).isoformat(timespec="microseconds")

class Base(DeclarativeBase): pass

class College(Base):
    __tablename__ = "colleges"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    created_at: Mapped[str] = mapped_column(String(40), default=now)

class Student(Base):
    __tablename__ = "students"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    college_id: Mapped[str] = mapped_column(ForeignKey("colleges.id"), index=True)
    number: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(100))
    class_name: Mapped[str] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (UniqueConstraint("college_id", "number"),)

class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    username: Mapped[str] = mapped_column(String(160), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(16))
    college_id: Mapped[str | None] = mapped_column(ForeignKey("colleges.id"), nullable=True)
    student_id: Mapped[str | None] = mapped_column(ForeignKey("students.id"), nullable=True, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    must_change: Mapped[bool] = mapped_column(Boolean, default=True)
    totp_step: Mapped[int] = mapped_column(Integer, default=-1)
    created_at: Mapped[str] = mapped_column(String(40), default=now)
    __table_args__ = (
        CheckConstraint("role IN ('admin','teacher','student')"),
        CheckConstraint(f"(role = 'admin' AND id = '{ADMIN_ID}') OR (role <> 'admin' AND id <> '{ADMIN_ID}')", name="one_fixed_admin"),
        CheckConstraint("(role = 'student' AND student_id IS NOT NULL AND college_id IS NOT NULL) OR (role = 'teacher' AND student_id IS NULL AND college_id IS NOT NULL) OR (role = 'admin' AND student_id IS NULL AND college_id IS NULL)", name="account_identity"),
        CheckConstraint("role <> 'admin' OR disabled = false", name="admin_cannot_disable"),
    )

class LoginSession(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    csrf: Mapped[str] = mapped_column(String(80))
    expires_at: Mapped[str] = mapped_column(String(40), index=True)
    created_at: Mapped[str] = mapped_column(String(40), default=now)

class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    key: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[str] = mapped_column(String(40), default=now, index=True)

class Folder(Base):
    __tablename__ = "folders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("folders.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(100))
    trashed: Mapped[bool] = mapped_column(Boolean, default=False)

class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    college_id: Mapped[str] = mapped_column(ForeignKey("colleges.id"), index=True)
    folder_id: Mapped[str | None] = mapped_column(ForeignKey("folders.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    trashed: Mapped[bool] = mapped_column(Boolean, default=False)
    body: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(40), default=now)
    updated_at: Mapped[str] = mapped_column(String(40), default=now)

class Resource(Base):
    __tablename__ = "resources"
    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    size: Mapped[int] = mapped_column(Integer)
    media_type: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[str] = mapped_column(String(40), default=now)

class PublicList(Base):
    __tablename__ = "public_lists"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    college_id: Mapped[str] = mapped_column(ForeignKey("colleges.id"), index=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    name: Mapped[str] = mapped_column(String(200))
    revision: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(40), default=now)

class PublicVersion(Base):
    __tablename__ = "public_versions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    list_id: Mapped[str] = mapped_column(ForeignKey("public_lists.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    body: Mapped[dict] = mapped_column(JSON)
    actor_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    created_at: Mapped[str] = mapped_column(String(40), default=now)
    __table_args__ = (UniqueConstraint("list_id", "revision"),)

class AgentToken(Base):
    __tablename__ = "agent_tokens"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    scopes: Mapped[list] = mapped_column(JSON)
    expires_at: Mapped[str] = mapped_column(String(40))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String(40), default=now)

class Feedback(Base):
    __tablename__ = "feedback"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    attachments: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="new")
    created_at: Mapped[str] = mapped_column(String(40), default=now)

class Audit(Base):
    __tablename__ = "audits"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    actor_id: Mapped[str] = mapped_column(String(36), index=True)
    actor_kind: Mapped[str] = mapped_column(String(30))
    target: Mapped[str] = mapped_column(String(100), index=True)
    action: Mapped[str] = mapped_column(String(100))
    details: Mapped[dict] = mapped_column(JSON)
    signature: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(40), default=now, index=True)

class SchemaVersion(Base):
    __tablename__ = "schema_version"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int] = mapped_column(Integer)

IMMUTABLE_TABLES = ("audits", "public_versions", "resources")
SCHEMA_VERSION = 1

def setup_database(settings, initialize=None):
    """Runtime workers only check the schema in production; never execute DDL.

    Run ``python -m app.manage init-db`` once with the migration role before
    starting workers. PostgreSQL advisory locking serializes initialization.
    SQLite is the local development/test path, not a clustered deployment.
    """
    initialize = (not settings.production) if initialize is None else initialize
    kwargs = {"pool_pre_ping": True}
    if settings.database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(settings.database_url, **kwargs)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def pragmas(conn, _):
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    if initialize:
        with engine.begin() as c:
            if engine.dialect.name == "postgresql":
                c.exec_driver_sql("SELECT pg_advisory_xact_lock(7400231901)")
            Base.metadata.create_all(c)
            version = c.execute(select(SchemaVersion.version).where(SchemaVersion.id == 1)).scalar()
            if version is not None and version != SCHEMA_VERSION:
                raise RuntimeError("数据库版本不兼容；禁止自动覆盖。先备份并使用对应版本迁移。")
            if version is None:
                c.execute(SchemaVersion.__table__.insert().values(id=1, version=SCHEMA_VERSION))
            if engine.dialect.name == "sqlite":
                for table in IMMUTABLE_TABLES:
                    for verb in ("UPDATE", "DELETE"):
                        c.exec_driver_sql(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{verb} BEFORE {verb} ON {table} BEGIN SELECT RAISE(ABORT, 'immutable history'); END")
                c.exec_driver_sql(f"CREATE TRIGGER IF NOT EXISTS keep_admin BEFORE DELETE ON accounts WHEN OLD.id = '{ADMIN_ID}' BEGIN SELECT RAISE(ABORT, 'permanent admin'); END")
            else:
                c.exec_driver_sql("CREATE OR REPLACE FUNCTION reject_history_change() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'immutable history'; END $$")
                for table in IMMUTABLE_TABLES:
                    c.exec_driver_sql(f"DROP TRIGGER IF EXISTS immutable_{table} ON {table}")
                    c.exec_driver_sql(f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_history_change()")
                c.exec_driver_sql(f"CREATE OR REPLACE FUNCTION protect_admin() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF OLD.id = '{ADMIN_ID}' THEN RAISE EXCEPTION 'permanent admin'; END IF; RETURN OLD; END $$")
                c.exec_driver_sql("DROP TRIGGER IF EXISTS keep_admin ON accounts")
                c.exec_driver_sql("CREATE TRIGGER keep_admin BEFORE DELETE ON accounts FOR EACH ROW EXECUTE FUNCTION protect_admin()")
    with factory() as db:
        version = db.get(SchemaVersion, 1)
        if not version or version.version != SCHEMA_VERSION:
            raise RuntimeError("数据库尚未初始化或版本不匹配，请先运行 python -m app.manage init-db。")
    return engine, factory

class UploadGrant(Base):
    __tablename__ = "upload_grants"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    resource_sha: Mapped[str] = mapped_column(ForeignKey("resources.sha256"), index=True)
    expires_at: Mapped[str] = mapped_column(String(40))
