"""Security/integrity regression tests, all fixtures are synthetic."""
from copy import deepcopy
from io import BytesIO
from datetime import timedelta
import json, uuid, zipfile
import pytest
from sqlalchemy import text
from fastapi.testclient import TestClient
from conftest import API, project, upload, annotate, png
from app.portable import verify_archive


def public(t,p,sid,score='1',category='activities.sports'):
    src=t.client.post('/api/uploads',files={'file':('roster.pdf',b'%PDF-1.4\nlist\n%%EOF','application/pdf')}).json()
    return t.ok('POST','/api/public-lists',dict(revision=0,name='唯一名单',body=dict(resource_sha=src['sha256'],source_name='roster.pdf',approved=True,rows=[dict(id=str(uuid.uuid4()),student_id=sid,category_id=category,score=score,description='合成名单',scenario='测试情形',rule_source='测试规则',material_date='2026-08-31',outcome_key='sport')])) )


def test_csrf_origin_mandatory_and_safe_headers(actors):
    t=actors['teacher'];path='/api/folders';data={'name':'x'}
    assert t.request('POST',path,data,headers={'X-CSRF-Token':'incorrect'}).status_code==403
    assert t.request('POST',path,data,headers={'Origin':'https://attacker.invalid'}).status_code==403
    r=t.request('GET','/api/students')
    assert r.headers['x-content-type-options']=='nosniff'
    assert "frame-ancestors 'none'" in r.headers['content-security-policy']
    assert 'no-store' in r.headers['cache-control']


def test_admin_separation_permanence_and_no_registration(actors,app):
    a=actors['admin'];t=actors['teacher'];s=actors['students'][0]
    assert a.request('GET','/api/projects').status_code==403
    assert t.request('GET','/api/ops/accounts').status_code==403
    assert s.request('POST','/api/students/import',{'rows':[dict(number='9',name='x',class_name='x')]}).status_code==403
    assert a.request('PATCH','/api/ops/accounts/'+a.me['id'],{'disabled':True}).status_code in (403,422)
    assert a.request('POST','/api/auth/register',{}).status_code==404
    with app.state.engine.begin() as db:
        with pytest.raises(Exception):db.execute(text("DELETE FROM accounts WHERE role='admin'"))
    assert a.ok('GET','/api/auth/me')['role']=='admin'


def test_cross_college_and_unassigned_teacher_isolation(actors,clock,app):
    p=project(actors,clock);t=actors['teacher'];a=actors['admin'];s=actors['students'][0];e=upload(s,p,41)
    c=a.ok('POST','/api/ops/colleges',{'name':'另一个学院'})
    ac=a.ok('POST','/api/ops/teachers',dict(college_id=c['id'],username='other',name='外院教师'))
    other=API(app,'teacher','other',ac['initial_password'])
    assert other.ok('GET','/api/projects')==[]
    assert other.request('GET','/api/projects/'+p['id']).status_code==404
    assert other.request('GET','/api/resources/'+e['resource_sha']).status_code==404
    assert other.ok('GET','/api/students')==[]
    ac=a.ok('POST','/api/ops/teachers',dict(college_id=actors['college']['id'],username='colleague',name='未授权同院教师'))
    colleague=API(app,'teacher','colleague',ac['initial_password'])
    assert colleague.request('GET','/api/projects/'+p['id']).status_code==404


def test_revision_conflict_does_not_silently_overwrite(actors,clock):
    p=project(actors,clock);t=actors['teacher'];path='/api/projects/'+p['id']+'/instructions'
    v=t.ok('PUT',path,dict(revision=p['revision'],text='解释一',reason='明确范围'))
    assert t.request('PUT',path,dict(revision=p['revision'],text='旧界面解释二',reason='竞争写入')).status_code==409
    current=t.ok('GET','/api/projects/'+p['id'])
    assert current['body']['instruction_versions'][-1]['text']=='解释一'


def test_members_cannot_remove_submitted_student(actors,clock):
    p=project(actors,clock);t=actors['teacher'];s=actors['students'][0];upload(s,p,42)
    p=t.ok('GET','/api/projects/'+p['id'])
    assert t.request('PUT','/api/projects/'+p['id']+'/members',dict(revision=p['revision'],student_ids=[actors['roster'][1]['id']],reviewer_ids=[])).status_code==422


def test_public_roster_versions_are_pinned_and_archived(actors,clock):
    p=project(actors,clock);t=actors['teacher'];sid=actors['roster'][0]['id'];base='/api/projects/'+p['id'];l=public(t,p,sid)
    p=t.ok('POST',base+'/public-bindings',dict(revision=p['revision'],list_id=l['id'],list_revision=1))
    data=deepcopy(l);data.pop('id');data['body']['rows'][0]['score']='2'
    l2=t.ok('PUT','/api/public-lists/'+l['id'],data)
    assert t.ok('GET',base+'/calculation')['students'][sid]['total']=='1'
    p=t.ok('POST',base+'/public-bindings',dict(revision=p['revision'],list_id=l['id'],list_revision=2))
    assert t.ok('GET',base+'/calculation')['students'][sid]['total']=='2'
    z,m,payload=verify_archive(t.request('GET',base+'/export/project').content)
    assert len(payload['public_versions'])==2
    assert payload['public_versions'][0]['body']['rows'][0]['score']=='1'
    assert payload['public_versions'][1]['body']['rows'][0]['score']=='2'


def test_rule_semantics_stales_annotations_but_cap_only_does_not(actors,clock):
    p=project(actors,clock);t=actors['teacher'];e=upload(actors['students'][0],p,43);annotate(t,p,e);base='/api/projects/'+p['id']
    p=t.ok('GET',base);rules={k:deepcopy(p['body']['rule_versions'][-1][k]) for k in ('nodes','display_places','explanation')}
    next(n for n in rules['nodes'] if n['id']=='innovation')['cap']='6'
    p=t.ok('PUT',base+'/rules',dict(revision=p['revision'],rules=rules,reason='修改全局封顶'))
    assert t.ok('GET',base+'/calculation')['issue_count']==0
    p=t.ok('PUT',base+'/instructions',dict(revision=p['revision'],text='新的学院认定解释',reason='解释变化'))
    assert t.ok('GET',base+'/calculation')['issue_count']>0


@pytest.mark.parametrize('case',['tamper','traversal','symlink'])
def test_portable_archive_rejects_unsafe_inputs(actors,clock,case):
    p=project(actors,clock);t=actors['teacher'];data=t.request('GET','/api/projects/'+p['id']+'/export/project').content
    original=zipfile.ZipFile(BytesIO(data));out=BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        for n in original.namelist():
            v=original.read(n)
            if case=='tamper' and n=='project.json':v+=b' '
            z.writestr(n,v)
        if case=='traversal':z.writestr('../escape',b'bad')
        if case=='symlink':
            info=zipfile.ZipInfo('symlink');info.create_system=3;info.external_attr=(0o120777<<16);z.writestr(info,'/etc/passwd')
    r=t.client.post('/api/projects-import',files={'file':('bad.zcp',out.getvalue(),'application/zip')})
    assert r.status_code==422,r.text


def test_feedback_is_only_visible_to_admin(actors):
    s=actors['students'][0];t=actors['teacher'];a=actors['admin']
    r=s.client.post('/api/feedback',data={'text':'中文反馈：上传流程'},files={'files':('screen.png',png(55),'image/png')})
    assert r.status_code==200,r.text
    assert t.request('GET','/api/ops/feedback').status_code==403
    assert s.request('GET','/api/ops/feedback').status_code==403
    records=a.ok('GET','/api/ops/feedback');assert '中文反馈' in json.dumps(records,ensure_ascii=False)


def test_folder_cycle_and_file_recycle_copy_are_controlled(actors,clock):
    t=actors['teacher'];p=project(actors,clock)
    a=t.ok('POST','/api/folders',{'name':'年度目录'});b=t.ok('POST','/api/folders',{'name':'班级目录','parent_id':a['id']})
    assert t.request('PATCH','/api/folders/'+a['id'],{'name':'年度目录','parent_id':b['id']}).status_code==422
    base='/api/projects/'+p['id'];p=t.ok('POST',base+'/file-action',dict(revision=p['revision'],action='move',folder_id=b['id']))
    assert t.request('PATCH','/api/folders/'+b['id'],{'name':'班级目录','parent_id':a['id'],'trashed':True}).status_code==422
    p=t.ok('POST',base+'/file-action',dict(revision=p['revision'],action='trash'))
    assert t.request('GET',base).status_code==404
    p=t.ok('POST',base+'/file-action',dict(revision=p['revision'],action='restore'))
    copied=t.ok('POST',base+'/file-action',dict(revision=p['revision'],action='copy',title='复核副本'))
    assert copied['id']!=p['id'] and copied['body']['publication_id'] is None


def test_required_baselines_block_final_and_test_flag_preserved(actors,clock):
    t=actors['teacher'];p=project(actors,clock);base='/api/projects/'+p['id'];clock[0]+=timedelta(hours=5)
    r=t.request('POST',base+'/publish',dict(revision=p['revision'],mode='final',label='数据不全'))
    assert r.status_code==422
    calc=t.ok('GET',base+'/calculation')
    assert calc['students'][actors['roster'][0]['id']]['missing_categories']==['学业成绩','思想品德']


def test_public_binding_requires_reconfirmation_after_semantic_change(actors,clock):
    p=project(actors,clock);t=actors['teacher'];sid=actors['roster'][0]['id'];base='/api/projects/'+p['id'];l=public(t,p,sid)
    p=t.ok('POST',base+'/public-bindings',dict(revision=p['revision'],list_id=l['id'],list_revision=1))
    p=t.ok('PUT',base+'/instructions',dict(revision=p['revision'],text='新增正式解释',reason='解释更新'))
    assert any(x['kind']=='stale_public' for x in t.ok('GET',base+'/calculation')['students'][sid]['issues'])
    p=t.ok('POST',base+'/public-bindings',dict(revision=p['revision'],list_id=l['id'],list_revision=1))
    assert t.ok('GET',base+'/calculation')['issue_count']==0
