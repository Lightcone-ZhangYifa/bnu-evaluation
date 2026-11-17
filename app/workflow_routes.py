"""Batch and interchange endpoints built on the existing permission/domain layer.

All write batches commit once. Export, parsing and validation are read-only apart
from an audit entry; no endpoint accepts totals or publication results as input.
"""
from copy import deepcopy
from typing import Annotated, Literal
from datetime import datetime
import hashlib
from fastapi import Depends, Request, UploadFile, File, Form, HTTPException
from pydantic import Field
from sqlalchemy import select, update, delete
from .db import Student, Account, LoginSession, Project, now
from .schemas import Strict, Revision, Annotation, AnnotationWrite
from .domain import Conflict, active_rules, as_utc, object_dict
from .security import audit
from .rules import bnu_template
from .table_reader import read_table
from .import_compiler import compile_roster, compile_public, compile_academic, roster_fingerprint
from .offline_tasks import build_task, verify_task, compile_task
from .reporting import report_context, records_sheet, XLSX_TYPE, descendants
from .tabular import xlsx_bytes


class TaskSelection(Revision):
    evidence_ids: list[str] = Field(min_length=1, max_length=2000)


class EvidenceVersion(Strict):
    id: str
    version: int = Field(ge=0)


class ReviewBatch(Revision):
    items: list[EvidenceVersion] = Field(min_length=1, max_length=2000)
    status: Literal['valid','manual','untrusted','invalid_image','out_of_scope','out_of_period','system_error']
    note: str = Field(min_length=1, max_length=4000)
    operation_id: str = Field(min_length=8,max_length=60)


class StudentVersion(Strict):
    id: str
    name: str
    class_name: str
    disabled: bool


class StudentBatch(Strict):
    items: list[StudentVersion] = Field(min_length=1, max_length=2000)
    action: Literal['enable','disable','class']
    class_name: str = Field(default='',max_length=100)


class ProjectVersion(Strict):
    id: str
    revision: int = Field(ge=1)


class ProjectBatch(Strict):
    items: list[ProjectVersion] = Field(min_length=1,max_length=2000)
    action: Literal['move','trash','restore']
    folder_id: str | None = None


class AppealBatch(Revision):
    ids: list[str] = Field(min_length=1,max_length=2000)
    decision: Literal['approved','rejected']
    note: str = Field(min_length=1,max_length=4000)
    until: datetime | None = None


def unique(values):
    if len(values)!=len(set(values)):raise ValueError('所选记录重复，请刷新选择后重试。')


def register_workflow_routes(app, database, principal, domain, settings, store, download):
    DB=Annotated[object,Depends(database)]

    async def read(file,limit=10*1024*1024):
        data=await file.read(limit+1)
        if len(data)>limit:raise HTTPException(413,'文件超过大小限制。')
        store.scan(data)
        return data

    def teacher(request,db):
        p=principal(request,db);p.require_teacher();return p

    @app.post('/api/tables/validate/students',tags=['表格校验'])
    async def roster_preflight(request:Request,db:DB,file:UploadFile=File(...)):
        p=teacher(request,db);data=await read(file)
        current={s.number:object_dict(s,'number','name') for s in db.scalars(select(Student).where(Student.college_id==p.user.college_id)).all()}
        rows=compile_roster(read_table(data,file.filename or ''),current)
        return {'valid':True,'saved':False,'rows':[r.model_dump() for r in rows],
                'new_count':sum(r.number not in current for r in rows),'matched_count':sum(r.number in current for r in rows),
                'file_sha256':hashlib.sha256(data).hexdigest(),
                'fingerprint':roster_fingerprint(db,p.user.college_id,{r.number for r in rows},hashlib.sha256(data).hexdigest())}

    @app.post('/api/public-lists/import-rows',tags=['表格校验'])
    async def public_rows(request:Request,db:DB,file:UploadFile=File(...),project_id:str|None=Form(None)):
        p=teacher(request,db);pr=domain.project(db,p,project_id,review=True) if project_id else None
        nodes=active_rules(pr.body)['nodes'] if pr else bnu_template()['nodes']
        roster={s.number:object_dict(s,'id','name','number') for s in db.scalars(select(Student).where(Student.college_id==p.user.college_id)).all()}
        data=await read(file);rows=compile_public(read_table(data,file.filename or ''),roster,nodes,pr.body['meta'] if pr else None)
        return {'valid':True,'saved':False,'rows':rows,'count':len(rows),'file_sha256':hashlib.sha256(data).hexdigest()}

    @app.post('/api/projects/{pid}/academic/import-rows',tags=['表格校验'])
    async def academic_rows(pid:str,request:Request,db:DB,file:UploadFile=File(...)):
        p=teacher(request,db);pr=domain.project(db,p,pid,owner=True)
        roster={s['number']:s for s in domain.roster(db,pr.body['student_ids'])}
        data=await read(file);payload=compile_academic(read_table(data,file.filename or ''),roster)
        return {'valid':True,'saved':False,**payload,'revision':pr.revision,'file_sha256':hashlib.sha256(data).hexdigest()}

    @app.post('/api/projects/{pid}/web-ai/export',tags=['网页AI表格任务'])
    def export_task(pid:str,payload:TaskSelection,request:Request,db:DB):
        p=teacher(request,db);pr=domain.project(db,p,pid,review=True)
        if pr.revision!=payload.revision:raise Conflict('工程已变化；请刷新后重新选择材料。')
        data,envelope=build_task(db,domain,pr,payload.evidence_ids)
        audit(db,settings,p,pid,'web-ai.export',{'task_id':envelope['manifest']['task_id'],'count':len(payload.evidence_ids)})
        db.commit()
        return download(data,'网页AI标注任务-'+envelope['manifest']['task_id']+'.zip','application/zip')

    @app.post('/api/projects/{pid}/web-ai/import',tags=['网页AI表格任务'])
    async def import_task(pid:str,request:Request,db:DB,credential:UploadFile=File(...),file:UploadFile=File(...),
                          commit:bool=Form(False),expected_revision:int|None=Form(None)):
        p=teacher(request,db);pr=domain.project(db,p,pid,review=True)
        manifest=verify_task(settings,await read(credential,3*1024*1024),pr)
        data=await read(file)
        items=compile_task(read_table(data,file.filename or ''),manifest,domain,p,pr)
        # Validation and commit use the same domain operations. On the confirmation
        # request every constraint is checked again; a cached preview is not authority.
        if commit and expected_revision!=pr.revision:
            raise Conflict('校验后工程发生变化；尚未写入，请重新校验并确认。')
        pr,changes=domain.annotation_batch(db,p,pr,items,pr.revision,source='web_ai',dry_run=not commit)
        if commit:
            audit(db,settings,p,pid,'web-ai.import',{'task_id':manifest['task_id'],'rows':len(items),'written':len(changes),'file_sha256':hashlib.sha256(data).hexdigest()})
            db.commit()
        return {'valid':True,'saved':commit,'revision':pr.revision,'count':len(items),'written':len(changes) if commit else 0,
                'would_write':len(changes),'already_applied':len(items)-len(changes),
                'file_sha256':hashlib.sha256(data).hexdigest(),
                'preview':[{'id':eid,'status':v.annotation.status,'score':v.annotation.score,'description':v.annotation.description,'reviewed':False} for eid,v in items]}

    @app.post('/api/projects/{pid}/review-batch',tags=['批量操作'])
    def batch_review(pid:str,payload:ReviewBatch,request:Request,db:DB):
        p=principal(request,db);p.require_human();pr=domain.project(db,p,pid,review=True)
        unique([x.id for x in payload.items]);items=[]
        if not payload.note.strip():raise ValueError('批量核对必须填写明确原因。')
        for item in payload.items:
            e=domain.evidence(pr,item.id);latest=e['annotations'][-1] if e['annotations'] else None
            if payload.status=='valid' and (not latest or latest['data']['status']!='valid'):
                raise ValueError('批量确认有效仅用于已经逐项填写完整且结论为有效的材料；其他材料请逐张核对填写分值。')
            data=deepcopy(latest['data']) if latest else {'status':'unlabeled','score':'0'}
            data['status']=payload.status;data['human_note']=payload.note
            if payload.status!='valid':data.update(score='0',base_score=None,factors=[],denominator='1')
            items.append((item.id,AnnotationWrite(revision=payload.revision,expected_annotation_version=item.version,
                annotation=Annotation.model_validate(data),idempotency_key=payload.operation_id+':'+item.id)))
        pr,changes=domain.annotation_batch(db,p,pr,items,payload.revision)
        db.commit()
        return {'revision':pr.revision,'count':len(changes)}

    @app.post('/api/students/batch',tags=['批量操作'])
    def batch_students(payload:StudentBatch,request:Request,db:DB):
        p=teacher(request,db);unique([i.id for i in payload.items])
        if payload.action=='class' and not payload.class_name.strip():raise ValueError('请填写目标班级名称。')
        changes=[]
        for i in payload.items:
            s=db.scalar(select(Student).where(Student.id==i.id,Student.college_id==p.user.college_id))
            a=db.scalar(select(Account).where(Account.student_id==i.id)) if s else None
            if not s or not a:raise HTTPException(404,'所选学生不存在或无权限。')
            if (s.name,s.class_name,a.disabled)!=(i.name,i.class_name,i.disabled):raise Conflict('学生资料已变化；所有选中项均未更新，请刷新。')
            changes.append((i,s,a))
        for i,s,a in changes:
            disabled=payload.action=='disable' if payload.action!='class' else i.disabled
            class_name=payload.class_name.strip() if payload.action=='class' else i.class_name
            count=db.execute(update(Student).where(Student.id==i.id,Student.name==i.name,Student.class_name==i.class_name,Student.active==(not i.disabled))
                .values(class_name=class_name,active=not disabled).execution_options(synchronize_session=False)).rowcount
            count2=db.execute(update(Account).where(Account.id==a.id,Account.disabled==i.disabled)
                .values(disabled=disabled).execution_options(synchronize_session=False)).rowcount
            if not count or not count2:raise Conflict('并发修改冲突，整个批次未保存。')
            if disabled:db.execute(delete(LoginSession).where(LoginSession.account_id==a.id))
        audit(db,settings,p,p.user.college_id,'students.batch',{'action':payload.action,'ids':[i.id for i in payload.items],'class_name':payload.class_name if payload.action=='class' else None})
        db.commit();return {'count':len(changes)}

    @app.post('/api/projects/batch',tags=['批量操作'])
    def batch_projects(payload:ProjectBatch,request:Request,db:DB):
        p=teacher(request,db);unique([i.id for i in payload.items])
        if payload.action=='move':domain.check_folder(db,p,payload.folder_id)
        projects=[]
        for item in payload.items:
            pr=db.get(Project,item.id)
            if not pr or pr.owner_id!=p.user.id:raise HTTPException(403,'只有本人负责的工程才可批量移动或回收。')
            if pr.revision!=item.revision:raise Conflict('所选工程版本已变化；全部未保存。')
            if (payload.action=='restore')!=bool(pr.trashed):raise ValueError('恢复仅用于回收站工程，其他操作仅用于正常工程。')
            projects.append((item,pr))
        for item,pr in projects:
            metadata={'folder_id':payload.folder_id} if payload.action=='move' else {'trashed':True} if payload.action=='trash' else {'trashed':False,'folder_id':None}
            domain.save(db,p,pr,deepcopy(pr.body),item.revision,'file.batch.'+payload.action,{},**metadata)
        db.commit();return {'count':len(projects)}

    @app.post('/api/projects/{pid}/appeals/batch',tags=['批量操作'])
    def batch_appeals(pid:str,payload:AppealBatch,request:Request,db:DB):
        p=teacher(request,db);pr=domain.project(db,p,pid,owner=True);unique(payload.ids)
        if pr.body['state'] in ('closed','archived'):raise Conflict('工程已冻结。')
        until=as_utc(payload.until) if payload.until else None
        if payload.decision=='approved' and (not until or until<=now()):raise ValueError('批准补交须填写未来截止时间。')
        body=deepcopy(pr.body);lookup={a['id']:a for a in body['appeals']}
        for aid in payload.ids:
            if aid not in lookup or lookup[aid]['history']:raise Conflict('仅可批量处理仍未处理的申请；有记录已变化，请刷新。')
        for aid in payload.ids:lookup[aid]['history'].append({'decision':payload.decision,'note':payload.note,'until':until,'actor_id':p.user.id,'created_at':now()})
        domain.save(db,p,pr,body,payload.revision,'appeals.batch',{'ids':payload.ids,'decision':payload.decision,'until':until})
        db.commit();return {'revision':pr.revision,'count':len(payload.ids)}

    @app.get('/api/projects/{pid}/bound-public-metadata',tags=['明细跳转'])
    def bound_public_metadata(pid:str,request:Request,db:DB):
        p=teacher(request,db);pr=domain.project(db,p,pid,owner=True)
        return [{'id':l.id,'revision':v.revision,'name':v.body.get('name_at_version') or l.name} for l,v in domain.public_versions(db,pr)]

    @app.get('/api/projects/{pid}/achievement-records',tags=['明细跳转'])
    def achievement_records(pid:str,request:Request,db:DB,student_id:str|None=None,category_id:str|None=None):
        p=principal(request,db);p.require_human();pr=domain.project(db,p,pid,review=True)
        if p.user.student_id and student_id==p.user.student_id:raise HTTPException(403,'自己的结果只能在个人公示中查看。')
        computed,roster,nodes,records=report_context(db,domain,pr,student_id=student_id)
        chosen=descendants(nodes,category_id)
        rows=[r for sid,rs in records.items() if sid!=p.user.student_id for r in rs if not category_id or r['category_id'] in chosen]
        return {'revision':pr.revision,'records':rows,'nodes':nodes}

    class SelectedRecords(Strict):
        ids:list[str]=Field(min_length=1,max_length=20000)

    @app.post('/api/projects/{pid}/export/selected-achievements',tags=['选择导出'])
    def selected_achievements(pid:str,payload:SelectedRecords,request:Request,db:DB):
        p=teacher(request,db);pr=domain.project(db,p,pid,review=True);unique(payload.ids)
        _,_,_,records=report_context(db,domain,pr)
        lookup={r['id']:r for rs in records.values() for r in rs}
        if set(payload.ids)-lookup.keys():raise HTTPException(404,'部分所选成果不存在。')
        data=xlsx_bytes([records_sheet([lookup[x] for x in payload.ids])])
        audit(db,settings,p,pid,'export.selected-achievements',{'count':len(payload.ids)})
        db.commit();return download(data,'所选成果.xlsx',XLSX_TYPE)
