from __future__ import annotations
from copy import deepcopy
from datetime import datetime, timezone
from collections import Counter
from sqlalchemy import select, update
from fastapi import HTTPException
from .db import (Project, Student, Account, PublicList, PublicVersion, Resource, Folder, Audit, uid, now)
from .schemas import Annotation, PublicBody, RuleSet, decimal
from .rules import (bnu_template, validate_tree, category_path, within_period, calculate, academic_average, text)
from .security import audit, canonical, digest, Principal

class Conflict(HTTPException):
    def __init__(self, detail="数据已被其他人更新，请刷新后重试。"):
        super().__init__(409,detail)

def object_dict(obj, *fields): return {k:getattr(obj,k) for k in fields}
def active_rules(body): return body["rule_versions"][-1]
def active_prompt(body): return body["instruction_versions"][-1] if body["instruction_versions"] else None

def contract_hash(rules):
    return digest(canonical([{k:n.get(k) for k in ("id","parent","name","source")} for n in rules["nodes"]]))

def stage(body):
    s=body["state"]; schedule=body.get("schedule")
    if s=="submission" and schedule:
        if now()<schedule["opens_at"]: return "scheduled"
        if now()>=schedule["deadline_at"]: return "review"
    return s

def as_utc(value):
    if isinstance(value,str): value=datetime.fromisoformat(value.replace("Z","+00:00"))
    if not value.tzinfo: raise ValueError("时刻缺少时区。")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")

def immutable(old, new):
    """History is append-only; identity and original bytes may not be rewritten."""
    for key in ("rule_versions","instruction_versions","source_documents","snapshots","academic_versions","receipts","public_bindings"):
        before=old.get(key,[]); after=new.get(key,[])
        if len(after)<len(before) or after[:len(before)]!=before:
            raise ValueError(f"{key} 是不可变历史，只能追加。")
    after={e["id"]:e for e in new["evidence"]}
    if len(after)!=len(new["evidence"]): raise ValueError("材料ID重复。")
    for record in old["evidence"]:
        n=after.get(record["id"])
        if n is None: raise ValueError("不允许删除旧材料。")
        for key in ("id","student_id","resource_sha","submitted_at","submitted_by","parent_id","request_id","incremental","idempotency_key"):
            if record.get(key)!=n.get(key): raise ValueError("材料原始信息不可修改。")
        history=record["annotations"]
        if n["annotations"][:len(history)]!=history: raise ValueError("不允许修改已有标注版本。")
    appeals={a["id"]:a for a in new["appeals"]}
    for a in old["appeals"]:
        n=appeals.get(a["id"])
        if not n: raise ValueError("不允许删除申请。")
        for key in ("id","student_id","evidence_id","reason","created_at"):
            if a[key]!=n[key]: raise ValueError("申请原始信息不可修改。")
        if n["history"][:len(a["history"])]!=a["history"]: raise ValueError("申请历史不可修改。")

class Domain:
    def __init__(self,settings,store): self.settings,self.store=settings,store

    def can_review(self,p,project):
        if p.user.role=="admin": return False
        return p.user.id==project.owner_id or p.user.id in project.body["reviewer_ids"]

    def is_member(self,p,project):
        return p.user.student_id and p.user.student_id in project.body["student_ids"]

    def project(self,db,p,pid,scope="context:read",review=False,owner=False,lock=False):
        p.scope(pid,scope)
        statement=select(Project).where(Project.id==pid)
        if lock: statement=statement.with_for_update()
        project=db.scalar(statement)
        if not project or project.trashed: raise HTTPException(404,"工程不存在或在回收站。")
        if p.user.role=="admin": raise HTTPException(403,"运维管理员不参与测评业务。")
        if project.college_id!=p.user.college_id: raise HTTPException(404,"工程不存在。")
        if owner:
            p.require_teacher()
            if p.user.id!=project.owner_id: raise HTTPException(403,"仅工程负责教师可管理。")
        elif review:
            if not self.can_review(p,project): raise HTTPException(403,"未获得此工程的审核权限。")
        elif not self.can_review(p,project) and not self.is_member(p,project):
            raise HTTPException(404,"工程不存在。")
        return project

    def save(self,db,p,project,body,revision,action,details=None, **metadata):
        if revision!=project.revision: raise Conflict()
        immutable(project.body,body)
        stamp=now()
        n=db.execute(update(Project).where(Project.id==project.id,Project.revision==revision)
            .values(body=body,revision=revision+1,updated_at=stamp,**metadata)
            .execution_options(synchronize_session=False)).rowcount
        if not n: raise Conflict()
        audit(db,self.settings,p,project.id,action,{"from_revision":revision,"to_revision":revision+1,**(details or {})})
        db.expire(project); db.refresh(project)
        return project

    def roster(self,db,ids):
        if not ids: return []
        students=db.scalars(select(Student).where(Student.id.in_(ids))).all()
        return [object_dict(s,"id","number","name","class_name","college_id","active") for s in sorted(students,key=lambda x:x.number)]

    def check_members(self,db,college,ids):
        if len(ids)!=len(set(ids)): raise ValueError("名单中有重复学生。")
        records=self.roster(db,ids)
        if len(records)!=len(ids) or any(s["college_id"]!=college for s in records): raise ValueError("学生不存在或不属于此学院。")
        return records

    def create(self,db,p,payload):
        p.require_teacher()
        self.check_members(db,p.user.college_id,payload.student_ids)
        self.check_folder(db,p,payload.folder_id)
        rules=RuleSet(**bnu_template()).model_dump()
        rule={"id":uid(),"created_at":now(),"actor":p.user.id,"reason":"建立工程，加载可编辑的学院模板", "contract_hash":contract_hash(rules),**rules}
        body={"format_version":1,"meta":{"year":payload.year,"class_name":payload.class_name,
            "responsible":payload.responsible,"assessment_start":str(payload.assessment_start),
            "assessment_end":str(payload.assessment_end),"test_only":payload.test_only},
            "state":"setup","schedule":None,"student_ids":payload.student_ids,"reviewer_ids":[],
            "rule_versions":[rule],"instruction_versions":[],"source_documents":[],"evidence":[],
            "public_bindings":[],"academic_versions":[],"snapshots":[],"publication_id":None,"appeals":[],"receipts":[]}
        project=Project(id=uid(),owner_id=p.user.id,college_id=p.user.college_id,title=payload.title,folder_id=payload.folder_id,body=body)
        db.add(project);db.flush()
        audit(db,self.settings,p,project.id,"project.create",{"title":project.title})
        return project

    def check_folder(self,db,p,fid):
        if fid:
            folder=db.get(Folder,fid)
            if not folder or folder.owner_id!=p.user.id or folder.trashed: raise ValueError("目标文件夹不可用。")

    def summary(self,db,p,project):
        return {"id":project.id,"title":project.title,"revision":project.revision,
            "folder_id":project.folder_id,"trashed":project.trashed,"updated_at":project.updated_at,
            "meta":project.body["meta"],"state":stage(project.body),"owner_id":project.owner_id,
            "can_manage":project.owner_id==p.user.id and not p.token,"can_review":self.can_review(p,project),
            "student_count":len(project.body["student_ids"]),"evidence_count":len(project.body["evidence"]) if self.can_review(p,project) else sum(e["student_id"]==p.user.student_id for e in project.body["evidence"]),
            "schedule":project.body["schedule"],"publication_id":project.body["publication_id"]}

    def view(self,db,p,project):
        out=self.summary(db,p,project)
        body=deepcopy(project.body)
        if self.can_review(p,project):
            body["snapshots"]=[{k:v for k,v in s.items() if k!="results"} for s in body["snapshots"]]
            # A class representative is not allowed to see unpublished own scores
            # through reviewer APIs. Neither are embedded annotation histories.
            if p.user.student_id:
                for e in body["evidence"]:
                    if e["student_id"]==p.user.student_id: e["annotations"]=[]
                body["academic_versions"]=[]
            body["roster"]=self.roster(db,body["student_ids"])
            body["staff"]=[object_dict(a,"id","name","role","student_id") for a in db.scalars(select(Account).where(Account.id.in_(body["reviewer_ids"]+[project.owner_id]))).all()]
            out["body"]=body
        else:
            out["body"]={"meta":body["meta"],"schedule":body["schedule"],
                "evidence":[{k:v for k,v in e.items() if k not in ("annotations","idempotency_key","submitted_by")} for e in body["evidence"] if e["student_id"]==p.user.student_id],
                "appeals":[a for a in body["appeals"] if a["student_id"]==p.user.student_id],
                "receipts":[r for r in body["receipts"] if r["student_id"]==p.user.student_id]}
        return out

    def rules_write(self,db,p,project,payload):
        if project.body["state"] in ("closed","archived"): raise Conflict("工程已冻结。")
        body=deepcopy(project.body); rules=payload.rules.model_dump()
        body["rule_versions"].append({"id":uid(),"created_at":now(),"actor":p.user.id,
            "reason":payload.reason,"contract_hash":contract_hash(rules),**rules})
        return self.save(db,p,project,body,payload.revision,"rules.append",{"reason":payload.reason})

    def instructions_write(self,db,p,project,payload):
        if project.body["state"] in ("closed","archived"): raise Conflict("工程已冻结。")
        body=deepcopy(project.body)
        body["instruction_versions"].append({"id":uid(),"text":payload.text,"created_at":now(),
            "actor":p.user.id,"reason":payload.reason,"sha256":digest(payload.text)})
        return self.save(db,p,project,body,payload.revision,"instructions.append",{"reason":payload.reason})

    def add_document(self,db,p,project,data,filename,purpose,revision):
        if project.body["state"] in ("closed","archived"): raise Conflict("工程已冻结。")
        if purpose not in ("rules","support"): raise ValueError("未知文档用途。")
        resource=self.store.put(db,data,"document")
        body=deepcopy(project.body)
        # Same bytes stored once, logical source-document attachment is also unique.
        if not any(x["sha256"]==resource["sha256"] and x["purpose"]==purpose for x in body["source_documents"]):
            body["source_documents"].append({**resource,"name":filename[:250],"purpose":purpose,"created_at":now(),"actor":p.user.id})
        return self.save(db,p,project,body,revision,"document.attach",{"sha256":resource["sha256"],"purpose":purpose})

    def members_write(self,db,p,project,payload):
        if project.body["state"] in ("closed","archived"): raise Conflict("工程已冻结。")
        self.check_members(db,project.college_id,payload.student_ids)
        body0=project.body
        historical=set(e["student_id"] for e in body0["evidence"])
        historical.update(a["student_id"] for a in body0["appeals"])
        historical.update(r["student_id"] for r in body0["receipts"])
        if body0["snapshots"] or body0["academic_versions"] or body0["public_bindings"]:
            historical.update(body0["student_ids"])
        if historical-set(payload.student_ids):
            raise ValueError("已有材料、成绩、名单、公示或申请历史的学生不能移除；请保留历史。")
        staff=db.scalars(select(Account).where(Account.id.in_(payload.reviewer_ids))).all() if payload.reviewer_ids else []
        if len(staff)!=len(set(payload.reviewer_ids)) or any(s.college_id!=project.college_id or s.role=="admin" for s in staff):
            raise ValueError("审核人员必须是本学院名单中的教师或学生。")
        body=deepcopy(project.body);body["student_ids"]=payload.student_ids;body["reviewer_ids"]=list(dict.fromkeys(payload.reviewer_ids))
        return self.save(db,p,project,body,payload.revision,"members.update",{"students":len(payload.student_ids),"reviewers":payload.reviewer_ids})

    def schedule_write(self,db,p,project,payload):
        if project.body["state"] in ("closed","archived"):
            raise Conflict("工程已冻结，不能修改时间窗口。")
        old=project.body.get("schedule")
        times={"opens_at":as_utc(payload.opens_at),"deadline_at":as_utc(payload.deadline_at),"appeal_deadline":as_utc(payload.appeal_deadline)}
        if project.body["state"] in ("review","publication") and old:
            if times["opens_at"]!=old["opens_at"] or times["deadline_at"]!=old["deadline_at"] or times["appeal_deadline"]<old["appeal_deadline"]:
                raise ValueError("审核或公示期间只能延长全体申诉截止时间；个人补交请走系统申请。")
        if project.body["state"]=="submission" and old:
            if times["opens_at"]!=old["opens_at"] or times["deadline_at"]<old["deadline_at"] or times["appeal_deadline"]<old["appeal_deadline"]:
                raise ValueError("已开放的窗口只允许延长截止时间，不允许缩短或移动开放时间。")
        body=deepcopy(project.body);body["schedule"]=times
        return self.save(db,p,project,body,payload.revision,"schedule.update",{"old":old,"new":times})

    def transition(self,db,p,project,payload):
        body=deepcopy(project.body);current=body["state"]; target=payload.state
        allowed={"setup":{"submission"},"submission":{"review"},"review":set(),"publication":{"review","closed"},"closed":{"archived"},"archived":set()}
        if target not in allowed.get(current,set()): raise Conflict("不允许的阶段跳转。")
        if target=="submission":
            if not body["schedule"] or not any(d["purpose"]=="rules" for d in body["source_documents"]):
                raise ValueError("请先存档细则并设置开放时间。")
            if not body["student_ids"]: raise ValueError("请先导入参评名单。")
        if current=="submission" and now()<body["schedule"]["deadline_at"]:
            raise ValueError("不能在已通知的截止时间前结束提交。")
        if current=="publication" and now()<body["schedule"]["appeal_deadline"]:
            raise ValueError("公示申诉期未结束。")
        if target=="closed":
            snapshot=next((s for s in body["snapshots"] if s["id"]==body["publication_id"]),None)
            if not snapshot or snapshot["mode"]!="final": raise ValueError("需要完成最终公示快照后才能结项。")
            if snapshot["input_hash"]!=self.input_fingerprint(db,project): raise ValueError("公示后数据已变化，请重新计算并公示。")
        body["state"]=target
        return self.save(db,p,project,body,payload.revision,"stage.transition",{"from":current,"to":target,"reason":payload.reason})

    def upload_allowed(self,body,student_id,parent_id=None,request_id=None):
        schedule=body.get("schedule"); current=now()
        if not schedule or body["meta"].get("import_unverified"): return False
        if body["state"]=="submission" and schedule["opens_at"]<=current<schedule["deadline_at"]:
            return parent_id is None
        if body["state"]=="publication" and current<schedule["appeal_deadline"]:
            # Incremental supplementation: original remains immutable. New standalone
            # material is allowed during public review and labelled as an increment.
            return True
        if body["state"] in ("review","publication") and request_id:
            request=next((a for a in body["appeals"] if a["id"]==request_id and a["student_id"]==student_id),None)
            if request and request["history"]:
                decision=request["history"][-1]
                return decision["decision"]=="approved" and decision.get("until") and current<decision["until"] and decision["created_at"]>=body["meta"].get("workflow_restart_at","")
        return False

    def upload_evidence(self,db,p,project,data,key,parent_id=None,request_id=None):
        p.require_human()
        if not self.is_member(p,project): raise HTTPException(403,"只有本工程参评学生可以提交个人材料。")
        existing=next((e for e in project.body["evidence"] if e["student_id"]==p.user.student_id and e["idempotency_key"]==key),None)
        sha=digest_bytes(data)
        if existing:
            if existing["resource_sha"]!=sha: raise Conflict("同一幂等键对应不同图片。")
            return existing,project
        if not self.upload_allowed(project.body,p.user.student_id,parent_id,request_id):
            raise HTTPException(403,"当前不在提交/补充窗口内。逾期请先在系统中申请。")
        if parent_id and not any(e["id"]==parent_id and e["student_id"]==p.user.student_id for e in project.body["evidence"]):
            raise ValueError("补充材料只能关联本人的旧材料。")
        evidence=project.body["evidence"]
        if len(evidence)>=self.settings.max_project_evidence or sum(e["student_id"]==p.user.student_id for e in evidence)>=self.settings.max_student_evidence:
            raise ValueError("材料数量达到上限，请联系教师处理。")
        if any(e["student_id"]==p.user.student_id and e["resource_sha"]==sha for e in evidence):
            raise Conflict("相同图片已经提交，原记录仍然有效，无需重复提交。")
        r=self.store.put(db,data,"image")
        record={"id":uid(),"resource_sha":r["sha256"],"student_id":p.user.student_id,
            "submitted_at":now(),"submitted_by":p.user.id,"parent_id":parent_id,"request_id":request_id,
            "idempotency_key":key,"incremental":project.body["state"]!="submission","annotations":[]}
        body=deepcopy(project.body);body["evidence"].append(record)
        updated=self.save(db,p,project,body,project.revision,"evidence.append",{"evidence_id":record["id"],"resource_sha":r["sha256"]})
        return record,updated

    def evidence(self,project,eid):
        item=next((e for e in project.body["evidence"] if e["id"]==eid),None)
        if not item: raise HTTPException(404,"材料不存在。")
        return item

    def annotation_version(self,p,project,eid,payload,source="api"):
        if source not in ("api", "web_ai"):
            raise ValueError("未知标注来源。")
        external = bool(p.token) or source == "web_ai"
        p.scope(project.id,"annotation:write")
        if project.body["state"] in ("setup","closed","archived"):
            raise Conflict("当前阶段不允许标注。")
        record=self.evidence(project,eid)
        if record["student_id"]==p.user.student_id: raise HTTPException(403,"不能审核本人的材料。")
        for old in record["annotations"]:
            if old["idempotency_key"]==payload.idempotency_key:
                if Annotation.model_validate(old["data"]).model_dump()!=payload.annotation.model_dump(): raise Conflict("幂等键对应的标注内容不同。")
                return None
        if payload.expected_annotation_version!=len(record["annotations"]): raise Conflict("该材料已产生新标注版本。")
        if external and record["annotations"] and record["annotations"][-1]["reviewed"]:
            raise HTTPException(403,"Agent 不得覆盖人工核对结论。")
        a=payload.annotation.model_dump(); rules=active_rules(project.body)
        if external and (a.get("human_note") or a.get("student_note") or a.get("date_override_reason") or a.get("replaces")):
            raise HTTPException(403,"Agent 不可填写人工备注、豁免日期或替代已计分材料。")
        if decimal(a["score"])<0 and (external or p.user.role!="teacher"):
            raise HTTPException(403,"扣分认定必须由教师人工完成。")
        if not external and a["status"]!="valid" and not a.get("human_note"):
            raise ValueError("人工确认无效/待判断材料时请填写原因。")
        if a["category_id"]:
            by_id,children,_=validate_tree(rules["nodes"])
            if a["category_id"] not in by_id or children[a["category_id"]]: raise ValueError("必须选择规则树的叶子分类。")
        if a["status"]=="valid" and not a.get("support_for") and not a.get("duplicate_of"):
            meta=project.body["meta"]
            if not within_period(a["material_date"],meta["assessment_start"],meta["assessment_end"]):
                if not a.get("date_override_reason") or external or p.user.role!="teacher":
                    raise ValueError("材料日期缺失、跨越或不在参评期内；应标记需人工判断，只有教师可根据佐证写明日期认定依据。")
        for relation in ("support_for","duplicate_of","replaces"):
            target=a.get(relation)
            if target:
                t=self.evidence(project,target)
                if t["student_id"]!=record["student_id"] or target==record["id"]:
                    raise ValueError("关联材料必须属于同一个学生且不能指向自身。")
                if relation=="replaces" and p.user.role!="teacher": raise HTTPException(403,"只有教师可确认替代关系。")
                # Avoid cycles across all types of supporting/superseding relation.
                seen={eid}; cursor=t
                while cursor:
                    if cursor["id"] in seen: raise ValueError("材料关联形成循环。")
                    seen.add(cursor["id"])
                    latest=cursor["annotations"][-1]["data"] if cursor["annotations"] else {}
                    nxt=latest.get("support_for") or latest.get("duplicate_of") or latest.get("replaces")
                    cursor=self.evidence(project,nxt) if nxt else None
        prompt=active_prompt(project.body)
        version={"id":uid(),"version":len(record["annotations"])+1,"data":a,
            "reviewed":not external,"actor_id":p.user.id,"actor_kind":"web_ai" if source=="web_ai" else p.kind,
            "agent_name":p.token.name if p.token else "网页 AI 表格回填" if source=="web_ai" else None,"created_at":now(),
            "rule_version_id":rules["id"],"contract_hash":rules["contract_hash"],
            "instruction_version_id":prompt["id"] if prompt else None,"idempotency_key":payload.idempotency_key}
        return version

    def annotate(self,db,p,project,eid,payload):
        version = self.annotation_version(p,project,eid,payload)
        if version is None:
            return project
        body=deepcopy(project.body)
        self.evidence_obj(body,eid)["annotations"].append(version)
        return self.save(db,p,project,body,payload.revision,"annotation.append",{
            "evidence_id":eid,"version":version["version"],
            "status":version["data"]["status"],"reviewed":version["reviewed"]})

    def annotation_batch(self,db,p,project,items,revision,source="api",dry_run=False):
        """Compile into a detached aggregate, then a single compare-and-swap.

        Individual validation is exactly the same as the one-image endpoint.
        A failure (including a relation cycle) never writes a partial batch.
        """
        from types import SimpleNamespace
        body=deepcopy(project.body)
        candidate=SimpleNamespace(id=project.id, body=body, revision=project.revision)
        changes=[]
        for eid,payload in items:
            version=self.annotation_version(p,candidate,eid,payload,source)
            if version is not None:
                self.evidence_obj(body,eid)["annotations"].append(version)
                changes.append({"evidence_id":eid,"version":version["version"],"status":version["data"]["status"]})
        if dry_run or not changes:
            return project,changes
        return self.save(db,p,project,body,revision,"annotation.batch",{
            "source":source,"count":len(changes),"items":changes}),changes

    @staticmethod
    def evidence_obj(body,eid): return next(e for e in body["evidence"] if e["id"]==eid)

    def public_versions(self,db,project,history=False):
        versions=[]
        bindings=project.body["public_bindings"]
        if history:
            bindings=list({(b["list_id"],b["revision"]):b for b in bindings}.values())
        else:
            bindings=list({b["list_id"]:b for b in bindings}.values())
        for binding in bindings:
            v=db.scalar(select(PublicVersion).where(PublicVersion.list_id==binding["list_id"],PublicVersion.revision==binding["revision"]))
            l=db.get(PublicList,binding["list_id"])
            if not v or not l or l.college_id!=project.college_id: raise ValueError("工程引用的公共名单版本缺失。")
            versions.append((l,v))
        return versions

    def bind_public(self,db,p,project,payload):
        if project.body["state"] in ("closed","archived"): raise Conflict("工程已冻结。")
        v=db.scalar(select(PublicVersion).where(PublicVersion.list_id==payload.list_id,PublicVersion.revision==payload.list_revision))
        l=db.get(PublicList,payload.list_id)
        if not v or not l or l.college_id!=project.college_id: raise ValueError("公共名单版本不可用。")
        if not v.body["approved"]: raise ValueError("公共名单须先经负责教师确认，才能应用到工程。")
        by_id,children,_=validate_tree(active_rules(project.body)["nodes"])
        for r in v.body["rows"]:
            if r["student_id"] in project.body["student_ids"] and (r["category_id"] not in by_id or children[r["category_id"]]):
                raise ValueError("名单中的分类不是当前工程的有效叶子分类。")
        body=deepcopy(project.body)
        body["public_bindings"].append({"list_id":payload.list_id,"revision":payload.list_revision,"bound_at":now(),"actor_id":p.user.id,"contract_hash":active_rules(body)["contract_hash"],"instruction_version_id":(active_prompt(body) or {}).get("id")})
        return self.save(db,p,project,body,payload.revision,"public.bind",{"list_id":payload.list_id,"list_revision":payload.list_revision})

    def input_fingerprint(self,db,project):
        b=project.body
        # Only calculation inputs, not stage transitions or publication metadata.
        return digest(canonical({k:b[k] for k in ("student_ids","rule_versions","instruction_versions","source_documents","evidence","public_bindings","academic_versions")}))

    def compute(self,db,project):
        b=project.body;rules=active_rules(b);by_id,children,_=validate_tree(rules["nodes"])
        inputs={sid:[] for sid in b["student_ids"]}; issues={sid:[] for sid in b["student_ids"]}
        prompt=active_prompt(b); prompt_id=prompt["id"] if prompt else None
        annotations={e["id"]:e["annotations"][-1] for e in b["evidence"] if e["annotations"]}
        # Replacement is effective only after a valid, teacher-reviewed new item.
        replaced={v["data"]["replaces"]:eid for eid,v in annotations.items() if v["data"].get("replaces") and v["data"]["status"]=="valid" and v["reviewed"] and v["actor_kind"]=="teacher"}
        material_details={sid:[] for sid in b["student_ids"]}
        for e in b["evidence"]:
            sid=e["student_id"]; version=annotations.get(e["id"])
            a=version["data"] if version else {"status":"unlabeled","score":"0","description":"尚未标注"}
            detail={"id":e["id"],"resource_sha":e["resource_sha"],"submitted_at":e["submitted_at"],
                "parent_id":e.get("parent_id"),"submitted_by":e.get("submitted_by"),
                "last_modified_at":version["created_at"] if version else e["submitted_at"],
                "last_modified_by":version.get("actor_id") if version else e.get("submitted_by"),
                "actor_kind":version.get("actor_kind") if version else None,
                "annotation":{**deepcopy(a),"has_stamp":a.get("has_stamp"),"stamp_text":a.get("stamp_text")},"reviewed":version["reviewed"] if version else False,
                "annotation_version":version["version"] if version else 0,"excluded_reason":None}
            material_details[sid].append(detail)
            if not version:
                issues[sid].append({"id":e["id"],"kind":"unlabeled","message":"材料未标注"});continue
            if e["id"] in replaced:
                detail["excluded_reason"]="已由新增材料替代："+replaced[e["id"]];continue
            if version["contract_hash"]!=rules["contract_hash"] or version["instruction_version_id"]!=prompt_id:
                issues[sid].append({"id":e["id"],"kind":"stale","message":"分类定义或解释已更新，局部标注需重新确认"})
            if a["status"]!="valid":
                if a["status"] in ("unlabeled","manual","system_error") or not version["reviewed"]:
                    issues[sid].append({"id":e["id"],"kind":"review","message":"非有效结论尚需人工处理"})
                detail["excluded_reason"]=a["status"];continue
            if a.get("support_for") or a.get("duplicate_of"):
                detail["excluded_reason"]="仅作佐证/重复，不独立计分";continue
            if a["category_id"] not in by_id or children[a["category_id"]]:
                issues[sid].append({"id":e["id"],"kind":"category","message":"分类已失效"});continue
            inputs[sid].append({"id":e["id"],"category_id":a["category_id"],"score":a["score"],
                "description":a["description"],"source":"evidence","outcome_key":a.get("outcome_key"),"annotation_version":version["version"]})
        public_details={sid:[] for sid in b["student_ids"]}
        for l,v in self.public_versions(db,project):
            binding=next(x for x in reversed(b["public_bindings"]) if x["list_id"]==l.id)
            stale=(binding.get("contract_hash",rules["contract_hash"])!=rules["contract_hash"] or binding.get("instruction_version_id",prompt_id)!=prompt_id)
            for row in v.body["rows"]:
                sid=row["student_id"]
                if sid not in inputs:continue
                rd={**deepcopy(row),"list_id":l.id,"list_name":v.body.get("name_at_version") or l.name,"list_revision":v.revision,
                    "resource_sha":v.body["resource_sha"],"source_name":v.body.get("source_name"),
                    "has_stamp":row.get("has_stamp") if row.get("has_stamp") is not None else v.body.get("has_stamp"),
                    "stamp_text":row.get("stamp_text") if row.get("has_stamp") is not None else v.body.get("stamp_text")}
                public_details[sid].append(rd)
                if stale:
                    issues[sid].append({"id":row["id"],"kind":"stale_public","message":"名单绑定后分类或解释发生变化，教师须重新确认名单并绑定版本"})
                if row["status"]=="manual":
                    issues[sid].append({"id":row["id"],"kind":"public","message":"公共名单行待认定"})
                    continue
                if row["status"]!="valid":continue
                if row["category_id"] not in by_id or children[row["category_id"]]:
                    issues[sid].append({"id":row["id"],"kind":"category","message":"公共名单行分类失效"});continue
                if row.get("material_date") and not within_period(row["material_date"],b["meta"]["assessment_start"],b["meta"]["assessment_end"]):
                    issues[sid].append({"id":row["id"],"kind":"date","message":"公共名单行日期不在参评期内"});continue
                inputs[sid].append({"id":f"public:{l.id}:{row['id']}","category_id":row["category_id"],"score":row["score"],
                    "description":row["description"],"source":"public","outcome_key":row.get("outcome_key"),"list_version":v.revision})
        academic={}
        if b["academic_versions"]:
            av=b["academic_versions"][-1]
            for row in av["students"]:
                sid=row["student_id"]
                if sid not in inputs:continue
                if not av["confirmed"]:
                    issues[sid].append({"id":av["id"],"kind":"academic","message":"成绩单尚未由教师确认"});continue
                if any(x["category_id"]=="academic" for x in inputs[sid]):
                    issues[sid].append({"id":av["id"],"kind":"duplicate","message":"同时存在课程成绩与平均成绩名单，必须消除重复来源"});continue
                calc=academic_average(av["courses"],row["entries"]);academic[sid]=calc
                if "academic" not in by_id or children["academic"]: raise ValueError("课程成绩计算需要 academic 叶子分类。")
                inputs[sid].append({"id":"academic:"+sid,"category_id":"academic","score":calc["average"],"description":"专业加权平均成绩（按课程机械计算）","source":"academic","outcome_key":"academic"})
        result={}
        for sid in b["student_ids"]:
            keys=Counter(x["outcome_key"] for x in inputs[sid] if x.get("outcome_key"))
            for key,count in keys.items():
                if count>1:issues[sid].append({"id":key,"kind":"duplicate","message":"相同成果键重复计分；需要认定佐证/重复/替代关系"})
            calc=calculate(rules["nodes"],inputs[sid],rules["display_places"])
            result[sid]={**calc,"issues":issues[sid],"materials":material_details[sid],"public_rows":public_details[sid],"academic":academic.get(sid)}
            result[sid]["ready"]=calc["complete"] and not issues[sid]
        return {"input_hash":self.input_fingerprint(db,project),"rule_version_id":rules["id"],
            "students":result,"issue_count":sum(len(x) for x in issues.values()),
            "missing_baseline_count":sum(not r["complete"] for r in result.values()),
            "ready":bool(result) and all(r["ready"] for r in result.values())}

    def publish(self,db,p,project,payload):
        if stage(project.body) not in ("review","publication"):raise Conflict("请在提交截止并完成初步标注后公示。")
        if not project.body.get("schedule"):raise ValueError("请先设置公示申诉截止时间。")
        result=self.compute(db,project)
        pending=[a for a in project.body["appeals"] if not a["history"]]
        live=[a for a in project.body["appeals"] if a["history"] and a["history"][-1]["decision"]=="approved" and a["history"][-1].get("until") and a["history"][-1]["until"]>now() and a["history"][-1]["created_at"]>=project.body["meta"].get("workflow_restart_at","")]
        if payload.mode=="final":
            if not result["ready"] or pending or live: raise ValueError("仍有未处理材料、重复成果、缺失成绩/品德认定或未结束申请，禁止最终公示。")
            if now()<project.body["schedule"]["appeal_deadline"]:raise ValueError("申诉期未结束，不能生成最终公示。")
        snapshot={"id":uid(),"version":len(project.body["snapshots"])+1,"created_at":now(),
            "label":payload.label,"mode":payload.mode,"input_hash":result["input_hash"],
            "rule_version_id":result["rule_version_id"],"actor_id":p.user.id,
            "issue_count":result["issue_count"],"missing_baseline_count":result["missing_baseline_count"],
            "results":result["students"],"roster":self.roster(db,project.body["student_ids"])}
        body=deepcopy(project.body);body["snapshots"].append(snapshot);body["publication_id"]=snapshot["id"];body["state"]="publication"
        return self.save(db,p,project,body,payload.revision,"publication.create",{"snapshot_id":snapshot["id"],"mode":payload.mode,"input_hash":snapshot["input_hash"]})

    def personal_result(self,p,project):
        if not self.is_member(p,project): raise HTTPException(403,"非本工程参评学生。")
        snapshot=next((s for s in project.body["snapshots"] if s["id"]==project.body["publication_id"]),None)
        if not snapshot: raise HTTPException(403,"尚未公示，不能查看分数。")
        from .privacy import personal_projection
        result=personal_projection(snapshot["results"][p.user.student_id])
        return {"snapshot_id":snapshot["id"],"published_at":snapshot["created_at"],"label":snapshot["label"],
            "mode":snapshot["mode"],"version":snapshot["version"],"result":result}

    def appeal(self,db,p,project,payload):
        p.require_human()
        if not self.is_member(p,project):raise HTTPException(403,"仅参评学生可申请。")
        if stage(project.body) not in ("review","publication"):raise ValueError("请在截止后或公示期提出申请。")
        if stage(project.body)=="publication" and now()>=project.body["schedule"]["appeal_deadline"]:
            raise ValueError("公示申诉期已结束。")
        if payload.evidence_id and self.evidence(project,payload.evidence_id)["student_id"]!=p.user.student_id:
            raise ValueError("只能对自己的材料提出申请。")
        if sum(a["student_id"]==p.user.student_id for a in project.body["appeals"])>=30:raise ValueError("申请过多，请联系教师处理已有申请。")
        body=deepcopy(project.body)
        record={"id":uid(),"student_id":p.user.student_id,"evidence_id":payload.evidence_id,
            "reason":payload.reason,"created_at":now(),"history":[]}
        body["appeals"].append(record)
        self.save(db,p,project,body,project.revision,"appeal.create",{"appeal_id":record["id"],"reason":record["reason"]})
        return record

    def appeal_decide(self,db,p,project,aid,payload):
        if project.body["state"] in ("closed","archived"):raise Conflict("工程已冻结。")
        body=deepcopy(project.body);a=next((a for a in body["appeals"] if a["id"]==aid),None)
        if not a:raise HTTPException(404,"申请不存在。")
        until=as_utc(payload.until) if payload.until else None
        if payload.decision=="approved" and (not until or until<=now()):raise ValueError("批准补交须设置未来的截止时间。")
        a["history"].append({"decision":payload.decision,"note":payload.note,"until":until,"actor_id":p.user.id,"created_at":now()})
        return self.save(db,p,project,body,payload.revision,"appeal.decide",{"appeal_id":aid,"decision":payload.decision,"until":until})

    def academic_write(self,db,p,project,payload):
        if project.body["state"] in ("closed","archived"):raise Conflict("工程已冻结。")
        if not db.get(Resource,payload.resource_sha):raise ValueError("成绩单佐证资源不存在。")
        ids=[s.student_id for s in payload.students]
        if len(ids)!=len(set(ids)) or set(ids)-set(project.body["student_ids"]):raise ValueError("成绩名单重复或不属于本工程。")
        courses=[x.model_dump() for x in payload.courses]
        for row in payload.students:academic_average(courses,[x.model_dump() for x in row.entries])
        body=deepcopy(project.body)
        body["academic_versions"].append({"id":uid(),"created_at":now(),"actor_id":p.user.id,
            **payload.model_dump(exclude={"revision"})})
        return self.save(db,p,project,body,payload.revision,"academic.append",{"students":len(ids),"confirmed":payload.confirmed})


def digest_bytes(value):
    import hashlib
    return hashlib.sha256(value).hexdigest()
