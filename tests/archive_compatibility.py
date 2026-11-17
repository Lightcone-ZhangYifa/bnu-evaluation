"""Verify the supplied rc1 .zcp using the current reader, engine and re-export.

No certificate is reinterpreted and no missing grade is filled in. The report is
aggregate only; temporary credentials never leave the temporary installation.
"""
from pathlib import Path
from datetime import datetime,timezone
import argparse,sys,tempfile,secrets,json,hashlib
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.config import Settings
from app.main import create_app
from app.portable import verify_archive
from conftest import API,INITIAL


def main():
    parser=argparse.ArgumentParser();parser.add_argument('archive',type=Path);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--export',type=Path);a=parser.parse_args()
    data=a.archive.read_bytes();z,manifest,payload=verify_archive(data);z.close()
    original=payload['body']['snapshots'][-1]['results'];checks=['基准归档全部成员清单、哈希、类型和引用通过当前读取器校验']
    with tempfile.TemporaryDirectory(prefix='bnu-archive-compatibility-') as tmp:
        tmp=Path(tmp);settings=Settings(data_dir=tmp/'data',database_url='sqlite:///'+str(tmp/'database.sqlite'),environment='development',secret=secrets.token_urlsafe(40),origin='http://testserver',admin_path='/private-archive-ops',bootstrap_password=INITIAL,admin_totp='',clamav_host='')
        app=create_app(settings)
        try:
            admin=API(app,'admin','admin',INITIAL);college=admin.ok('POST','/api/ops/colleges',dict(name='隔离兼容性验证学院'))
            teacher=admin.ok('POST','/api/ops/teachers',dict(college_id=college['id'],name='兼容性验证教师',username='compat_teacher'))
            t=API(app,'teacher','compat_teacher',teacher['initial_password'])
            r=t.client.post('/api/projects-import',files={'file':('test.zcp',data,'application/octet-stream')})
            assert r.status_code==200,r.text
            imp=r.json();p=imp.get('project',imp);pid=p['id'];base='/api/projects/'+pid
            project=t.ok('GET',base);calc=t.ok('GET',base+'/calculation')
            roster=project['body']['roster'];original_ids={r['number']:r['id'] for r in payload['roster']}
            checked=0
            for row in roster:
                old=original[original_ids[row['number']]];new=calc['students'][row['id']]
                assert str(old['total'])==str(new['total']),(row['number'],'total')
                # Node computation includes source ids; compare numbers, names, and
                # computation structure without claiming identities remain unchanged.
                new_nodes={n['id']:n for n in new['nodes']}
                for old_node in old['nodes']:
                    node=old_node['id'];new_node=new_nodes[node]
                    for k in ['value','raw_sum','selected_before_weight','weighted_before_cap','cap_delta']:
                        assert old_node.get(k)==new_node.get(k),(node,k)
                assert old['missing_categories']==new['missing_categories']
                checked+=1
            checks.append(f'{checked}人当前数值、分类原始值及封顶前后数值与基准公示快照一致')
            assert project['body']['publication_id'] is None and not project['body']['reviewer_ids']
            assert project['body']['meta']['import_unverified']
            checks.append('旧公示没有自动开放，审核权限未继承，导入来源保留待核对状态')
            r=t.request('GET',base+'/export/project');assert r.status_code==200,r.text
            nz,nm,np=verify_archive(r.content);nz.close()
            assert {k for k in nm['files'] if k.startswith('objects/')}=={k for k in manifest['files'] if k.startswith('objects/')}
            assert len(np['roster'])==len(payload['roster']) and len(np['body']['evidence'])==len(payload['body']['evidence'])
            checks.append('当前版本重新导出后可校验，资源集合、名册人数和材料数一致')
            if a.export:
                a.export.parent.mkdir(parents=True,exist_ok=True);a.export.write_bytes(r.content)
            report={'version':'1.0.0rc4','source_archive_sha256':hashlib.sha256(data).hexdigest(),'source_engine':manifest['engine_version'],'students_compared':checked,'evidence':len(payload['body']['evidence']),'resources':sum(k.startswith('objects/') for k in manifest['files']),'checks':checks,'passed':len(checks),'failed':0,'missing_grades_filled':False,'certificate_regraded':False,'note':'兼容性与数值一致性检查，不是全班材料复评或正式成绩认定。'}
            a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False,indent=2))
        finally:app.state.engine.dispose()

if __name__=='__main__':main()
