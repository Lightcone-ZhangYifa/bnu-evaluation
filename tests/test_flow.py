from datetime import timedelta
from io import BytesIO
from copy import deepcopy
import zipfile, json, uuid, hashlib
import pytest
from conftest import API, project, upload, annotate, png, PASSWORD
from app.portable import verify_archive

def test_complete_workflow_privacy_publication_and_archive(actors,clock,app):
    t=actors['teacher'];s1,s2,_=actors['students'];p=project(actors,clock);base='/api/projects/'+p['id']
    e=upload(s1,p,3);annotate(t,p,e)
    assert s1.request('GET',base+'/my-result').status_code==403
    visible=s1.ok('GET',base);assert len(visible['body']['evidence'])==1 and 'annotations' not in visible['body']['evidence'][0]
    assert s2.request('GET',base+'/evidence/'+e['evidence_id']+'/image').status_code==404
    assert s2.request('GET','/api/resources/'+e['resource_sha']).status_code==404
    source=t.client.post('/api/uploads',files={'file':('official.pdf',b'%PDF-1.4\nofficial\n%%EOF','application/pdf')}).json()
    rows=[]
    for st in actors['roster'][:2]:
        for cat,score in [('moral','5'),('academic','90')]:rows.append(dict(id=str(uuid.uuid4()),student_id=st['id'],category_id=cat,score=score,description='测试官方认定',scenario='完整数据',rule_source='测试来源',material_date='2026-08-31',outcome_key=cat))
    l=t.ok('POST','/api/public-lists',dict(revision=0,name='基础认定',body=dict(resource_sha=source['sha256'],source_name='official.pdf',description='合成测试数据，不使用真实学生成绩',rows=rows,approved=True)))
    p=t.ok('GET',base);p=t.ok('POST',base+'/public-bindings',dict(revision=p['revision'],list_id=l['id'],list_revision=l['revision']))
    clock[0]+=timedelta(hours=2)
    p=t.ok('POST',base+'/publish',dict(revision=p['revision'],mode='provisional',label='第一轮公示'))
    snapshot=s1.ok('GET',base+'/my-result');assert snapshot['result']['total']=='82.8';assert s2.me['student_id'] not in json.dumps(snapshot)
    assert s1.request('GET','/api/resources/'+source['sha256']).status_code==404
    supplementary=upload(s1,p,4,parent_id=e['evidence_id'])
    assert s1.ok('GET',base+'/my-result')==snapshot
    annotate(t,p,supplementary,score='0.8',scenario='二等奖',replaces=e['evidence_id'])
    calc=t.ok('GET',base+'/calculation');assert calc['students'][s1.me['student_id']]['total']=='83.2'
    p=t.ok('GET',base)
    assert t.request('POST',base+'/publish',dict(revision=p['revision'],mode='final',label='过早最终公示')).status_code==422
    clock[0]+=timedelta(hours=3)
    p=t.ok('POST',base+'/publish',dict(revision=p['revision'],mode='final',label='最终公示'))
    p=t.ok('POST',base+'/transition',dict(revision=p['revision'],state='closed',reason='完整核验'))
    p=t.ok('POST',base+'/transition',dict(revision=p['revision'],state='archived',reason='冻结归档'))
    assert s1.client.post(base+'/evidence',files={'file':('x.png',png(5),'image/png')},data={'idempotency_key':str(uuid.uuid4())}).status_code==403
    xlsx=t.request('GET',base+'/export/xlsx');assert xlsx.status_code==200 and xlsx.content[:2]==b'PK'
    pdf=s1.request('GET',base+'/export/student/'+s1.me['student_id']);assert pdf.status_code==200 and pdf.content.startswith(b'%PDF')
    arc=t.request('GET',base+'/export/project');assert arc.status_code==200
    z,m,payload=verify_archive(arc.content);assert len(payload['body']['snapshots'])==2
    assert not any('password' in name or 'token' in name for name in m['files'])
    imported=t.client.post('/api/projects-import',files={'file':('test.zcp',arc.content,'application/zip')});assert imported.status_code==200,imported.text
    ip=imported.json()['project'];assert ip['body']['publication_id'] is None and ip['body']['reviewer_ids']==[] and ip['body']['meta']['import_unverified']
    assert s1.request('GET','/api/projects/'+ip['id']+'/my-result').status_code==403
    restored=t.ok('GET','/api/projects/'+ip['id']+'/calculation')
    for sid, before in calc['students'].items():
        after=restored['students'][sid]
        assert after['total']==before['total']
        assert [(x['id'],x['raw_sum'],x['value'],x['cap_delta']) for x in after['nodes']]==[(x['id'],x['raw_sum'],x['value'],x['cap_delta']) for x in before['nodes']]
        assert after['missing_categories']==before['missing_categories']
        assert [(x['resource_sha'],x['annotation']['score']) for x in after['materials']]==[(x['resource_sha'],x['annotation']['score']) for x in before['materials']]
    # Portable import deliberately remaps library identifiers and does not inherit live publication privileges.

def test_submission_idempotent_image_only_deadline(actors,clock):
    p=project(actors,clock);s=actors['students'][0];base='/api/projects/'+p['id'];key=str(uuid.uuid4());e=upload(s,p,11,key)
    assert upload(s,p,11,key)['evidence_id']==e['evidence_id']
    assert s.client.post(base+'/evidence',files={'file':('x.png',png(12),'image/png')},data={'idempotency_key':key}).status_code==409
    assert s.client.post(base+'/evidence',files={'file':('x.pdf',b'%PDF-1.4\n%%EOF','application/pdf')},data={'idempotency_key':str(uuid.uuid4())}).status_code==422
    receipt=s.ok('POST',base+'/receipt');assert s.ok('POST',base+'/receipt')==receipt
    clock[0]+=timedelta(hours=1)
    r=s.client.post(base+'/evidence',files={'file':('x.png',png(13),'image/png')},data={'idempotency_key':str(uuid.uuid4())});assert r.status_code==403

def test_agent_scope_invalid_review_and_self_review(actors,clock,app):
    p=project(actors,clock);t=actors['teacher'];s1,s2,reviewer=actors['students'];base='/api/projects/'+p['id'];e=upload(s1,p,6)
    p=t.ok('GET',base);p=t.ok('PUT',base+'/members',dict(revision=p['revision'],student_ids=p['body']['student_ids'],reviewer_ids=[s1.me['id'],reviewer.me['id']]))
    agent=t.ok('POST','/api/agent-tokens',dict(project_id=p['id'],name='独立Agent',hours=1,scopes=['context:read','evidence:read','annotation:write']))
    client=app_client=__import__('fastapi.testclient',fromlist=['TestClient']).TestClient(app);client.headers['Authorization']='Bearer '+agent['token']
    assert client.get('/api/agent/manifest').status_code==200
    ctx=client.get('/api/agent/context/'+p['id']).json();assert len(ctx['queue'])==1
    annotation=dict(status='out_of_scope',score='0',description='不在规定范围',ai_note='需教师复核')
    data=dict(revision=p['revision'],expected_annotation_version=0,annotation=annotation,idempotency_key=str(uuid.uuid4()))
    r=client.put(base+'/evidence/'+e['evidence_id']+'/annotation',json=data);assert r.status_code==200,r.text
    calc=t.ok('GET',base+'/calculation');assert calc['issue_count']==1
    data['revision']=r.json()['revision'];data['expected_annotation_version']=1
    assert s1.request('PUT',base+'/evidence/'+e['evidence_id']+'/annotation',data).status_code==403
    annotate(t,p,e,status='out_of_scope',score='0',human_note='确不在附件二范围内')
    calc=t.ok('GET',base+'/calculation');assert calc['issue_count']==0
    current=t.ok('GET',base);data['revision']=current['revision'];data['expected_annotation_version']=2;data['idempotency_key']=str(uuid.uuid4())
    assert client.put(base+'/evidence/'+e['evidence_id']+'/annotation',json=data).status_code==403
    assert client.post(base+'/publish',json=dict(revision=current['revision'],mode='provisional',label='越权')).status_code==403
    assert s1.request('GET','/api/agent/context/'+p['id']).status_code==403
    t.ok('DELETE','/api/agent-tokens/'+agent['id']);assert client.get('/api/agent/manifest').status_code==401
