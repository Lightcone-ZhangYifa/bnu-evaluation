""".zcp: ZIP + versioned manifest + JSON + unique SHA-256-addressed originals.

Integrity is verified, not identity/authenticity of the person who created a file.
Imported accounts/permissions/publication visibility are never trusted.
"""
from io import BytesIO
from copy import deepcopy
import zipfile, json, hashlib, secrets
from sqlalchemy import select
from .db import Project, Student, Resource, PublicList, PublicVersion, Audit, Account, uid, now
from .security import canonical, password_hash, audit
from .storage import safe_zip, sniff, InvalidFile
from .schemas import RuleSet, Annotation, PublicBody, ProjectCreate, StudentInput, ScheduleWrite, AcademicWrite, AppealCreate
from .domain import as_utc
from .rules import academic_average
from .domain import contract_hash
from . import __version__


def resource_hashes(body, public_versions=()):
    sha={e["resource_sha"] for e in body["evidence"]}
    sha.update(d["sha256"] for d in body["source_documents"])
    sha.update(a["resource_sha"] for a in body["academic_versions"])
    sha.update(v["body"]["resource_sha"] for v in public_versions)
    return sha

def export_project(db,domain,project):
    publics=[]
    for l,v in domain.public_versions(db,project,history=True):
        # Public bytes remain whole and unique; structured rows are scoped to class.
        body=deepcopy(v.body);body["rows"]=[r for r in body["rows"] if r["student_id"] in project.body["student_ids"]]
        publics.append({"list_id":l.id,"name":v.body.get("name_at_version") or l.name,"revision":v.revision,"body":body,"actor_id":v.actor_id,"created_at":v.created_at})
    events=db.scalars(select(Audit).where(Audit.target==project.id).order_by(Audit.created_at,Audit.id)).all()
    audits=[{k:getattr(a,k) for k in ("id","actor_id","actor_kind","target","action","details","signature","created_at")} for a in events]
    payload={"id":project.id,"title":project.title,"college_id":project.college_id,
        "created_at":project.created_at,"updated_at":project.updated_at,"revision":project.revision,
        "body":project.body,"roster":domain.roster(db,project.body["student_ids"]),"public_versions":publics,"audits":audits}
    encoded=canonical(payload).encode()
    files={"project.json":encoded};metadata={}
    for sha in sorted(resource_hashes(project.body,publics)):
        res=db.get(Resource,sha)
        if not res:raise ValueError("存档引用的资源元数据缺失："+sha)
        data=domain.store.path(sha).read_bytes()
        if hashlib.sha256(data).hexdigest()!=sha:raise ValueError("存储中的资源已损坏："+sha)
        path="objects/"+sha;files[path]=data;metadata[path]={"media_type":res.media_type}
    manifest={"format":"clearledger-project","format_version":1,"engine_version":__version__,
        "created_at":now(),"project_title":project.title,"classification":"contains-student-personal-data",
        "authenticity":"not-digitally-signed; hashes detect corruption, not a forged author",
        "files":{name:{"sha256":hashlib.sha256(data).hexdigest(),"size":len(data),**metadata.get(name,{})} for name,data in files.items()}}
    out=BytesIO()
    with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json",canonical(manifest).encode())
        for name,data in files.items():z.writestr(name,data)
    return out.getvalue(),manifest

def verify_archive(data):
    z=safe_zip(data,512*1024*1024,5000)
    try:
        manifest=json.loads(z.read("manifest.json"))
        if manifest["format"]!="clearledger-project" or manifest["format_version"]!=1:raise ValueError("工程格式版本不支持。")
        names=set(z.namelist());expected=set(manifest["files"])|{"manifest.json"}
        if names!=expected:raise ValueError("工程成员与清单不一致。")
        for name,meta in manifest["files"].items():
            if name!="project.json" and not (name.startswith("objects/") and len(name)==72):raise ValueError("未知工程成员路径。")
            b=z.read(name)
            if len(b)!=meta["size"] or hashlib.sha256(b).hexdigest()!=meta["sha256"]:raise ValueError("工程校验失败："+name)
            if name.startswith("objects/") and name[8:]!=meta["sha256"]:raise ValueError("对象名与内容哈希不一致。")
        if len(z.read("project.json"))>32*1024*1024:raise ValueError("工程数据过大。")
        payload=json.loads(z.read("project.json"))
        body=payload["body"]
        if body["format_version"]!=1:raise ValueError("数据版本不支持。")
        required={"format_version","meta","state","schedule","student_ids","reviewer_ids","rule_versions","instruction_versions","source_documents","evidence","public_bindings","academic_versions","snapshots","publication_id","appeals","receipts"}
        allowed=required|{"import_provenance"}
        if not required.issubset(body) or set(body)-allowed:raise ValueError("工程字段不完整或存在未知字段。")
        if len(body["evidence"])>12000 or len(body["student_ids"])>2000:raise ValueError("工程规模超过限制。")
        if not body["rule_versions"]:raise ValueError("工程缺少规则。")
        for r in body["rule_versions"]:
            rules=RuleSet(**{k:r[k] for k in ("nodes","display_places","explanation")}).model_dump()
            if r["contract_hash"]!=contract_hash(rules):raise ValueError("规则合同哈希不一致。")
        members=set(body["student_ids"])
        if len(members)!=len(body["student_ids"]) or members!={r["id"] for r in payload["roster"]}:raise ValueError("工程名册不一致。")
        ids={e["id"] for e in body["evidence"]}
        if len(ids)!=len(body["evidence"]):raise ValueError("材料ID重复。")
        for e in body["evidence"]:
            if e["student_id"] not in members:raise ValueError("材料提交人不在名册中。")
            if len(e["annotations"])>1000:raise ValueError("异常标注历史。")
            for v in e["annotations"]:Annotation(**v["data"])
        for p in payload["public_versions"]:PublicBody(**p["body"])
        if not isinstance(payload.get("title"),str) or not 1<=len(payload["title"])<=200:raise ValueError("工程名称无效。")
        ProjectCreate(title=payload["title"],year=body["meta"]["year"],class_name=body["meta"]["class_name"],
            responsible=body["meta"].get("responsible",""),assessment_start=body["meta"]["assessment_start"],assessment_end=body["meta"]["assessment_end"],student_ids=body["student_ids"])
        if body["state"] not in ("setup","submission","review","publication","closed","archived"):raise ValueError("未知阶段。")
        if body["schedule"]:ScheduleWrite(revision=1,**body["schedule"])
        if len(payload["roster"])!=len(members):raise ValueError("名册包含重复行。")
        for row in payload["roster"]:
            StudentInput(**{k:row[k] for k in ("number","name","class_name")})
            if not isinstance(row["id"],str) or not row["id"] or len(row["id"])>120:raise ValueError("非法学生标识。")
        if len({r["number"] for r in payload["roster"]})!=len(members):raise ValueError("学号重复。")
        rule_ids={r["id"] for r in body["rule_versions"]}
        prompt_ids={r["id"] for r in body["instruction_versions"]}
        if len(rule_ids)!=len(body["rule_versions"]) or len(prompt_ids)!=len(body["instruction_versions"]):raise ValueError("版本ID重复。")
        for prompt in body["instruction_versions"]:
            if not isinstance(prompt["text"],str) or len(prompt["text"])>100000:raise ValueError("辅助说明格式无效。")
            if hashlib.sha256(prompt["text"].encode()).hexdigest()!=prompt["sha256"]:raise ValueError("辅助说明哈希不一致。")
        identities=set();images_seen=set();evidence_by_id={e["id"]:e for e in body["evidence"]}
        for e in body["evidence"]:
            if not isinstance(e["id"],str) or not 1<=len(e["id"])<=120:raise ValueError("材料标识无效。")
            as_utc(e["submitted_at"])
            key=(e["student_id"],e["idempotency_key"]);image=(e["student_id"],e["resource_sha"])
            if key in identities or image in images_seen:raise ValueError("重复图片或提交幂等标识。")
            identities.add(key);images_seen.add(image)
            if e.get("parent_id"):
                target=evidence_by_id.get(e["parent_id"])
                if not target or target["student_id"]!=e["student_id"] or target["id"]==e["id"]:raise ValueError("增量材料原件引用无效。")
            for index,v in enumerate(e["annotations"],1):
                if v["version"]!=index or not isinstance(v["reviewed"],bool):raise ValueError("标注版本序号或核对状态无效。")
                if v["rule_version_id"] not in rule_ids or v.get("instruction_version_id") not in prompt_ids|{None}:raise ValueError("标注引用的规则/解释版本缺失。")
                expected=next(r["contract_hash"] for r in body["rule_versions"] if r["id"]==v["rule_version_id"])
                if v["contract_hash"]!=expected:raise ValueError("标注规则语义版本不一致。")
                if v["actor_kind"]=="agent" and v["reviewed"]:raise ValueError("外部Agent不能被标记为人工核对。")
                for rel in ("support_for","duplicate_of","replaces"):
                    target=v["data"].get(rel)
                    if target and (target not in evidence_by_id or evidence_by_id[target]["student_id"]!=e["student_id"] or target==e["id"]):raise ValueError("材料关联目标不合法。")
            seen=set();current=e
            while current and current["annotations"]:
                if current["id"] in seen:raise ValueError("材料关联存在循环。")
                seen.add(current["id"]);v=current["annotations"][-1]["data"]
                target=v.get("support_for") or v.get("duplicate_of") or v.get("replaces")
                current=evidence_by_id.get(target)
        versions={(p["list_id"],p["revision"]) for p in payload["public_versions"]}
        if len(versions)!=len(payload["public_versions"]) or versions!={(b["list_id"],b["revision"]) for b in body["public_bindings"]}:raise ValueError("公共名单历史引用不完整或重复。")
        for public in payload["public_versions"]:
            if any(r["student_id"] not in members for r in public["body"]["rows"]):raise ValueError("名单行不在工程名册中。")
        for av in body["academic_versions"]:
            a=AcademicWrite(revision=1,**{k:av[k] for k in ("resource_sha","courses","students","confirmed","note")})
            if any(g.student_id not in members for g in a.students):raise ValueError("学业成绩不在工程名单中。")
            for row in a.students:academic_average([c.model_dump() for c in a.courses],[g.model_dump() for g in row.entries])
        for appeal in body["appeals"]:
            if appeal["student_id"] not in members:raise ValueError("申请人不在工程名册中。")
            AppealCreate(reason=appeal["reason"],evidence_id=appeal.get("evidence_id"))
            if appeal.get("evidence_id") and evidence_by_id.get(appeal["evidence_id"],{}).get("student_id")!=appeal["student_id"]:raise ValueError("申请材料引用无效。")
            for decision in appeal["history"]:
                if decision["decision"] not in ("approved","rejected"):raise ValueError("申请处理状态无效。")
                if decision.get("until"):as_utc(decision["until"])
        for receipt in body["receipts"]:
            if receipt["student_id"] not in members or any(evidence_by_id.get(e,{}).get("student_id")!=receipt["student_id"] for e in receipt["evidence_ids"]):raise ValueError("提交回执引用无效。")
        for snapshot in body["snapshots"]:
            if not isinstance(snapshot["results"],dict) or set(snapshot["results"])-members:raise ValueError("公示结果包含非法学生。")
            if snapshot["mode"] not in ("provisional","final"):raise ValueError("公示类型无效。")
        refs=resource_hashes(body,payload["public_versions"])
        if refs!={n[8:] for n in manifest["files"] if n.startswith("objects/")}:raise ValueError("资源引用不完整或包含未引用资源。")
        # Do not trust claimed MIME types, even when the internal hash is valid.
        images={e["resource_sha"] for e in body["evidence"]}
        for sha in refs:
            raw=z.read("objects/"+sha)
            if len(raw)>(20 if sha in images else 40)*1024*1024:raise ValueError("工程中的单文件超过上传限制。")
            actual=sniff(raw,"image" if sha in images else "document")
            if actual!=manifest["files"]["objects/"+sha]["media_type"]:raise ValueError("文件媒体类型不一致。")
        return z,manifest,payload
    except (KeyError,TypeError,IndexError,RecursionError,json.JSONDecodeError,zipfile.BadZipFile) as exc:
        raise InvalidFile("工程结构不完整或文件损坏。") from exc


def replace_ids(value,mapping):
    if isinstance(value,str):return mapping.get(value,value)
    if isinstance(value,list):return [replace_ids(x,mapping) for x in value]
    if isinstance(value,dict):return {mapping.get(k,k):replace_ids(v,mapping) for k,v in value.items()}
    return value

def import_project(db,p,domain,data):
    p.require_teacher()
    z,manifest,payload=verify_archive(data)
    mapping={};credentials=[]
    for original in payload["roster"]:
        student=db.scalar(select(Student).where(Student.college_id==p.user.college_id,Student.number==original["number"]))
        if student:
            if student.name!=original["name"]:raise ValueError("学号姓名冲突，需先核对名册："+original["number"])
        else:
            student=Student(id=uid(),college_id=p.user.college_id,number=original["number"],name=original["name"],class_name=original["class_name"])
            db.add(student);db.flush()
            username=student.number
            if db.scalar(select(Account).where(Account.username==username)):username=p.user.college_id[:8]+":"+student.number
            initial=secrets.token_urlsafe(18)
            db.add(Account(username=username,name=student.name,role="student",college_id=p.user.college_id,student_id=student.id,password_hash=password_hash(initial),must_change=True))
            credentials.append({"number":student.number,"name":student.name,"username":username,"initial_password":initial})
        mapping[original["id"]]=student.id
    for name,meta in manifest["files"].items():
        if name.startswith("objects/"):
            resource=z.read(name);domain.store.scan(resource)
            domain.store.put_verified(db,meta["sha256"],resource,meta["media_type"])
    public_map={};list_map={}
    # Preserve every historically pinned version, not just the latest source.
    for v in sorted(payload["public_versions"],key=lambda v:(v["list_id"],v["revision"])):
        original=v["list_id"]
        if original not in list_map:
            lid=uid();list_map[original]=lid
            db.add(PublicList(id=lid,college_id=p.user.college_id,owner_id=p.user.id,name=v["name"]+"（导入）",revision=v["revision"]));db.flush()
        lid=list_map[original]
        db.get(PublicList,lid).revision=max(db.get(PublicList,lid).revision,v["revision"])
        public_map[(original,v["revision"])]=(lid,v["revision"])
        db.add(PublicVersion(id=uid(),list_id=lid,revision=v["revision"],body=replace_ids(v["body"],mapping),actor_id=p.user.id))
    body=replace_ids(deepcopy(payload["body"]),mapping)
    body["reviewer_ids"]=[];body["publication_id"]=None;body["state"]="review" if body["schedule"] else "setup"
    body["meta"]["import_unverified"]=True
    body["meta"]["workflow_restart_at"]=now()
    body["import_provenance"]={"archive_sha256":hashlib.sha256(data).hexdigest(),"original_project_id":payload["id"],
        "original_revision":payload["revision"],"imported_at":now(),"acknowledged":False,
        "audit_events":payload["audits"],"note":"来源工程历史保留；身份与权限不导入；旧公示不会自动向新实例的学生开放。"}
    for binding in body["public_bindings"]:
        binding["list_id"],binding["revision"]=public_map[(binding["list_id"],binding["revision"])]
    project=Project(id=uid(),owner_id=p.user.id,college_id=p.user.college_id,title=payload["title"]+"（导入）",body=body)
    db.add(project);db.flush()
    audit(db,domain.settings,p,project.id,"project.import",{"archive_sha256":body["import_provenance"]["archive_sha256"],"source_id":payload["id"]})
    return project,credentials
