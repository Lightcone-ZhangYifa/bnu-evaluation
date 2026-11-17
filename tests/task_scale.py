"""Synthetic 70-student, 840-image signed spreadsheet round trip.

Uses an export_scale.py archive for synthetic images only; seeds a *new* project
with no annotation history. Does not erase/alter the imported project's history.
No provider/model is contacted; not an HTTP upload/concurrency benchmark.
"""
from pathlib import Path
from copy import deepcopy
from io import BytesIO
from zipfile import ZipFile,ZIP_DEFLATED
import argparse,json,secrets,sys,tempfile,time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from sqlalchemy import select
from app.main import create_app
from app.config import Settings
from app.db import Account,uid,now
from app.security import Principal
from app.schemas import ProjectCreate
from app.portable import import_project
from app.table_reader import read_table
from app.tabular import csv_bytes
from conftest import API,INITIAL


def run(source:Path,output:Path):
    output.mkdir(parents=True,exist_ok=True);start=time.perf_counter();checks=[];timings={}
    with tempfile.TemporaryDirectory(prefix='bnu-task-scale-') as temporary:
        root=Path(temporary)
        settings=Settings(data_dir=root/'data',database_url='sqlite:///'+str(root/'db.sqlite'),environment='development',secret=secrets.token_urlsafe(40),origin='http://testserver',admin_path='/task-scale-private',bootstrap_password=INITIAL,admin_totp='',clamav_host='')
        app=create_app(settings);domain=app.state.domain
        admin=API(app,'admin','admin',INITIAL)
        college=admin.ok('POST','/api/ops/colleges',{'name':'任务回填验收学院（全部合成）'})
        teacher_account=admin.ok('POST','/api/ops/teachers',{'college_id':college['id'],'username':'task_teacher','name':'合成测试教师'})
        teacher=API(app,'teacher','task_teacher',teacher_account['initial_password'])
        with app.state.factory() as db:
            owner=Principal(db.scalar(select(Account).where(Account.username=='task_teacher')))
            original,_=import_project(db,owner,domain,source.read_bytes());db.commit()
            p=domain.create(db,owner,ProjectCreate(title='网页AI回填规模测试（合成）',year='2025-2026',class_name='合成01班',responsible='合成测试教师',student_ids=original.body['student_ids'],assessment_start='2025-09-01',assessment_end='2026-08-31',test_only=True))
            body=deepcopy(p.body);body['state']='review';body['source_documents']=deepcopy(original.body['source_documents'])
            body['evidence']=[]
            for source_item in original.body['evidence']:
                body['evidence'].append({'id':uid(),'student_id':source_item['student_id'],'resource_sha':source_item['resource_sha'],'submitted_at':now(),'submitted_by':owner.user.id,'submitter_role':'teacher','parent_id':None,'idempotency_key':uid(),'annotations':[]})
            domain.save(db,owner,p,body,p.revision,'fixture.new-unannotated-project',{'synthetic_only':True,'images':len(body['evidence'])});db.commit()
            pid=p.id;ids=[e['id'] for e in body['evidence']];student_count=len(body['student_ids'])
        base='/api/projects/'+pid
        def export(ids):
            pr=teacher.ok('GET',base)
            response=teacher.request('POST',base+'/web-ai/export',{'revision':pr['revision'],'evidence_ids':ids})
            assert response.status_code==200,response.text
            return response.content
        tick=time.perf_counter();archive=export(ids);timings['export_840_seconds']=round(time.perf_counter()-tick,3)
        z=ZipFile(BytesIO(archive));credential=z.read('任务凭据.json');table=read_table(z.read('回填模板.csv'),'回填.csv')
        rows=[dict(r) for r in table]
        assert len(rows)==len(ids)==840 and student_count==70
        assert len([n for n in z.namelist() if n.startswith('图片/')])==840
        checks.append('70名学生840张图片按资源校验值导出，840行固定身份与材料索引一致')
        for i,row in enumerate(rows):
            row.update({'状态':'需人工判断' if i%13==0 else '有效','局部分数':'0' if i%13==0 else '0.4','材料日期':'2026-05-06','图片内容描述':'合成测试判读结果 '+str(i+1),'分类ID':'competition.professional','细则加分项':'合成赛项','具体情形':'三等奖','细则出处':'合成来源文件；仅供软件测试','成果键':f'scale-{i}','是否有盖章':'未判定','AI备注':'材料层级尚未明确，等待人工核对' if i%13==0 else ''})
        def response(rows,commit=False,revision=None,task_credential=None):
            data=csv_bytes(table.headers,[[row.get(h,'') for h in table.headers] for row in rows])
            return teacher.client.post(base+'/web-ai/import',files={'credential':('任务凭据.json',task_credential or credential,'application/json'),'file':('回填.csv',data,'text/csv')},data={'commit':str(commit).lower(),**({'expected_revision':str(revision)} if revision is not None else {})})
        bad=deepcopy(rows);bad[-1]['学生ID']='INVALID-IDENTITY'
        tick=time.perf_counter();invalid=response(bad,True,teacher.ok('GET',base)['revision']);timings['invalid_last_row_seconds']=round(time.perf_counter()-tick,3)
        assert invalid.status_code==422,invalid.text
        errors=invalid.json()['detail'];assert any(e['row']==841 and e['column']=='B' for e in errors['errors'])
        assert all(not e['annotations'] for e in teacher.ok('GET',base)['body']['evidence'])
        checks.append('第841行B列身份错误定位准确：前839条有效行同样零写入')
        (output/'示例_回填错误清单.json').write_text(json.dumps(errors,ensure_ascii=False,indent=2))
        tick=time.perf_counter();preview=response(rows);assert preview.status_code==200,preview.text
        assert all(not e['annotations'] for e in teacher.ok('GET',base)['body']['evidence'])
        timings['preview_840_seconds']=round(time.perf_counter()-tick,3)
        checks.append('840行完整预校验不写入任何标注，预览不伪装成人工审批')
        # A small task illustrates file layout; credentials refer only to this
        # temporary test instance and are NOT reusable in a user deployment.
        small=export(ids[:12]);(output/'示例_网页AI任务_12张合成材料.zip').write_bytes(small)
        small_credential=ZipFile(BytesIO(small)).read('任务凭据.json')
        example_rows=rows[:12]
        assert {r['材料ID'] for r in example_rows}=={r['evidence_id'] for r in json.loads(small_credential)['manifest']['evidence']}
        paired_preview=response(example_rows,task_credential=small_credential)
        assert paired_preview.status_code==200,paired_preview.text
        assert all(not e['annotations'] for e in teacher.ok('GET',base)['body']['evidence'])
        (output/'示例_AI回填_12行_与12张任务配对.csv').write_bytes(csv_bytes(table.headers,[[r.get(h,'') for h in table.headers] for r in example_rows]))
        invalid_example=deepcopy(example_rows);invalid_example[-1]['学生ID']='INVALID-IDENTITY'
        example_error=response(invalid_example,task_credential=small_credential)
        assert example_error.status_code==422 and example_error.json()['detail']['errors'][0]['row']==13
        (output/'示例_12行错误表_仅供对照.csv').write_bytes(csv_bytes(table.headers,[[r.get(h,'') for h in table.headers] for r in invalid_example]))
        (output/'示例_12行错误清单_第13行B列.json').write_text(json.dumps(example_error.json()['detail'],ensure_ascii=False,indent=2))
        checks.append('12张示例任务与12行回填完全配对、预览通过；附第13行B列错误对照及真实诊断')
        (output/'示例_AI回填_840行.csv').write_bytes(csv_bytes(table.headers,[[r.get(h,'') for h in table.headers] for r in rows]))
        tick=time.perf_counter();result=response(rows,True,preview.json()['revision']);assert result.status_code==200,result.text
        timings['commit_840_seconds']=round(time.perf_counter()-tick,3)
        current=teacher.ok('GET',base)
        assert all(len(e['annotations'])==1 and e['annotations'][0]['actor_kind']=='web_ai' and not e['annotations'][0]['reviewed'] for e in current['body']['evidence'])
        assert current['revision']==preview.json()['revision']+1
        checks.append('840条局部标注单次版本更新整体提交；全部web_ai，人工核对均为否')
        again=response(rows,True,current['revision']);assert again.status_code==200,again.text
        assert all(len(e['annotations'])==1 for e in teacher.ok('GET',base)['body']['evidence'])
        checks.append('重复提交同一840行任务不产生重复标注版本、不重复计分')
        (output/'请先阅读.txt').write_text('所有资料均为合成测试数据，不是学生真实材料。\n12张任务与“12行_与12张任务配对.csv”完全配对，已在临时实例校验通过。另有错误表和逐行诊断供对照。840行CSV只展示规模格式，不可与12张任务配对。\n任务凭据属于已销毁的临时测试实例，不能导入您的部署。实际使用时必须从您自己的工程重新导出任务包。\n网页AI不能接收ZIP时应先解压并分批上传，禁止提供账号令牌。\n')
        app.state.engine.dispose()
    result={'version':'1.0.0rc4','synthetic_only':True,'students':student_count,'images':len(ids),'checks':checks,'passed':len(checks),'timings_seconds':{**timings,'total':round(time.perf_counter()-start,3)},'not_validated':['任何实际外部AI服务的识图质量','并发提交或上传性能','真实学生资料审批']}
    (output/'task-scale-report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('archive',type=Path);parser.add_argument('--output',type=Path,default=ROOT/'artifacts/task-scale');args=parser.parse_args();run(args.archive,args.output)
