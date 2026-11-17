"""Strict table diagnostics, signed web-AI interchange and all-or-nothing updates.

No model/provider is contacted. Return sheets are synthetic, deliberately include
invalid identities, conflicting versions and mixed valid/invalid batches.
"""
from copy import deepcopy
from datetime import timedelta
from io import BytesIO
import csv
import importlib.util
import json
from pathlib import Path
import uuid
from zipfile import ZipFile, ZIP_DEFLATED
import pytest
from pydantic import ValidationError
from conftest import project, upload, annotate, PASSWORD
from app.table_reader import read_table, TableError
from app.import_compiler import compile_roster, compile_academic, compile_public
from app.tabular import xlsx_bytes, csv_bytes
from app.schemas import PublicRow
from app.rules import bnu_template
from app.privacy import personal_projection


def csv_data(headers, rows):
    return csv_bytes(headers, rows)


@pytest.mark.parametrize('data,code,row,column',[
    ('学号,姓名,班级\n001,甲,一班,不能丢掉\n','EXTRA_CELL',2,'D'),
    ('学号,姓名,姓名\n001,甲,一班\n','DUPLICATE_OR_EMPTY_HEADER',1,'C'),
    ('学号,,班级\n001,甲,一班\n','DUPLICATE_OR_EMPTY_HEADER',1,'B'),
    ('学号,姓名,班级,合计分\n001,甲,一班,5\n','UNKNOWN_COLUMN',1,'D'),
    ('学号,number,姓名,班级\n001,001,甲,一班\n','DUPLICATE_FIELD',1,'B'),
    ('学号,姓名\n001,甲\n','MISSING_COLUMN',1,''),
    ('学号,姓名,班级\n001,甲,一班\n\n001,甲,一班\n','DUPLICATE_ID',4,'A'),
    ('学号,姓名,班级\n001,"甲\n甲",一班\n001,甲,一班\n','DUPLICATE_ID',4,'A'),
])
def test_compiler_preserves_coordinate(data,code,row,column):
    with pytest.raises(TableError) as err:
        compile_roster(read_table(data.encode(),'roster.csv'),{})
    diagnostics=err.value.response()
    assert diagnostics['saved'] is False
    assert any((d['code'],d['row'],d['column'])==(code,row,column) for d in diagnostics['errors']), diagnostics


def mutate_xlsx(data,part,fn):
    output=BytesIO()
    with ZipFile(BytesIO(data)) as src,ZipFile(output,'w',ZIP_DEFLATED) as target:
        for name in src.namelist():target.writestr(name,fn(src.read(name)) if name==part else src.read(name))
    return output.getvalue()


def test_xlsx_formula_rejected_with_exact_cell():
    data=xlsx_bytes([('填写数据',['学号','姓名','班级'],[['001','甲','一班']])])
    data=mutate_xlsx(data,'xl/worksheets/sheet1.xml',lambda b:b.replace(b'<c r="A2"',b'<c r="A2"').replace(b'<is><t>001</t></is>',b'<f>SUM(1,2)</f><v>3</v>'))
    # Writer may include xml:space; add directly to the A2 cell in a structural way.
    if b'<f>' not in ZipFile(BytesIO(data)).read('xl/worksheets/sheet1.xml'):
        import re
        data=mutate_xlsx(data,'xl/worksheets/sheet1.xml',lambda b:re.sub(rb'(<c\b[^>]*r="A2"[^>]*>)',rb'\1<f>1+2</f>',b))
    with pytest.raises(TableError) as err:read_table(data,'input.xlsx')
    assert any(d.code=='FORMULA_NOT_ALLOWED' and d.row==2 and d.column=='A' and d.sheet=='填写数据' for d in err.value.errors)


@pytest.mark.parametrize('kind',['macro','external','hidden','merge'])
def test_xlsx_reject_unsupported_structure(kind):
    data=xlsx_bytes([('填写数据',['学号','姓名','班级'],[['001','甲','一班']])])
    if kind in ('macro','external'):
        out=BytesIO(data)
        with ZipFile(out,'a') as z:z.writestr('xl/vbaProject.bin' if kind=='macro' else 'xl/externalLinks/link1.xml','untrusted')
        data=out.getvalue()
    elif kind=='hidden':data=mutate_xlsx(data,'xl/workbook.xml',lambda b:b.replace(b'<sheet ',b'<sheet state="hidden" ',1))
    else:data=mutate_xlsx(data,'xl/worksheets/sheet1.xml',lambda b:b.replace(b'</worksheet>',b'<mergeCells><mergeCell ref="A2:B2"/></mergeCells></worksheet>'))
    with pytest.raises(TableError):read_table(data,'input.xlsx')


def test_roster_blank_rows_and_chinese_field_labels():
    data='\n\n学号,姓名,班级\n001,错误姓名,一班\n'
    with pytest.raises(TableError) as err:compile_roster(read_table(data.encode(),'input.csv'),{'001':{'name':'原姓名'}})
    item=err.value.errors[0]
    assert (item.row,item.column,item.field,item.code)==(4,'B','姓名','IDENTITY_CONFLICT')


def test_academic_course_name_is_not_student_name_and_exclusions():
    headers=['学号','课程ID','课程名称','学分','成绩制','成绩','免计入','免计入依据']
    rows=[['001','c1','数据结构','3','numeric','91','否',''],['001','c2','两级制课程','1','two','合格','否',''],['002','c1','数据结构','3','numeric','','是','转专业认定文件']]
    table=read_table(csv_data(headers,rows),'grades.csv')
    out=compile_academic(table,{'001':{'id':'s1','name':'甲'},'002':{'id':'s2','name':'乙'}})
    assert len(out['students'])==2 and len(out['courses'])==2
    assert out['students'][1]['entries'][0]['excluded'] is True


@pytest.mark.parametrize('score',['NaN','Infinity','-1','101','1e2'])
def test_academic_invalid_numeric_coordinates(score):
    table=read_table(csv_data(['学号','课程ID','课程名称','学分','成绩制','成绩'],[['001','c','数据结构','3','numeric',score]]),'grades.csv')
    with pytest.raises(TableError) as err:compile_academic(table,{'001':{'id':'s1','name':'甲'}})
    assert any(d.row==2 and d.column=='F' for d in err.value.errors),err.value.response()


def test_academic_duplicates_conflicting_course_metadata():
    table=read_table(csv_data(['学号','课程ID','课程名称','学分','成绩制','成绩'],[['001','c','数据结构','3','numeric','90'],['001','c','数据结构','4','numeric','91']]),'grades.csv')
    with pytest.raises(TableError) as err:compile_academic(table,{'001':{'id':'s1','name':'甲'}})
    assert {'COURSE_CONFLICT','DUPLICATE_COURSE'}<={x.code for x in err.value.errors}


@pytest.mark.parametrize('extra',[{'denominator':'0'},{'denominator':'NaN'},{'factors':['-1']},{'factors':['Infinity']}])
def test_public_unused_factor_fields_still_validated(extra):
    with pytest.raises((ValueError,ValidationError)):
        PublicRow(id='r1',student_id='s1',category_id='moral',score='5',description='官方认定',scenario='基础分',rule_source='第1页',**extra)


def test_public_import_unknown_leaf_and_stamp():
    headers=['学号','分类ID','分数','描述','情形','规则出处','状态','是否有盖章','盖章上的文字']
    data=csv_data(headers,[['001','innovation','1','成果','一等奖','第1页','有效','否','不应存在的印文']])
    with pytest.raises(TableError):compile_public(read_table(data,'list.csv'),{'001':{'id':'s','name':'甲'}},bnu_template()['nodes'])


def task(actors,clock,count=2):
    p=project(actors,clock);teacher=actors['teacher'];base='/api/projects/'+p['id']
    evidence=[upload(actors['students'][0],p,81+i) for i in range(count)]
    p=teacher.ok('GET',base)
    response=teacher.request('POST',base+'/web-ai/export',{'revision':p['revision'],'evidence_ids':[e['evidence_id'] for e in evidence]})
    assert response.status_code==200,response.text
    archive=ZipFile(BytesIO(response.content));credential=archive.read('任务凭据.json')
    table=read_table(archive.read('回填模板.csv'),'response.csv')
    rows=[dict(r) for r in table]
    for index,r in enumerate(rows):r.update({'状态':'有效','材料日期':'2026-05-06','图片内容描述':f'合成天梯赛证书{index}','分类ID':'competition.professional','细则加分项':'天梯赛','具体情形':'三等奖','细则出处':'测试细则第10页','局部分数':'0.4','成果键':f'test-{index}','是否有盖章':'未判定'})
    return p,base,credential,table.headers,rows,archive


def import_task(teacher,base,credential,headers,rows,commit=False,revision=None):
    return teacher.client.post(base+'/web-ai/import',files={'credential':('任务凭据.json',credential,'application/json'),'file':('AI回填.csv',csv_data(headers,[[r.get(h,'') for h in headers] for r in rows]),'text/csv')},data={'commit':str(commit).lower(),**({'expected_revision':str(revision)} if revision is not None else {})})


def test_web_ai_roundtrip_preview_commit_idempotence_atomic(actors,clock):
    p,base,cred,headers,rows,archive=task(actors,clock)
    t=actors['teacher'];before=t.ok('GET',base)
    assert {'提示词.txt','学院辅助说明.txt','规则树.json','材料索引.csv','回填模板.xlsx','任务凭据.json'}<=set(archive.namelist())
    preview=import_task(t,base,cred,headers,rows)
    assert preview.status_code==200,preview.text
    assert preview.json()['saved'] is False and preview.json()['would_write']==2
    assert t.ok('GET',base)==before
    saved=import_task(t,base,cred,headers,rows,True,before['revision']);assert saved.status_code==200,saved.text
    current=t.ok('GET',base)
    for e in current['body']['evidence']:
        assert len(e['annotations'])==1
        assert e['annotations'][0]['reviewed'] is False and e['annotations'][0]['actor_kind']=='web_ai'
    repeat=import_task(t,base,cred,headers,rows,True,current['revision']);assert repeat.status_code==200,repeat.text
    assert repeat.json()['already_applied']==2 and repeat.json()['written']==0
    assert t.ok('GET',base)['revision']==current['revision']
    archive.close()


@pytest.mark.parametrize('mutation',['wrong_student','wrong_hash','duplicate','unknown_column','illegal_score','bad_category','bad_date','forbidden_relation','bad_stamp'])
def test_web_ai_errors_do_not_partially_save(actors,clock,mutation):
    p,base,cred,headers,rows,z=task(actors,clock);t=actors['teacher'];before=t.ok('GET',base)
    if mutation=='wrong_student':rows[1]['学生ID']='someone-else'
    if mutation=='wrong_hash':rows[1]['图片SHA256']='0'*64
    if mutation=='duplicate':rows.append(deepcopy(rows[0]))
    if mutation=='unknown_column':headers=[*headers,'人工核对'];rows[1]['人工核对']='是'
    if mutation=='illegal_score':rows[1]['局部分数']='-0.4'
    if mutation=='bad_category':rows[1]['分类ID']='innovation'
    if mutation=='bad_date':rows[1]['材料日期']='2030-01-01'
    if mutation=='forbidden_relation':rows[1]['关系类型']='replaces';rows[1]['关联材料ID']=rows[0]['材料ID']
    if mutation=='bad_stamp':rows[1]['是否有盖章']='否';rows[1]['盖章上的文字']='印文'
    response=import_task(t,base,cred,headers,rows,True,before['revision'])
    assert response.status_code==422,response.text
    body=response.json()['detail'];assert body['saved'] is False and body['errors'],body
    assert any(x['row'] in [1,2,3,4] for x in body['errors'])
    assert t.ok('GET',base)==before


def test_web_ai_signature_rules_and_expiry_fail_closed(actors,clock):
    p,base,cred,headers,rows,z=task(actors,clock);t=actors['teacher']
    bad=json.loads(cred);bad['manifest']['project_id']='other'
    assert import_task(t,base,json.dumps(bad).encode(),headers,rows).status_code==422
    current=t.ok('GET',base)
    t.ok('PUT',base+'/instructions',{'revision':current['revision'],'text':'修改后的判断依据，请重新判读','reason':'测试说明变更后的任务失效'})
    response=import_task(t,base,cred,headers,rows)
    assert response.status_code==422 and '变化' in response.text
    assert all(not e['annotations'] for e in t.ok('GET',base)['body']['evidence'])


def test_web_ai_cannot_overwrite_human_or_accept_stale_preview(actors,clock):
    p,base,cred,headers,rows,z=task(actors,clock);t=actors['teacher']
    preview=import_task(t,base,cred,headers,rows).json()
    evidence={'evidence_id':rows[1]['材料ID']};annotate(t,p,evidence)
    response=import_task(t,base,cred,headers,rows,True,preview['revision'])
    assert response.status_code in (409,422)
    records=t.ok('GET',base)['body']['evidence']
    assert len(records[0]['annotations'])==0 and len(records[1]['annotations'])==1 and records[1]['annotations'][0]['reviewed']
    export=t.request('POST',base+'/web-ai/export',{'revision':t.ok('GET',base)['revision'],'evidence_ids':[evidence['evidence_id']]})
    assert export.status_code==422


def test_web_ai_cross_row_relation_cycle_detected(actors,clock):
    p,base,cred,headers,rows,z=task(actors,clock);t=actors['teacher']
    for i,r in enumerate(rows):r.update({'局部分数':'0','关系类型':'support_for','关联材料ID':rows[1-i]['材料ID']})
    response=import_task(t,base,cred,headers,rows)
    assert response.status_code==422 and '循环' in response.text
    assert all(not e['annotations'] for e in t.ok('GET',base)['body']['evidence'])


def test_review_batch_conflict_rolls_back_whole_batch(actors,clock):
    p,base,cred,headers,rows,z=task(actors,clock);t=actors['teacher']
    payload={'revision':p['revision'],'items':[{'id':r['材料ID'],'version':0 if i==0 else 99} for i,r in enumerate(rows)],'status':'manual','note':'需补充日期','operation_id':str(uuid.uuid4())}
    response=t.request('POST',base+'/review-batch',payload);assert response.status_code==409
    assert all(not e['annotations'] for e in t.ok('GET',base)['body']['evidence'])
    payload['items'][1]['version']=0
    saved=t.ok('POST',base+'/review-batch',payload);assert saved['count']==2
    p2=t.ok('GET',base);assert p2['revision']==p['revision']+1
    for e in p2['body']['evidence']:
        a=e['annotations'][0];assert a['data']['status']=='manual' and a['reviewed']
    assert t.ok('POST',base+'/review-batch',payload)['count']==0


def test_batch_valid_does_not_create_scores(actors,clock):
    p,base,cred,headers,rows,z=task(actors,clock);t=actors['teacher']
    response=t.request('POST',base+'/review-batch',{'revision':p['revision'],'items':[{'id':r['材料ID'],'version':0} for r in rows],'status':'valid','note':'不能一键捏造评分','operation_id':str(uuid.uuid4())})
    assert response.status_code==422
    assert all(not e['annotations'] for e in t.ok('GET',base)['body']['evidence'])


def test_roster_preview_fingerprint_and_batch_session_revoke(actors):
    t=actors['teacher'];content='学号,姓名,班级\n001,测试甲,新班级\n'.encode()
    preview=t.client.post('/api/tables/validate/students',files={'file':('名册.csv',content)});assert preview.status_code==200,preview.text
    rows=t.ok('GET','/api/students');first=rows[0]
    items=[{k:s[k] for k in ('id','name','class_name','disabled')} for s in rows[:2]]
    bad=deepcopy(items);bad[1]['class_name']='旧版班级'
    response=t.request('POST','/api/students/batch',{'action':'disable','items':bad});assert response.status_code==409
    assert t.ok('GET','/api/students')==rows
    t.ok('POST','/api/students/batch',{'action':'disable','items':items})
    assert actors['students'][0].request('GET','/api/auth/me').status_code==401
    response=t.client.post('/api/students/import-file',files={'file':('名册.csv',content)},data={'fingerprint':preview.json()['fingerprint']})
    assert response.status_code==409,response.text
    assert t.ok('GET','/api/students')[0]['class_name']==first['class_name']


def test_project_batch_atomic_and_restore(actors,clock):
    t=actors['teacher'];p1=project(actors,clock);p2=project(actors,clock)
    items=[{'id':p['id'],'revision':p['revision']} for p in (p1,p2)]
    bad=deepcopy(items);bad[1]['revision']-=1
    assert t.request('POST','/api/projects/batch',{'items':bad,'action':'trash'}).status_code==409
    assert t.ok('GET','/api/projects/'+p1['id'])['revision']==p1['revision']
    assert t.ok('POST','/api/projects/batch',{'items':items,'action':'trash'})['count']==2
    for item in items:item['revision']+=1
    assert t.ok('POST','/api/projects/batch',{'items':items,'action':'restore'})['count']==2
    assert t.ok('GET','/api/projects/'+p1['id'])['revision']==p1['revision']+2


def test_student_public_notes_private_api_and_exports(actors,clock):
    t=actors['teacher'];student=actors['students'][0];p=project(actors,clock);base='/api/projects/'+p['id'];e=upload(student,p,145)
    annotate(t,p,e,ai_note='INTERNAL_AI_SECRET',human_note='INTERNAL_HUMAN_SECRET',student_note='PUBLIC_EXPLANATION_2026')
    assert student.request('GET',base+'/my-result').status_code==403
    clock[0]+=timedelta(hours=2);p=t.ok('GET',base)
    t.ok('POST',base+'/publish',{'revision':p['revision'],'mode':'provisional','label':'说明字段测试'})
    result=student.ok('GET',base+'/my-result');text=json.dumps(result)
    assert 'INTERNAL_' not in text and 'PUBLIC_EXPLANATION_2026' in text
    response=student.request('GET',base+'/export/student/'+student.me['student_id']+'/xlsx')
    assert response.status_code==200,response.text
    with ZipFile(BytesIO(response.content)) as z:
        xml=b'\n'.join(z.read(n) for n in z.namelist() if n.endswith('.xml'))
        assert b'INTERNAL_' not in xml and b'PUBLIC_EXPLANATION_2026' in xml
    response=student.request('GET',base+'/export/student/'+student.me['student_id']);assert response.status_code==200
    import fitz
    with fitz.open(stream=response.content,filetype='pdf') as pdf:
        text=''.join(page.get_text() for page in pdf)
        assert 'INTERNAL_' not in text and 'PUBLIC_EXPLANATION_2026' in text


def test_future_private_fields_do_not_leak():
    source={'total':'1','root':'r','future_secret':'private','materials':[{'id':'e','secret':'x','annotation':{'status':'valid','score':'1','human_note':'PRIVATE','ai_note':'PRIVATE','future_field':'PRIVATE','student_note':'可见说明'}}], 'public_rows':[{'id':'r','note':'PRIVATE','student_id':'s','future_secret':'PRIVATE'}]}
    result=personal_projection(source)
    assert 'PRIVATE' not in json.dumps(result) and 'private' not in json.dumps(result)
    assert result['root']=='r'
    result['materials'][0]['annotation']['student_note']='changed';assert source['materials'][0]['annotation']['student_note']=='可见说明'


def agent_module():
    path=Path(__file__).parents[1]/'agent/clearledger/scripts/zc_agent.py'
    spec=importlib.util.spec_from_file_location('agent_rc4',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


@pytest.mark.parametrize('change',['rules','supplementary_instructions','source_documents','meta','student','sha','version','human'])
def test_agent_harness_pins_judged_contract(change):
    ctx={'project_id':'p','rules':{'id':'r1'},'supplementary_instructions':{'id':'i1'},'source_documents':[], 'meta':{'assessment_start':'2025-09-01'},'queue':[{'id':'e','student_id':'s','resource_sha':'a'*64,'annotation_version':0,'latest':None}]}
    newer=deepcopy(ctx)
    if change in ('rules','supplementary_instructions','source_documents','meta'):newer[change]={'changed':True}
    elif change=='student':newer['queue'][0]['student_id']='s2'
    elif change=='sha':newer['queue'][0]['resource_sha']='b'*64
    elif change=='version':newer['queue'][0]['annotation_version']=1
    else:newer['queue'][0]['latest']={'reviewed':True}
    with pytest.raises(ValueError):agent_module().check_context(ctx,newer,'e','p','retry-key')


def test_agent_harness_only_refreshes_unrelated_revision():
    ctx={'project_id':'p','revision':3,'rules':{'id':'r'},'queue':[{'id':'e','student_id':'s','resource_sha':'a'*64,'annotation_version':0,'latest':None}]}
    newer=deepcopy(ctx);newer['revision']=8
    assert agent_module().check_context(ctx,newer,'e','p','key')==ctx['queue'][0]


def test_web_ai_expired_and_new_document_contract(actors,clock,settings):
    from app.offline_tasks import _sign
    from datetime import datetime,timezone
    p,base,cred,headers,rows,z=task(actors,clock);t=actors['teacher']
    expired=json.loads(cred);expired['manifest']['expires_at']=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
    expired['signature']=_sign(settings,expired['manifest'])
    response=import_task(t,base,json.dumps(expired).encode(),headers,rows)
    assert response.status_code==422 and '过期' in response.text
    p=t.ok('GET',base)
    added=t.client.post(base+'/documents',files={'file':('补充细则.pdf',b'%PDF-1.4\nadditional-rules\n%%EOF','application/pdf')},data={'purpose':'rules','revision':str(p['revision'])})
    assert added.status_code==200,added.text
    response=import_task(t,base,cred,headers,rows)
    assert response.status_code==422 and '来源文件已变化' in response.text
    assert all(not e['annotations'] for e in t.ok('GET',base)['body']['evidence'])


def test_web_ai_cross_project_student_and_agent_forbidden(actors,clock,app):
    from fastapi.testclient import TestClient
    p,base,cred,headers,rows,z=task(actors,clock);t=actors['teacher']
    for student in actors['students']:
        assert student.request('POST',base+'/web-ai/export',{'revision':p['revision'],'evidence_ids':[rows[0]['材料ID']]}).status_code==403
    other=project(actors,clock)
    assert import_task(t,'/api/projects/'+other['id'],cred,headers,rows).status_code==422
    token=t.ok('POST','/api/agent-tokens',{'project_id':p['id'],'name':'受限外部服务','scopes':['context:read','annotation:write','evidence:read'],'hours':1})
    client=TestClient(app);client.headers['Authorization']='Bearer '+token['token']
    response=client.post(base+'/web-ai/export',json={'revision':p['revision'],'evidence_ids':[rows[0]['材料ID']]})
    assert response.status_code==403


def test_appeal_batch_pending_only_atomic(actors,clock):
    p=project(actors,clock);base='/api/projects/'+p['id'];t=actors['teacher']
    clock[0]+=timedelta(hours=2)
    a1=actors['students'][0].ok('POST',base+'/appeals',{'reason':'missing_evidence'})
    a2=actors['students'][1].ok('POST',base+'/appeals',{'reason':'missing_evidence'})
    p=t.ok('GET',base);payload={'revision':p['revision'],'ids':[a1['id'],a2['id']],'decision':'approved','note':'同意补充原始证明','until':(clock[0]+timedelta(hours=1)).isoformat()}
    bad={**payload,'ids':[a1['id'],'unknown']}
    assert t.request('POST',base+'/appeals/batch',bad).status_code==409
    assert all(not a['history'] for a in t.ok('GET',base)['body']['appeals'])
    result=t.ok('POST',base+'/appeals/batch',payload);assert result['count']==2
    payload['revision']=result['revision']
    assert t.request('POST',base+'/appeals/batch',payload).status_code==409
    assert all(len(a['history'])==1 for a in t.ok('GET',base)['body']['appeals'])


def test_review_batch_self_review_rolls_back_all(actors,clock):
    p=project(actors,clock);t=actors['teacher'];base='/api/projects/'+p['id'];own=upload(actors['students'][0],p,121);other=upload(actors['students'][1],p,122)
    p=t.ok('GET',base);p=t.ok('PUT',base+'/members',{'revision':p['revision'],'student_ids':p['body']['student_ids'],'reviewer_ids':[actors['students'][0].me['id']]})
    result=actors['students'][0].request('POST',base+'/review-batch',{'revision':p['revision'],'items':[{'id':other['evidence_id'],'version':0},{'id':own['evidence_id'],'version':0}],'status':'manual','note':'自审不允许','operation_id':str(uuid.uuid4())})
    assert result.status_code==403
    assert all(not e['annotations'] for e in t.ok('GET',base)['body']['evidence'])


def test_bound_public_metadata_uses_fixed_version(actors,clock):
    t=actors['teacher'];p=project(actors,clock);base='/api/projects/'+p['id']
    source=t.client.post('/api/uploads',files={'file':('名单.pdf',b'%PDF-1.4\npublic\n%%EOF','application/pdf')}).json()
    body={'resource_sha':source['sha256'],'source_name':'名单.pdf','rows':[],'approved':True}
    listing=t.ok('POST','/api/public-lists',{'revision':0,'name':'第一版奖项名','body':body})
    t.ok('POST',base+'/public-bindings',{'revision':p['revision'],'list_id':listing['id'],'list_revision':1})
    t.ok('PUT','/api/public-lists/'+listing['id'],{'revision':1,'name':'后来修改的名字','body':body})
    assert t.ok('GET',base+'/bound-public-metadata')==[{'id':listing['id'],'revision':1,'name':'第一版奖项名'}]


def test_storage_never_overwrites_corrupt_existing_object(app,actors,clock):
    p=project(actors,clock);e=upload(actors['students'][0],p,123);store=app.state.domain.store;path=store.path(e['resource_sha'])
    path.chmod(0o600);path.write_bytes(b'CORRUPTED')
    from app.storage import InvalidFile
    from conftest import png
    with app.state.factory() as db:
        with pytest.raises(InvalidFile):store.put(db,png(123),'image')
    assert path.read_bytes()==b'CORRUPTED'
    response=actors['teacher'].request('GET','/api/projects/'+p['id']+'/export/materials')
    assert response.status_code==422,response.text


def test_windows_file_durability_does_not_open_directory(app,monkeypatch):
    import app.storage as storage
    import types,os
    from conftest import png
    proxy=types.SimpleNamespace(**{name:getattr(os,name) for name in dir(os) if not name.startswith('__')})
    proxy.name='nt';calls=[];real_fsync=os.fsync
    proxy.fsync=lambda fd:(calls.append(fd),real_fsync(fd))[1]
    proxy.open=lambda *a,**kw:(_ for _ in ()).throw(AssertionError('Do not fsync a Windows directory'))
    monkeypatch.setattr(storage,'os',proxy)
    with app.state.factory() as db:r=app.state.domain.store.put(db,png(197),'image');db.commit()
    assert len(calls)==1 and app.state.domain.store.path(r['sha256']).is_file()


@pytest.mark.parametrize('name,route',[('使用手册','/manual'),('图文操作教程','/tutorial'),('外部AI接入与表格回填指南','/ai-guide')])
def test_served_document_matches_distributed_document(app,name,route):
    from fastapi.testclient import TestClient
    with TestClient(app) as client:
        response=client.get(route)
        assert response.status_code==200
        assert response.text==(Path(__file__).parents[1]/'docs'/(name+'.html')).read_text()
        assert '1.0.0rc4' in response.text and '1.0.0rc3' not in response.text


def test_no_duplicate_frontend_function_declarations_and_agent_context_required():
    import re
    root=Path(__file__).parents[1]
    files=['app.js','review-ui.js','workflow-ui.js']
    functions=[]
    for name in files:
        functions.extend(re.findall(r'^(?:async )?function\s+([A-Za-z_$][\w$]*)\s*\(', (root/'app/static'/name).read_text(), re.M))
    assert len(functions)==len(set(functions)), 'Refactored handlers must not leave shadowed function declarations'
    skill=(root/'agent/clearledger/SKILL.md').read_text()
    assert 'annotate <evidence-id> annotation.json --context context.json --key' in skill
    assert (root/'agent/clearledger/references/workflows.md').is_file()
