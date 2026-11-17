"""Reproducible export acceptance fixture. All identities and evidence are synthetic.

Run from source: python tests/export_scale.py --output artifacts/export-scale
This bulk-seeds valid versioned domain objects, then invokes the real calculation,
report and archive code. It is not an upload throughput or concurrent load test.
Requires a local CJK font for readable synthetic certificates (CJK_FONT env).
The temporary DB, credentials and font are NOT part of the delivered examples.
"""
from __future__ import annotations
from pathlib import Path
from datetime import datetime,timezone,timedelta
from decimal import Decimal,localcontext
from io import BytesIO
from copy import deepcopy
from collections import Counter
import argparse,os,json,time,uuid,zipfile,sys,tempfile,secrets,xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from PIL import Image,ImageDraw,ImageFont
from sqlalchemy import select
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
from app.db import Student,Account,Project,PublicList,PublicVersion,uid,now
from app.schemas import Annotation,PublicBody,PublicRow,ProjectCreate,AcademicWrite,Publish,RuleSet
from app.security import Principal,canonical,audit
from app.domain import active_rules,contract_hash
from app.exports import result_xlsx,personal_pdf,material_bundle
from app.reporting import result_records,records_sheet,xlsx_bytes,rule_sheet
from app.import_templates import template_bytes
from app.portable import export_project,verify_archive,import_project
from conftest import API,INITIAL
from test_exports import workbook,values,all_text
D=Decimal


def fixed(value):return str(uuid.uuid5(uuid.NAMESPACE_URL,'bnu-synthetic-rc3/'+value))


def run(output:Path,classes=10,students=70):
    output.mkdir(parents=True,exist_ok=True);started=time.perf_counter();checks=[];timings={};reports=[]
    fontpath=os.environ.get('CJK_FONT','/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
    if not Path(fontpath).is_file():raise RuntimeError('请设置CJK_FONT为本机中文字体路径。字体文件不随测试包分发。')
    f20=ImageFont.truetype(fontpath,20);f26=ImageFont.truetype(fontpath,26);f15=ImageFont.truetype(fontpath,15)
    temp=tempfile.TemporaryDirectory(prefix='bnu-export-scale-');base=Path(temp.name)
    settings=Settings(data_dir=base/'data',database_url='sqlite:///'+str(base/'test.sqlite'),environment='development',secret=secrets.token_urlsafe(40),origin='http://testserver',admin_path='/scale-private',bootstrap_password=INITIAL,admin_totp='',clamav_host='')
    app=create_app(settings);domain=app.state.domain
    admin=API(app,'admin','admin',INITIAL)
    college=admin.ok('POST','/api/ops/colleges',{'name':'合成测试学院（非真实学院）'})
    cred=admin.ok('POST','/api/ops/teachers',dict(college_id=college['id'],username='synthetic_teacher',name='合成测试教师'))
    teacher=API(app,'teacher','synthetic_teacher',cred['initial_password'])
    stamps=['合成测试学院（非真实印章）','合成竞赛工作组（仅供软件测试）',None]
    source=BytesIO()
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    c=canvas.Canvas(source);c.setFont('STSong-Light',16);c.drawString(50,780,'导出验收合成来源文件')
    c.setFont('STSong-Light',11)
    for n,line in enumerate(['本文件、名册、分数和证明图片全部为软件测试数据。','不是学校发布的细则、名单或真实成绩，不可用于任何认定。','测试范围：多级分类、最大值、封顶、课程权重、扣分、缺失材料。','岗位测试：班级1.6分、校级工作人员0.25分，取最高。','活动测试：文体累加封顶2分，学生活动上限4分。','基础认定由测试程序赋值，不代表任何实际学生的评价。']):c.drawString(50,735-28*n,line)
    c.save();source_bytes=source.getvalue()
    counts=Counter();expected={};pids=[];snapshot_ids={};seed=time.perf_counter()
    with app.state.factory() as db:
        account=db.scalar(select(Account).where(Account.username=='synthetic_teacher'));principal=Principal(account)
        src=domain.store.put(db,source_bytes,'document')['sha256']
        allstudents=[]
        for ci in range(classes):
            for si in range(students):
                index=ci*students+si
                st=Student(id=fixed(f'student-{index}'),college_id=college['id'],number=f'DEMO2024{ci+1:02d}{si+1:03d}',name=f'合成学生{ci+1:02d}{si+1:03d}',class_name=f'合成{ci+1:02d}班')
                db.add(st);allstudents.append(st)
        db.flush();counts['students']=len(allstudents)
        # Six different public lists share one immutable source file; each student
        # appears in all six. Different class files bind the exact same version.
        definitions=[('基础认定','moral','5'),('班级岗位','positions','1.6'),('校级岗位','positions','0.25'),('班级活动','activities.party.class','0.5'),('大型活动','activities.large','0.3'),('系列活动','activities.party.lectures','0.1')]
        publics=[]
        for li,(name,cat,score) in enumerate(definitions):
            lid=fixed('public-'+str(li));rows=[]
            for index,st in enumerate(allstudents):
                rows.append(PublicRow(id=fixed(f'public-{li}-student-{index}'),student_id=st.id,category_id=cat,score=score,description='合成测试：'+name,scenario='测试认定条件已满足',rule_source='导出验收合成来源文件',material_date='2026-06-30',outcome_key=f'public-{li}-{index}',has_stamp=False if index%7==0 else None,note='仅供验收，不代表真实学生认定').model_dump())
            body=PublicBody(resource_sha=src,source_name='合成来源.pdf',description='合成测试：'+name,rows=rows,approved=True,has_stamp=True,stamp_text=stamps[0],name_at_version='合成测试：'+name).model_dump()
            pl=PublicList(id=lid,college_id=college['id'],owner_id=account.id,name='合成测试：'+name,revision=1);db.add(pl);db.flush()
            pv=PublicVersion(id=fixed('public-version-'+str(li)),list_id=lid,revision=1,body=body,actor_id=account.id);db.add(pv);publics.append((pl,pv));counts['public_rows']+=len(rows)
        db.flush()
        courses=[dict(id=f'CS{i+1:02d}',name=['数据结构','离散数学','程序设计','算法设计','计算机组成','数据库','操作系统','计算机网络','概率统计','计算机图形学','课程实践','体育'][i],credits='3' if i<10 else '2' if i==10 else '1',scheme='numeric' if i<10 else 'five' if i==10 else 'two') for i in range(12)]
        descriptions=[('competition.professional','0.4','团体程序设计竞赛个人三等奖'),('competition.professional','0.8','程序设计竞赛个人二等奖'),('research.projects','0.6','校级科研项目主持结题'),('research.projects','1','校级科研项目优秀结题'),('activities.sports','1','运动会跑步项目第二名'),('activities.sports','1','运动会跳远项目第一名'),('activities.sports','0.8','运动会B级项目第二名'),('activities.practice','0.9','暑期社会实践项目主持一等奖'),('certification','1','计算机软件能力认证250分'),('activities.party.lectures','0.1','学院学术系列活动参加一次'),('competition.creative','0.3','创意策划比赛校级二等奖'),('activities.party.dorm','0.5','文明宿舍三等奖')]
        current=datetime.now(timezone.utc)
        for ci in range(classes):
            group=allstudents[ci*students:(ci+1)*students]
            payload=ProjectCreate(title=f'2025—2026学年合成{ci+1:02d}班综合测评',year='2025-2026',class_name=f'合成{ci+1:02d}班',responsible='合成测试教师',student_ids=[s.id for s in group],assessment_start='2025-09-01',assessment_end='2026-08-31',test_only=True)
            pr=domain.create(db,principal,payload);body=deepcopy(pr.body);rules=active_rules(body);stamp=now()
            # Add a fourth-level public-position taxonomy to exercise ancestor max.
            nodes=deepcopy(rules['nodes']);pos=next(n for n in nodes if n['id']=='positions');pos['name']='岗位任职'
            # The production template already includes depth-five paths under activities.
            body['state']='review';body['schedule']=dict(opens_at=(current-timedelta(days=7)).isoformat(),deadline_at=(current-timedelta(hours=1)).isoformat(),appeal_deadline=(current+timedelta(days=3)).isoformat())
            body['source_documents']=[dict(id=fixed(f'doc-{ci}'),sha256=src,name='合成来源.pdf',purpose='rules',created_at=stamp,actor_id=account.id)]
            body['public_bindings']=[dict(list_id=l.id,revision=1,bound_at=stamp,actor_id=account.id,contract_hash=rules['contract_hash'],instruction_version_id=None) for l,v in publics]
            grade_rows=[]
            for si,st in enumerate(group):
                index=ci*students+si;first=None
                for k,(cat,score,title) in enumerate(descriptions):
                    eid=fixed(f'evidence-{index}-{k}');first=first or eid
                    date=f'2026-{3+k//4:02d}-{5+k:02d}'
                    status='valid';note=None;fields={}
                    if k==10 and index%5==0:status='manual';score='0';note='测试待定材料：比赛层级未明确，等待人工确认。';counts['manual']+=1
                    if k==11 and index%11==0:cat='penalties';score='-2';title='合成扣分测试记录';note='仅为扣分运算测试，不代表任何个人行为。';counts['deductions']+=1
                    elif k==11 and index%3==0:cat='competition.professional';score='0';fields['duplicate_of']=first;title='同一成果重复佐证';counts['duplicates']+=1
                    has=True if index%3==0 else False if index%3==1 else None
                    # Text is rasterized with an obvious synthetic label; no real
                    # university seals, signatures, logos or identifying data.
                    im=Image.new('RGB',(760,480),'white');draw=ImageDraw.Draw(im)
                    draw.rectangle((24,24,736,456),outline='#555555',width=2)
                    draw.text((50,46),'合成测试材料 · 不作证明',font=f26,fill='black')
                    draw.text((50,110),title,font=f26,fill='black')
                    for y,line in [(180,st.name+'　'+st.number),(220,'材料日期：'+date),(260,'场景编号：'+f'{index:04d}-{k:02d}'),(308,'材料内容仅用于导出和分页测试。'),(356,'盖章字段：'+('是（合成标记）' if has else '否' if has is False else '未判定'))]:draw.text((50,y),line,font=f20 if y<300 else f15,fill='black')
                    image=BytesIO();im.save(image,'JPEG',quality=78);rsha=domain.store.put(db,image.getvalue(),'image')['sha256']
                    ann=Annotation(status=status,material_date=date,description='合成测试：'+title,category_id=cat,rule_item=title,scenario='测试场景的对应认定情形',rule_source='导出验收合成来源文件；非正式评定',score=score,outcome_key=f'fixture-{index}-{k}',has_stamp=has,stamp_text=stamps[0] if has else None,human_note=note,**fields).model_dump()
                    version=dict(id=fixed(f'ann-{index}-{k}'),version=1,data=ann,reviewed=True,actor_id=account.id,actor_kind='teacher',agent_name=None,created_at=stamp,rule_version_id=rules['id'],contract_hash=rules['contract_hash'],instruction_version_id=None,idempotency_key=f'ann-{index}-{k}')
                    body['evidence'].append(dict(id=eid,student_id=st.id,resource_sha=rsha,submitted_at=stamp,submitted_by=account.id,parent_id=None,request_id=None,idempotency_key=f'image-{index}-{k}',incremental=False,annotations=[version]))
                    counts['personal_images']+=1
                entries=[];numerator=D(0);den=D(0)
                for k,course in enumerate(courses):
                    score=str(65+(index*7+k*3)%35) if k<10 else '良好' if k==10 else '合格';excluded=(index%17==0 and k==9)
                    entries.append(dict(course_id=course['id'],score=score,excluded=excluded,exclusion_reason='合成测试：已修课程按认定免计入' if excluded else '',substitute_name='合成替代课程' if index%13==0 and k==0 else None,substitution_reason='合成测试认定' if index%13==0 and k==0 else ''))
                    if k!=11 and not excluded:numerator+=(D(score) if k<10 else D(85))*D(course['credits']);den+=D(course['credits'])
                grade_rows.append(dict(student_id=st.id,entries=entries));counts['course_rows']+=len(entries)
                with localcontext() as ctx:
                    ctx.prec=48;average=numerator/den;academics=average*D('.86')
                    creative=D(0) if index%5==0 else D('.3');innovation=D('2.8')+creative
                    dorm=D(0) if index%11==0 or index%3==0 else D('.5');activities=min(D(4),D('3.9')+dorm)
                    final=D(5)+academics+innovation+D('1.6')+activities-(D(2) if index%11==0 else D(0))
                expected[st.id]=dict(total=str(final),professional='0.8',projects='1',sports='2',activities=str(activities),innovation=str(innovation),ready=index%5!=0)
                body['receipts'].append(dict(id=fixed('receipt-'+str(index)),student_id=st.id,evidence_ids=[e['id'] for e in body['evidence'] if e['student_id']==st.id],created_at=stamp))
            av=AcademicWrite(revision=1,resource_sha=src,courses=courses,students=grade_rows,confirmed=True,note='完整合成课程数据，不是真实成绩。').model_dump();av.pop('revision')
            body['academic_versions']=[dict(id=fixed('academic-'+str(ci)),version=1,actor_id=account.id,created_at=stamp,**av)]
            domain.save(db,principal,pr,body,pr.revision,'fixture.bulk-seed',{'synthetic':True,'count':len(body['evidence']),'note':'测试专用批量构造，不是上传性能测试'})
            db.commit();pids.append(pr.id)
            print('seeded',ci+1,'/',classes,flush=True)
        timings['synthetic_seed_seconds']=round(time.perf_counter()-seed,3)
        checks.append('合成身份、公共记录、课程成绩和图片按业务Schema验证后批量建立')
        start=time.perf_counter();allrecords=[];catalog=[]
        for ci,pid in enumerate(pids):
            pr=db.get(Project,pid);computed=domain.compute(db,pr);roster=domain.roster(db,pr.body['student_ids'])
            for st in roster:
                result=computed['students'][st['id']];exp=expected[st['id']];vals={n['id']:n['value'] for n in result['nodes']}
                assert abs(D(result['total'])-D(exp['total']))<D('1e-30'),(st['number'],result['total'],exp['total'])
                for key,nid in [('professional','competition.professional'),('projects','research.projects'),('sports','activities.sports'),('activities','activities'),('innovation','innovation')]:assert D(vals[nid])==D(exp[key])
                assert result['ready']==exp['ready']
                records=result_records(pr,st,result,active_rules(pr.body)['nodes'],college['name']);allrecords.extend(records)
                assert len(records)==19  # twelve images + six public rows + average
            catalog.append([pr.title,pr.id,len(roster),len(pr.body['evidence']),computed['input_hash']])
            if ci==0:
                first_project=pr;first_calc=computed;first_roster=roster
            reports.append(dict(project=pr.title,students=len(roster),images=len(pr.body['evidence']),ready=computed['ready'],issues=computed['issue_count']))
        timings['all_calculations_and_record_assembly_seconds']=round(time.perf_counter()-start,3)
        checks.append(f'{len(allstudents)}名学生与独立Decimal期望值逐一比较：总分及5个分类小计一致')
        assert len(allrecords)==classes*students*19
        start=time.perf_counter();summary=result_xlsx(db,domain,first_project,computed=first_calc)
        (output/'合成01班_多级分类总表.xlsx').write_bytes(summary);timings['class_summary_seconds']=round(time.perf_counter()-start,3)
        sheets=workbook(summary);assert len(sheets)==7
        text=all_text(summary);assert '0.25，取最大值时已舍弃' in text and '0.6，取最大值时已舍弃' in text and '【1】' in text
        checks.append('总表含7张工作表；最大值舍弃标记、原始分值与逐级分类小计均保留')
        start=time.perf_counter();out=xlsx_bytes([records_sheet(allrecords),('工程目录',['工程','工程ID','学生数','图片数','计算输入指纹'],catalog)])
        (output/'合成学院_所有成果.xlsx').write_bytes(out);timings['college_achievements_seconds']=round(time.perf_counter()-start,3)
        # Parse spreadsheet XML without re-exporting or changing the generated file.
        with zipfile.ZipFile(BytesIO(out)) as z:
            root=ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
            assert len(root.findall('{*}sheetData/{*}row'))==len(allrecords)+1
            assert not root.findall('.//{*}f')
        checks.append(f'跨{classes}班所有成果表：{len(allrecords)}行，无公式注入，列含盖章和详细处理记录')
        # Scope exports are invoked over real ASGI authenticated requests.
        start=time.perf_counter();api=teacher.request('GET','/api/export-tables/workspace');assert api.status_code==200
        (output/'合成学院_工程目录.xlsx').write_bytes(api.content)
        for kind in ('achievements','rules','students','submissions','public','academic','appeals','publications','audit'):
            response=teacher.request('GET',f'/api/projects/{first_project.id}/export/table/{kind}');assert response.status_code==200,(kind,response.text[:200])
            workbook(response.content)
            if kind in ('academic','public'):(output/('合成01班_'+{'academic':'课程成绩','public':'公共名单记录'}[kind]+'.xlsx')).write_bytes(response.content)
        # College HTTP report must have exactly the same row count as pure renderer.
        response=teacher.request('GET','/api/export-tables/achievements');assert response.status_code==200
        with zipfile.ZipFile(BytesIO(response.content)) as z:assert len(ET.fromstring(z.read('xl/worksheets/sheet1.xml')).findall('{*}sheetData/{*}row'))==len(allrecords)+1
        timings['authenticated_export_endpoints_seconds']=round(time.perf_counter()-start,3)
        checks.append('所有成果、分类、名册、收件、公共信息、课程、公示、申请、审计及学院导出端点真实鉴权调用通过')
        for category,name in [('positions','岗位任职'),('activities','学生活动')]:
            (output/f'合成01班_{name}分类表.xlsx').write_bytes(result_xlsx(db,domain,first_project,computed=first_calc,category_id=category))
        # Snapshot and export use domain methods; pre-existing evidence isn't rewritten.
        before=canonical(first_project.body['evidence']);domain.publish(db,principal,first_project,Publish(revision=first_project.revision,label='合成测试阶段性公示',mode='provisional'));db.commit()
        snap=first_project.body['snapshots'][-1];snapshot_ids[first_project.id]=snap['id']
        assert canonical(first_project.body['evidence'])==before
        (output/'合成01班_固定公示版本总表.xlsx').write_bytes(result_xlsx(db,domain,first_project,snapshot=snap))
        st=first_roster[1]  # varied caps; excludes internal long-test record
        result=first_calc['students'][st['id']]
        start=time.perf_counter();pdf=personal_pdf(domain,first_project,st,result,'合成测试阶段性公示',rule_nodes=active_rules(first_project.body)['nodes'])
        (output/'合成学生_个人图片材料.pdf').write_bytes(pdf);timings['personal_pdf_seconds']=round(time.perf_counter()-start,3)
        (output/'合成学生_个人成果表.xlsx').write_bytes(result_xlsx(db,domain,first_project,snapshot=snap,student_id=st['id']))
        # Worst-length fields, same image on explicit continuation pages. Not a new award.
        long=deepcopy(result);long['materials']=long['materials'][:1]
        a=long['materials'][0]['annotation'];a.update(description='长材料说明，保留原文。'*450,has_stamp=True,stamp_text='合成盖章文字记录。'*300,rule_item='细则项目说明。'*250,scenario='具体获奖情形。'*250,human_note='人工审核说明。'*1000)
        Annotation.model_validate(a)
        # This is a report-renderer boundary fixture, not an Annotation schema bypass
        # in the deployed API. Sizes below the respective API character limits.
        (output/'边界测试_长说明分页.pdf').write_bytes(personal_pdf(domain,first_project,st,long,'长说明分页边界测试',rule_nodes=active_rules(first_project.body)['nodes']))
        start=time.perf_counter();archive,manifest=export_project(db,domain,first_project);z,m,payload=verify_archive(archive)
        assert len(payload['body']['evidence'])==students*12
        assert len([n for n in z.namelist() if n.startswith('objects/')])==students*12+1
        (output/'合成01班_导出验收工程.zcp').write_bytes(archive);timings['class_archive_seconds']=round(time.perf_counter()-start,3)
        checks.append('班级工程校验：图片资源唯一、公共原件只存一份，盖章字段与标注历史写入归档')
        start=time.perf_counter();ip,credentials=import_project(db,principal,domain,archive);db.commit()
        restored=domain.compute(db,ip)
        for sid,before_result in first_calc['students'].items():
            after=restored['students'][sid];assert after['total']==before_result['total']
            assert [(m['annotation'].get('has_stamp'),m['annotation'].get('stamp_text')) for m in after['materials']]==[(m['annotation'].get('has_stamp'),m['annotation'].get('stamp_text')) for m in before_result['materials']]
        timings['class_archive_reimport_seconds']=round(time.perf_counter()-start,3)
        checks.append(f'{students}人、{students*12}张图片工程重新导入后，逐人分数与盖章字段保持一致；导入件未自动公示')
        tmpbuf=BytesIO()
        with zipfile.ZipFile(tmpbuf,'w',zipfile.ZIP_DEFLATED) as z:
            for kind in ('students','public','academic'):
                for fmt in ('xlsx','csv'):
                    data,name,mime=template_bytes(kind,active_rules(first_project.body)['nodes'] if kind=='public' else None,first_roster if kind in ('public','academic') else None,fmt)
                    z.writestr(name,data)
            z.writestr('请先阅读.txt','第一张工作表是空白填写区，从第二行录入。\n示例只在单独示例工作表，不会自动导入。\n所有参考学生均为合成数据，不应导入正式学院。\n从正式系统页面下载的模板带有该工程的实际参考名册和分类。\n'.encode('utf-8'))
        (output/'导入模板_三类六份.zip').write_bytes(tmpbuf.getvalue())
        checks.append('名册、公共名单、课程三种空白模板同时提供XLSX和UTF-8 CSV')
    app.state.engine.dispose();temp.cleanup();timings['total_seconds']=round(time.perf_counter()-started,3)
    report=dict(version='1.0.0rc4',synthetic_only=True,method='Bulk fixture schema validation + deterministic domain calculation + authenticated ASGI export endpoints; not concurrent production load.',scale=dict(classes=classes,**dict(counts),achievement_rows=len(allrecords)),checks=checks,timings_seconds=timings,projects=reports,not_validated=['并发导出峰值','学校实际数据库与对象存储IO','Excel/WPS桌面应用实际打印','生产HTTPS浏览器下载'])
    (output/'规模测试结果.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=ROOT/'artifacts/export-scale');parser.add_argument('--classes',type=int,default=10);parser.add_argument('--students',type=int,default=70);args=parser.parse_args()
    if not 1<=args.classes<=20 or not 1<=args.students<=150:parser.error('测试班级数1—20；每班1—150人。')
    run(args.output,args.classes,args.students)
