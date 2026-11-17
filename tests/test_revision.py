"""Regression cases for the rc2 office UI, exports and management workflows."""
from copy import deepcopy
from contextlib import closing
from datetime import timedelta
from io import BytesIO
from pathlib import Path
import hashlib, json, sqlite3, zipfile
import pytest
from conftest import project, upload, annotate, png
from scripts import console


def test_student_edit_is_version_checked_and_revokes_sessions(actors):
    t=actors['teacher'];student=actors['students'][0]
    old=t.ok('GET','/api/students')[0]
    payload=dict(name='测试甲更正',class_name='新班级',disabled=True,
                 expected_name=old['name'],expected_class_name=old['class_name'],expected_disabled=old['disabled'])
    changed=t.ok('PATCH','/api/students/'+old['id'],payload)
    assert changed['name']=='测试甲更正' and not changed['active'] and changed['disabled']
    assert student.request('GET','/api/auth/me').status_code==401
    assert t.request('PATCH','/api/students/'+old['id'],payload).status_code==409
    assert actors['admin'].request('PATCH','/api/students/'+old['id'],payload).status_code==403


def test_student_edit_cross_college_forbidden(actors):
    a=actors['admin'];t=actors['teacher'];old=t.ok('GET','/api/students')[0]
    college=a.ok('POST','/api/ops/colleges',{'name':'其他学院'})
    details=a.ok('POST','/api/ops/teachers',dict(college_id=college['id'],name='其他教师',username='otherteacher'))
    from conftest import API
    other=API(t.client.app,'teacher','otherteacher',details['initial_password'])
    payload=dict(name=old['name'],class_name='新班',disabled=False,expected_name=old['name'],expected_class_name=old['class_name'],expected_disabled=False)
    assert other.request('PATCH','/api/students/'+old['id'],payload).status_code==404


def test_receipt_rejects_new_late_confirmation_but_existing_is_idempotent(actors,clock):
    p=project(actors,clock);first,second,_=actors['students'];base='/api/projects/'+p['id']
    receipt=first.ok('POST',base+'/receipt')
    clock[0]+=timedelta(hours=2)
    assert first.ok('POST',base+'/receipt')==receipt
    assert second.request('POST',base+'/receipt').status_code==409


def test_folder_restore_and_cycle_guard(actors):
    t=actors['teacher'];f=t.ok('POST','/api/folders',dict(name='归档目录',parent_id=None,trashed=False))
    child=t.ok('POST','/api/folders',dict(name='子目录',parent_id=f['id'],trashed=False))
    assert t.request('PATCH','/api/folders/'+f['id'],dict(name='归档目录',parent_id=child['id'],trashed=False)).status_code==422
    t.ok('PATCH','/api/folders/'+child['id'],dict(name='子目录',parent_id=f['id'],trashed=True))
    t.ok('PATCH','/api/folders/'+f['id'],dict(name='归档目录',parent_id=None,trashed=True))
    t.ok('PATCH','/api/folders/'+child['id'],dict(name='子目录',parent_id=None,trashed=False))
    assert next(x for x in t.ok('GET','/api/folders') if x['id']==child['id'])['parent_id'] is None


def test_material_bundle_is_private_and_has_unique_originals(actors,clock):
    p=project(actors,clock);t=actors['teacher'];s=actors['students'][0];base='/api/projects/'+p['id']
    e=upload(s,p,29);annotate(t,p,e)
    r=t.request('GET',base+'/export/materials');assert r.status_code==200,r.text
    with zipfile.ZipFile(BytesIO(r.content)) as z:
        names=z.namelist();manifest=json.loads(z.read('材料清单.json'))
        assert '请先打开.html' in names and len(names)==len(set(names))
        assert len(manifest['resources'])==2
        for resource in manifest['resources']:
            assert hashlib.sha256(z.read(resource['path'])).hexdigest()==resource['sha256']
        assert e['resource_sha'] in z.read('材料清单.json').decode()
    assert s.request('GET',base+'/export/materials').status_code==403
    assert actors['admin'].request('GET',base+'/export/materials').status_code==403


def test_xlsx_uses_configured_categories_not_template(actors,clock):
    p=project(actors,clock);t=actors['teacher'];base='/api/projects/'+p['id']
    template=deepcopy(p['body']['rule_versions'][-1]);rules={k:template[k] for k in ('nodes','display_places','explanation')}
    for n in rules['nodes']:
        if n['id']=='work':n['name']='学院自定义分类'
    t.ok('PUT',base+'/rules',dict(revision=p['revision'],rules=rules,reason='验证导出表头随规则变化'))
    r=t.request('GET',base+'/export/xlsx');assert r.status_code==200,r.text
    with zipfile.ZipFile(BytesIO(r.content)) as z:
        first=z.read('xl/worksheets/sheet1.xml').decode()
        assert '学院自定义分类' in first and '学生工作' not in first


def test_feedback_and_audit_export_are_admin_only(actors):
    t=actors['teacher'];a=actors['admin']
    r=t.client.post('/api/feedback',data={'text':'仅供测试的反馈'},files={'files':('screenshot.png',png(9),'image/png')});assert r.status_code==200
    data=a.ok('GET','/api/ops/feedback-export');assert data['records'][0]['text']=='仅供测试的反馈'
    log=a.request('GET','/api/ops/audit-export');assert log.status_code==200
    records=[json.loads(x) for x in log.text.splitlines()]
    assert any(x['action']=='feedback.export' for x in records)
    for uri in ('/api/ops/feedback-export','/api/ops/audit-export'):
        assert t.request('GET',uri).status_code==403
        assert actors['students'][0].request('GET',uri).status_code==403


def test_office_ui_routes_and_all_action_handlers_have_implementations(app):
    from fastapi.testclient import TestClient
    import re
    client=TestClient(app)
    for uri in ('/','/login/student','/login/teacher','/private-ops'):
        text=client.get(uri).text
        assert '北京师范大学学生综合测评管理系统' in text and '澄明' not in text
    source='\n'.join((Path(__file__).parents[1]/'app/static'/name).read_text() for name in ['app.js','review-ui.js','workflow-ui.js'])
    assert "['unlabeled','untrusted','system_error','manual']" in source
    assert 'data-public-version' in source and 'data-public-rule-project' in source
    assert 'restore-folder' in source and 'allStudents()' in source
    literal=set(re.findall(r"btn\('([^']+)'",source))|set(re.findall(r'data-action="([a-z][a-z-]+)"',source))
    defined=set(re.findall(r"A\['([^']+)'\]\s*=",source))|set(re.findall(r'A\.([a-z]+)\s*=',source))
    assert not literal-defined
    forms=set(re.findall(r'data-form="([a-z][a-z-]+)"',source))
    handlers=set(re.findall(r"F\['([^']+)'\]\s*=",source))|set(re.findall(r'F\.([a-z]+)\s*=',source))
    assert not forms-handlers


@pytest.mark.parametrize('mode',['local','docker'])
def test_cli_configuration_is_non_overwriting_and_hides_secrets(tmp_path,capsys,mode):
    env=console.configure(mode,domain='evaluation.example.edu.cn',root=tmp_path)
    out=capsys.readouterr().out
    assert env['APP_SECRET'] not in out and env['BOOTSTRAP_ADMIN_PASSWORD'] not in out
    assert console.profile(tmp_path)==mode
    assert console.load_env(tmp_path/'.env')==env
    before=(tmp_path/'.env').read_bytes()
    with pytest.raises(console.OperationError):console.configure(mode,domain='evaluation.example.edu.cn',root=tmp_path)
    assert (tmp_path/'.env').read_bytes()==before


@pytest.mark.parametrize('domain',['https://example.edu.cn','example.edu.cn:443','abc;reboot','example.edu.cn\nBAD=1'])
def test_cli_rejects_unsafe_domains(tmp_path,domain):
    with pytest.raises(console.OperationError):console.configure('docker',domain=domain,root=tmp_path)
    assert not (tmp_path/'.env').exists()


def test_cli_native_configuration_and_service(tmp_path):
    env=console.configure('native',domain='example.edu.cn',database_url='postgresql+psycopg://zc_app:secret@localhost/clearledger',migration_url='postgresql+psycopg://zc_migrator:secret@localhost/clearledger',root=tmp_path)
    console.native_files(tmp_path)
    unit=(tmp_path/'deploy/generated/bnu-evaluation.service').read_text()
    assert 'EnvironmentFile=' in unit and 'User=bnu-evaluation' in unit and 'Restart=on-failure' in unit
    assert env['APP_SECRET'] not in unit and 'secret@' not in unit


def test_cli_backup_restore_matching_database_resources_and_secrets(tmp_path,monkeypatch):
    root=tmp_path/'old';root.mkdir();env=console.configure('local',root=root)
    (root/'var/objects/aa').mkdir(parents=True)
    dbpath=root/'var/ledger.db'
    with closing(sqlite3.connect(dbpath)) as db, db:
        db.execute('create table example (value text)');db.execute("insert into example values ('保存的数据')")
    blob=b'immutable example';sha=hashlib.sha256(blob).hexdigest()
    obj=root/'var/objects'/sha[:2]/sha;obj.parent.mkdir(exist_ok=True);obj.write_bytes(blob)
    monkeypatch.setattr(console,'health',lambda *a,**k:(_ for _ in ()).throw(console.OperationError('not running')))
    destination=tmp_path/'backup';console.backup(destination,root)
    archive=destination/'system-backup.zip';manifest=console.verify_backup(archive)
    assert manifest['mode']=='local' and manifest['files']['objects/'+sha[:2]+'/'+sha]['sha256']==sha
    restored=tmp_path/'restored';restored.mkdir();monkeypatch.setattr(console,'confirm',lambda *a,**k:True)
    console.restore(archive,restored)
    assert console.load_env(restored/'.env')['APP_SECRET']==env['APP_SECRET']
    assert (restored/'var/objects'/sha[:2]/sha).read_bytes()==blob
    with closing(sqlite3.connect(restored/'var/ledger.db')) as db, db:assert db.execute('select value from example').fetchone()[0]=='保存的数据'
    with pytest.raises(console.OperationError):console.restore(archive,restored)


@pytest.mark.parametrize('kind',['traversal','duplicate','tamper','symlink'])
def test_cli_rejects_unsafe_backup_before_extracting(tmp_path,kind):
    path=tmp_path/'bad.zip';content=b'example';name='../outside' if kind=='traversal' else 'database.sqlite'
    with zipfile.ZipFile(path,'w') as z:
        if kind=='symlink':
            info=zipfile.ZipInfo(name);info.create_system=3;info.external_attr=(0o120777<<16);z.writestr(info,content)
        else:z.writestr(name,content)
        if kind=='duplicate':
            with pytest.warns(UserWarning):z.writestr(name,content)
        z.writestr('manifest.json',json.dumps(dict(format='bnu-system-backup-v1',mode='local',files={name:dict(size=len(content),sha256='0'*64 if kind=='tamper' else hashlib.sha256(content).hexdigest())})))
    with pytest.raises(console.OperationError):console.verify_backup(path)
    assert not (tmp_path.parent/'outside').exists()


def test_cli_backup_does_not_stop_unknown_process(tmp_path,monkeypatch):
    root=tmp_path/'root';root.mkdir();console.configure('local',root=root)
    monkeypatch.setattr(console,'health',lambda *a,**k:{'status':'ok','instance_id':'other'})
    monkeypatch.setattr(console,'stop',lambda *a,**k:pytest.fail('must not stop unknown application'))
    with pytest.raises(console.OperationError):console.backup(tmp_path/'backup',root)
    assert not (tmp_path/'backup').exists()


def test_public_names_are_versioned_and_server_assigned(actors,clock):
    t=actors['teacher'];p=project(actors,clock);src=p['body']['source_documents'][0]['sha256']
    body=dict(resource_sha=src,source_name='公共来源.pdf',description='版本测试',note='',rows=[],approved=False,name_at_version='客户端不应决定历史名称')
    first=t.ok('POST','/api/public-lists',dict(revision=0,name='第一版名称',body=body))
    second=t.ok('PUT','/api/public-lists/'+first['id'],dict(revision=1,name='第二版名称',body=body))
    old=t.ok('GET','/api/public-lists/'+first['id']+'?revision=1')
    latest=t.ok('GET','/api/public-lists/'+first['id'])
    assert old['name']=='第一版名称' and old['body']['name_at_version']=='第一版名称'
    assert latest['name']=='第二版名称' and latest['body']['name_at_version']=='第二版名称'


@pytest.mark.parametrize('unsafe',[False,True])
def test_docker_backup_tar_is_validated_before_restore(tmp_path,unsafe):
    import tarfile
    directory=tmp_path/'docker';directory.mkdir();blob=b'immutable object';sha=hashlib.sha256(blob).hexdigest()
    with tarfile.open(directory/'objects.tar','w') as tar:
        info=tarfile.TarInfo('../escape' if unsafe else 'objects/'+sha[:2]+'/'+sha);info.size=len(blob);tar.addfile(info,BytesIO(blob))
    (directory/'database.dump').write_bytes(b'test format validation, not PostgreSQL restore')
    (directory/'environment.secret').write_text('APP_SECRET=test-only\n')
    (directory/'SHA256SUMS').write_text(''.join(console.file_hash(directory/name)+'  '+name+'\n' for name in ['database.dump','objects.tar','environment.secret']))
    if unsafe:
        with pytest.raises(console.OperationError):console.verify_backup(directory)
    else:assert console.verify_backup(directory)['resources']==1


def test_frontend_roster_fetches_more_than_two_thousand_students():
    import subprocess,shutil
    node=shutil.which('node')
    if not node:pytest.skip('Node.js is required for the frontend pagination regression')
    source='\n'.join((Path(__file__).parents[1]/'app/static'/name).read_text() for name in ['app.js','review-ui.js','workflow-ui.js'])
    function=next(line for line in source.splitlines() if line.startswith('async function allStudents()'))
    harness="""const rows=Array.from({length:4501},(_,i)=>({id:String(i)}));let calls=0;
async function api(url){calls++;const q=new URL(url,'http://fixture').searchParams;const from=Number(q.get('offset'));return rows.slice(from,from+Number(q.get('limit')));}
"""+function+"""\n(async()=>{const all=await allStudents();if(all.length!==4501||new Set(all.map(x=>x.id)).size!==4501||calls!==3)throw Error('pagination lost rows');})().catch(e=>{console.error(e);process.exit(1);});"""
    r=subprocess.run([node,'-e',harness],capture_output=True,text=True,timeout=10)
    assert r.returncode==0,r.stderr


def test_cli_malformed_manifest_is_a_controlled_error(tmp_path):
    archive=tmp_path/'bad.zip'
    with zipfile.ZipFile(archive,'w') as z:z.writestr('manifest.json','[]')
    with pytest.raises(console.OperationError):console.verify_backup(archive)


def test_cli_restore_refuses_symlink_data_directory(tmp_path):
    if not hasattr(__import__('os'),'symlink'):pytest.skip('symbolic links unavailable')
    target=tmp_path/'target';outside=tmp_path/'outside';target.mkdir();outside.mkdir()
    (target/'var').symlink_to(outside,target_is_directory=True)
    with pytest.raises(console.OperationError):console.restore(tmp_path/'unused.zip',target)
    assert not list(outside.iterdir())
