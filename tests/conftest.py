from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone, timedelta
from io import BytesIO
import secrets, uuid
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app

INITIAL='Initial-password-test-2026!'
PASSWORD='Changed-password-test-2026!'

class API:
    def __init__(self, app, portal, username, password, change=True):
        self.client=TestClient(app)
        path='/api/ops/login' if portal=='admin' else '/api/auth/login'
        r=self.client.post(path,json=dict(portal=portal,username=username,password=password))
        assert r.status_code==200, r.text
        self.me=r.json();self.client.headers['X-CSRF-Token']=self.me['csrf']
        if self.me['must_change'] and change:
            self.me=self.ok('POST','/api/auth/password',dict(old_password=password,new_password=PASSWORD))
            self.client.headers['X-CSRF-Token']=self.me['csrf']
    def request(self,method,path,data=None,**kw):
        if data is not None:kw['json']=data
        return self.client.request(method,path,**kw)
    def ok(self,method,path,data=None,**kw):
        r=self.request(method,path,data,**kw)
        assert r.status_code==200, f'{method} {path}: {r.status_code} {r.text[:1200]}'
        return r.json()

@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path/'data',database_url='sqlite:///'+str(tmp_path/'db.sqlite'),environment='development',secret=secrets.token_urlsafe(40),origin='http://testserver',admin_path='/private-ops',bootstrap_password=INITIAL,admin_totp='',clamav_host='')

@pytest.fixture
def app(settings):
    a=create_app(settings)
    yield a
    a.state.engine.dispose()

@pytest.fixture
def actors(app):
    admin=API(app,'admin','admin',INITIAL)
    college=admin.ok('POST','/api/ops/colleges',{'name':'测试学院'})
    t=admin.ok('POST','/api/ops/teachers',dict(college_id=college['id'],username='teacher',name='测试教师'))
    teacher=API(app,'teacher','teacher',t['initial_password'])
    roster=teacher.ok('POST','/api/students/import',{'rows':[dict(number='001',name='测试甲',class_name='测试班'),dict(number='002',name='测试乙',class_name='测试班'),dict(number='003',name='审核员',class_name='另一个年级')]})
    students=[API(app,'student',c['username'],c['initial_password']) for c in roster['credentials']]
    return dict(admin=admin,teacher=teacher,students=students,roster=roster['students'],college=college)

@pytest.fixture
def clock(monkeypatch):
    import app.domain
    t=[datetime.now(timezone.utc)]
    monkeypatch.setattr(app.domain,'now',lambda:t[0].isoformat(timespec='microseconds'))
    return t

def png(seed=1):
    buf=BytesIO();Image.new('RGB',(100,120),(seed%255,(seed*7)%255,(seed*13)%255)).save(buf,'PNG');return buf.getvalue()

def project(actors,clock=None,all_students=False):
    t=clock[0] if clock else datetime.now(timezone.utc)
    teacher=actors['teacher'];ids=[s['id'] for s in actors['roster'][:3 if all_students else 2]]
    p=teacher.ok('POST','/api/projects',dict(title='流程验证工程',year='2025-2026',class_name='测试班',responsible='测试教师',student_ids=ids,assessment_start='2025-09-01',assessment_end='2026-08-31'))
    base='/api/projects/'+p['id']
    r=teacher.client.post(base+'/documents',files={'file':('rules.pdf',b'%PDF-1.4\nfixture\n%%EOF','application/pdf')},data={'purpose':'rules','revision':str(p['revision'])});assert r.status_code==200,r.text;p=r.json()
    p=teacher.ok('PUT',base+'/schedule',dict(revision=p['revision'],opens_at=(t-timedelta(hours=1)).isoformat(),deadline_at=(t+timedelta(hours=1)).isoformat(),appeal_deadline=(t+timedelta(hours=4)).isoformat()))
    p=teacher.ok('POST',base+'/transition',dict(revision=p['revision'],state='submission',reason='测试启动'))
    return p

def upload(actor,p,seed=1,key=None,**fields):
    key=key or str(uuid.uuid4())
    r=actor.client.post('/api/projects/'+p['id']+'/evidence',files={'file':('proof.png',png(seed),'image/png')},data={'idempotency_key':key,**fields})
    assert r.status_code==200,r.text
    return r.json()

def annotate(actor,p,e,status='valid',score='0.4',**extra):
    base='/api/projects/'+p['id'];current=actor.ok('GET',base)
    item=next(x for x in current['body']['evidence'] if x['id']==e['evidence_id'])
    data=dict(status=status,score=score,material_date='2026-05-06',description='测试证书',category_id='competition.professional',rule_item='中国高校计算机大赛团体程序设计天梯赛全国个人奖',scenario='三等奖',rule_source='细则第10页',outcome_key='tian-ti-2026')
    data.update(extra)
    return actor.ok('PUT',base+'/evidence/'+e['evidence_id']+'/annotation',dict(revision=current['revision'],expected_annotation_version=len(item['annotations']),annotation=data,idempotency_key=str(uuid.uuid4())))
