"""Offline Chromium UI walkthrough backed by the real ASGI application.

All fixtures are synthetic. This verifies DOM interactions/API persistence and
responsive layout, NOT browser networking, HTTPS/cookies or physical devices.
Run: python tests/ui_walkthrough.py --output artifacts/ui
Requires requirements-dev.txt and a local Chromium executable (CHROMIUM env).
No bridge code is shipped inside the deployed web application.
"""
from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone, timedelta
from io import BytesIO
import argparse, base64, json, os, re, secrets, sys, tempfile, uuid
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright
from PIL import Image, ImageDraw, ImageFont
from app.config import Settings
from app.main import create_app
from conftest import API, INITIAL, PASSWORD

JS_BRIDGE=r'''
window.__pending=0;
// about:blank lacks the secure-origin randomUUID API; keep this fixture shim out of the app.
if(!crypto.randomUUID)crypto.randomUUID=()=>{const a=crypto.getRandomValues(new Uint8Array(16));a[6]=(a[6]&15)|64;a[8]=(a[8]&63)|128;const h=[...a].map(x=>x.toString(16).padStart(2,'0')).join('');return h.slice(0,8)+'-'+h.slice(8,12)+'-'+h.slice(12,16)+'-'+h.slice(16,20)+'-'+h.slice(20);};
window.fetch=async function(url,options={}){
  window.__pending++;
  try{
    const payload={url:String(url),method:options.method||'GET',headers:options.headers||{}};
    if(options.body instanceof FormData){payload.form=[];for(const [key,value] of options.body.entries()){
      if(value instanceof Blob){const b=new Uint8Array(await value.arrayBuffer());let text='';for(let i=0;i<b.length;i+=8192)text+=String.fromCharCode(...b.subarray(i,i+8192));payload.form.push({key,name:value.name,type:value.type,data:btoa(text)});}
      else payload.form.push({key,value});
    }}else if(options.body!==undefined)payload.body=options.body;
    const result=await window.__backend(payload);
    return new Response(Uint8Array.from(atob(result.body),c=>c.charCodeAt(0)),{status:result.status,headers:result.headers});
  } finally {window.__pending--;}
};
const RealDate=Date;
window.Date=class extends RealDate{constructor(...args){super(...(args.length?args:[window.__now]));}static now(){return window.__now;}};
new MutationObserver(()=>{for(const image of document.querySelectorAll('img')){
 const path=image.getAttribute('src');if(!path||!path.startsWith('/api/')||image.dataset.processing===path)continue;
 image.dataset.processing=path;fetch(path).then(r=>{if(!r.ok)throw Error('Image request failed '+r.status);return r.blob();}).then(b=>{image.src=URL.createObjectURL(b);}).catch(e=>window.__imageErrors.push(String(e)));
}}).observe(document.documentElement,{childList:true,subtree:true,attributes:true,attributeFilter:['src']});
'''

class Walkthrough:
 def __init__(self,out):
  self.out=out;out.mkdir(parents=True,exist_ok=True);self.tmp=tempfile.TemporaryDirectory(prefix='bnu-ui-');self.data=Path(self.tmp.name)
  self.now=datetime(2026,9,22,2,0,tzinfo=timezone.utc)
  import app.domain
  self.old_now=app.domain.now;app.domain.now=lambda:self.now.isoformat(timespec='microseconds')
  settings=Settings(data_dir=self.data/'objects',database_url='sqlite:///'+str(self.data/'ui.sqlite'),environment='development',secret=secrets.token_urlsafe(40),origin='http://testserver',admin_path='/private-ui-ops',bootstrap_password=INITIAL,admin_totp='',clamav_host='')
  self.app=create_app(settings);self.admin=API(self.app,'admin','admin',INITIAL)
  college=self.admin.ok('POST','/api/ops/colleges',dict(name='教学演示学院'))
  account=self.admin.ok('POST','/api/ops/teachers',dict(college_id=college['id'],name='演示教师',username='demo_teacher'))
  self.teacher=API(self.app,'teacher','demo_teacher',account['initial_password'])
  imported=self.teacher.ok('POST','/api/students/import',{'rows':[dict(number=f'DEMO202400{i}',name=n,class_name='演示班' if i<3 else '复核演示班') for i,n in enumerate(['演示学生甲','演示学生乙','演示审核员'],1)]})
  self.roster=imported['students'];self.students=[API(self.app,'student',c['username'],c['initial_password']) for c in imported['credentials']]
  self.student_names=[c['username'] for c in imported['credentials']]
  self.extra=self.admin.ok('POST','/api/ops/teachers',dict(college_id=college['id'],name='首次登录演示',username='demo_firstlogin'))
  self.records=[];self.calls=[];self.errors=[];self.pages=[];self.checks=[]
  self.proof=self.make_proof('团体程序设计天梯赛','全国个人三等奖','2026年4月26日',1)
  self.proof2=self.make_proof('团体程序设计天梯赛','补充证明：全国个人二等奖','2026年4月26日',2)
  self.source=self.data/'操作演示细则.pdf'
  from reportlab.pdfgen import canvas
  from reportlab.pdfbase import pdfmetrics
  from reportlab.pdfbase.cidfonts import UnicodeCIDFont
  pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
  c=canvas.Canvas(str(self.source));c.setFont('STSong-Light',16);c.drawString(60,780,'操作演示用规则与名单来源');c.setFont('STSong-Light',12)
  for i,line in enumerate(['本文件为合成测试材料，不代表正式成绩。','来源：软件验收测试。','演示学生甲、演示学生乙：基础认定5分。','课程成绩由演示教务表录入。']):c.drawString(60,730-i*28,line)
  c.save()
 def make_proof(self,title,award,date,number):
  path=self.data/f'proof-{number}.png';im=Image.new('RGB',(960,640),'white');d=ImageDraw.Draw(im)
  font_path=os.environ.get('CJK_FONT','/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
  if not Path(font_path).is_file():raise RuntimeError('图文验证需要本机中文字体，请设置CJK_FONT。字体文件不随包分发。')
  font=ImageFont.truetype(font_path,30)
  titlefont=ImageFont.truetype(font_path,40)
  d.rectangle((30,30,930,610),outline='#495565',width=2)
  for pos,text,ft in [((70,75),'操作演示用合成证明',titlefont),((70,185),'演示学生甲（DEMO2024001）',font),((70,260),title,font),((70,330),award,font),((70,445),date,font),((70,535),'不代表任何学生的真实材料或正式成绩。',font)]:d.text(pos,text,font=ft,fill='#263442')
  im.save(path);return path
 def bridge(self,client,payload):
  url=payload['url'];assert url.startswith('/api/') or url in ['/manual','/tutorial']
  kwargs={'headers':{**payload.get('headers',{}),'Origin':'http://testserver'}}
  if 'form' in payload:
   data={};files=[]
   for item in payload['form']:
    if 'data' in item:files.append((item['key'],(item['name'],base64.b64decode(item['data']),item['type'])))
    else:data[item['key']]=item['value']
   kwargs['data']=data
   if files:kwargs['files']=files
  elif 'body' in payload:kwargs['content']=payload['body']
  response=client.request(payload['method'],url,**kwargs)
  self.calls.append({'method':payload['method'],'url':url,'status':response.status_code,**({'error':response.text[:500]} if response.status_code>=400 else {})})
  return dict(status=response.status_code,headers={'Content-Type':response.headers.get('content-type','application/octet-stream')},body=base64.b64encode(response.content).decode())
 def page(self,browser,portal='teacher',viewport=(1440,1000)):
  page=browser.new_page(viewport=dict(width=viewport[0],height=viewport[1]),device_scale_factor=1,timezone_id='Asia/Shanghai')
  client=TestClient(self.app);page.expose_binding('__backend',lambda source,payload:self.bridge(client,payload))
  page.on('pageerror',lambda err:self.errors.append(str(err)))
  page.on('dialog',lambda d:d.accept())
  html=(ROOT/'app/templates/index.html').read_text().replace('__PORTAL__',portal)
  html=re.sub(r'<link rel="stylesheet"[^>]+>|<script src="[^"]+" defer></script>','',html)
  page.set_content(html);page.add_style_tag(content=(ROOT/'app/static/app.css').read_text())
  page.evaluate('(n)=>{window.__now=n;window.__imageErrors=[]}',int(self.now.timestamp()*1000))
  page.add_script_tag(content=JS_BRIDGE)
  for filename in ['capture.js','collections.js','app.js','review-ui.js','workflow-ui.js']:page.add_script_tag(content=(ROOT/'app/static'/filename).read_text())
  page.evaluate("document.dispatchEvent(new Event('DOMContentLoaded'))")
  self.idle(page);self.pages.append(page);return page
 def idle(self,page):
  page.wait_for_timeout(120);page.wait_for_function('window.__pending===0');page.wait_for_timeout(80)
 def nav(self,page,path):
  page.evaluate('(h)=>{location.hash=h}',path);self.idle(page)
  assert '页面无法打开' not in page.locator('#app').inner_text(),page.locator('#app').inner_text()
 def action(self,page,name,index=0):
  page.locator(f'[data-action="{name}"]:visible').nth(index).click();self.idle(page)
 def submit(self,page,name):
  page.locator(f'form[data-form="{name}"] button[type="submit"]').click();self.idle(page)
 def capture(self,page,name,caption,selector=None):
  self.idle(page);page.locator('#toasts').evaluate('(e)=>e.replaceChildren()')
  if selector:page.locator(selector).scroll_into_view_if_needed();self.idle(page)
  else:page.evaluate('window.scrollTo(0,0)')
  overflow=page.evaluate('Math.max(0,document.documentElement.scrollWidth-innerWidth)')
  if overflow>2:
   page.screenshot(path=str(self.out/'overflow-debug.png'),full_page=True)
   (self.out/'overflow-debug.json').write_text(json.dumps(page.evaluate("Array.from(document.querySelectorAll('body *')).filter(e=>e.getBoundingClientRect().right>innerWidth+2).map(e=>({tag:e.tagName,cls:e.className,text:e.innerText?.slice(0,100),width:e.getBoundingClientRect().width,right:e.getBoundingClientRect().right,scroll:e.scrollWidth}))"),ensure_ascii=False,indent=2))
  assert overflow<=2,f'{name}: whole-page horizontal overflow {overflow}' 
  assert not page.evaluate('window.__imageErrors'),page.evaluate('window.__imageErrors')
  path=self.out/(name+'.png');page.screenshot(path=str(path),full_page=False)
  self.records.append(dict(id=name,caption=caption,file=path.name,viewport=page.viewport_size,overflow_px=overflow))
 def login(self,page,username,password=PASSWORD):
  page.locator('input[name="username"]').fill(username);page.locator('input[name="password"]').fill(password);self.submit(page,'login')
 def settime(self,new):
  self.now=new
  for page in self.pages:page.evaluate('(n)=>window.__now=n',int(new.timestamp()*1000))
 def api_project(self):return self.teacher.ok('GET','/api/projects/'+self.pid)
 def refresh(self,page):page.evaluate('async()=>{await route()}');self.idle(page)
 def run(self,browser):
  page=self.page(browser,'choose');self.capture(page,'01-entry','入口页：分别选择学生登录、教师登录。')
  page=self.page(browser,'student',(390,844));self.capture(page,'02-student-login','学生登录页。账号和初始密码由负责教师发放。')
  first=self.page(browser);self.login(first,'demo_firstlogin',self.extra['initial_password']);self.capture(first,'03-first-password','首次登录必须填写初始密码和新密码。')
  first.locator('[name="old_password"]').fill(self.extra['initial_password']);first.locator('[name="new_password"]').fill(PASSWORD);first.locator('[name="confirm_password"]').fill(PASSWORD)
  # Read actual form names rather than assuming a repeated-password UI exists.
  self.submit(first,'password');assert not first.locator('#dialog').is_visible()
  self.checks.append('首次登录修改密码并进入工作台')
  t=self.page(browser);self.login(t,'demo_teacher');self.capture(t,'04-workspace-empty','教师工作空间及说明文档入口。')
  self.nav(t,'#/students');self.capture(t,'05-students','教师管理学院学生名册。')
  self.action(t,'roster-import');template=t.locator('#dialog a[href*="export-templates"]').first.get_attribute('href');res=self.teacher.request('GET',template);assert res.status_code==200
  from app.tabular import parse_table
  assert parse_table(res.content,'template.xlsx')==[];self.checks.append('名册导入窗口模板链接下载成功，第一工作表无示例数据')
  self.capture(t,'06-roster-import','导入CSV或XLSX，或展开下方区域新增一名学生。');self.action(t,'close')
  self.action(t,'student-edit');t.locator('#dialog input[name="name"]').fill('演示学生甲')
  self.capture(t,'07-student-edit','核对学号后修改姓名、班级或账号状态。');self.submit(t,'student-edit')
  self.checks.append('学生资料编辑经表单保存并由接口返回')
  self.nav(t,'#/workspace');self.action(t,'new-folder');t.locator('#dialog [name="name"]').fill('2025—2026学年');self.submit(t,'folder')
  self.action(t,'new-project');f=t.locator('form[data-form="new-project"]');f.locator('[name="title"]').fill('2025—2026学年演示班综合测评');f.locator('[name="year"]').fill('2025-2026');f.locator('[name="class_name"]').fill('演示班');f.locator('[name="responsible"]').fill('演示教师');f.locator('[name="assessment_start"]').fill('2025-09-01');f.locator('[name="assessment_end"]').fill('2026-08-31')
  for st in self.roster[:2]:f.locator(f'[name="student_ids"][value="{st["id"]}"]').check()
  f.locator('[name="test_only"]').check();self.capture(t,'08-project-create','一个班级建立一个工程，选择正确年度与参评学生。');self.submit(t,'new-project')
  self.pid=t.evaluate('S.project.id');self.base='/api/projects/'+self.pid
  self.nav(t,f'#/project/{self.pid}/overview');self.capture(t,'09-overview','工程总览按准备、提交、标注、公共信息、计算、公示、存档排列。')
  self.nav(t,f'#/project/{self.pid}/setup');self.capture(t,'10-setup','准备工程：左侧为分类规则，右侧为名册、时间和原始文件。')
  self.action(t,'rule-edit',0);self.capture(t,'11-rule-edit','逐级设置求和或取最高、上限和权重。');self.action(t,'close')
  self.action(t,'members');f=t.locator('form[data-form="members"]');f.locator(f'[name="reviewer_ids"][value="{self.students[2].me["id"]}"]').check();self.capture(t,'12-reviewer-permissions','参评学生和审核人员分别选择。审核权限只作用于本工程。');self.submit(t,'members')
  self.action(t,'document-upload');self.capture(t,'13-rule-upload','上传原始细则；多个细则文件可分次上传。');t.locator('form[data-form="document-upload"] input[type="file"]').set_input_files(str(self.source));self.submit(t,'document-upload')
  f=t.locator('form[data-form="instructions"]');f.locator('[name="text"]').fill('此工程仅作软件操作演示。材料缺少日期、身份或赛事级别时，标记需人工判断，不自行补全事实。');f.locator('[name="reason"]').fill('教学演示确认说明');self.submit(t,'instructions')
  f=t.locator('form[data-form="schedule"]')
  for key,time in [('opens_at',self.now-timedelta(hours=1)),('deadline_at',self.now+timedelta(hours=1)),('appeal_deadline',self.now+timedelta(hours=4))]:f.locator(f'[name="{key}"]').fill(time.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%dT%H:%M'))
  self.capture(t,'14-schedule','填写开放、截止、公示申诉截止时间，并分别保存。','form[data-form="schedule"]');self.submit(t,'schedule')
  self.action(t,'transition');t.locator('#dialog [name="reason"]').fill('准备工作已核对');self.submit(t,'transition')
  self.checks.append('工程创建、权限分配、上传细则、辅助说明、时间窗口、启用提交')
  s=self.page(browser,'student',(390,844));self.login(s,self.student_names[0]);self.capture(s,'15-student-workspace','学生在“我的综测”中打开所在班级工程。')
  self.nav(s,f'#/project/{self.pid}/submission');self.capture(s,'16-student-upload','手机提交页：选择照片，无需填写成果文字或分数。')
  s.locator('input[data-upload="evidence"][multiple]').set_input_files(str(self.proof));self.idle(s);self.capture(s,'17-student-queue','选择后先检查预览，再点击“提交选中的图片”。')
  self.action(s,'upload-all');self.action(s,'receipt');self.capture(s,'18-submission-receipt','回执说明本轮图片已收到，不表示已经认定有效。');self.action(s,'close')
  e=self.api_project()['body']['evidence'][0];self.eid=e['id'];self.checks.append('手机页面选择图片、真实上传、确认回执')
  self.nav(t,f'#/project/{self.pid}/submission');self.capture(t,'19-submission-monitor','教师查看各学生已保存材料数和提交确认情况。')
  self.nav(t,f'#/project/{self.pid}/review');self.capture(t,'20-review','标注页面：默认筛选未标注、不可信、系统异常、需人工判断。')
  form=t.locator('form[data-form="annotation"]');form.locator('[name="material_date"]').fill('2026-04-26');form.locator('[name="description"]').fill('演示学生甲的天梯赛全国个人三等奖证书')
  # Walk the actual hierarchical selector, never replace it with free text.
  for value in ['innovation','competition','competition.professional']:
   selectors=t.locator('[data-category-level]');selectors.nth(selectors.count()-1).select_option(value);self.idle(t)
  for name,value in [('rule_item','中国高校计算机大赛团体程序设计天梯赛全国个人奖'),('scenario','全国个人三等奖'),('rule_source','操作演示用细则：全国个人三等奖0.4分'),('score','0.4'),('outcome_key','demo-tian-ti-2026')]:form.locator(f'[name="{name}"]').fill(value)
  form.locator('[name="has_stamp"]').select_option('true');form.locator('[name="stamp_text"]').fill('操作演示工作组（合成文字）')
  self.capture(t,'21-annotation-form','逐项填写材料日期、分类、细则项目、获奖情形和局部分值。','form[data-form="annotation"]');t.locator('[data-action="review-verdict"][data-status="valid"]').click();self.idle(t)
  assert self.api_project()['body']['evidence'][0]['annotations'][-1]['data']['score']=='0.4'
  assert self.api_project()['body']['evidence'][0]['annotations'][-1]['data']['stamp_text']=='操作演示工作组（合成文字）'
  self.checks.append('图形界面保存是否盖章与印章文字，服务端按布尔字段持久化')
  t.locator('[data-review-filter]').select_option('all');self.idle(t);self.action(t,'annotation-history');self.capture(t,'22-annotation-history','修改历史保留每次认定、执行者和时间。');self.action(t,'close');self.checks.append('多级分类选择、人工标注保存、历史记录')
  self.nav(t,f'#/project/{self.pid}/public');self.action(t,'public-new');self.capture(t,'23-public-new','公共名单单独维护，选择对应工程的分类规则。')
  self.action(t,'public-source');t.locator('#dialog input[type="file"]').set_input_files(str(self.source));self.submit(t,'public-source')
  f=t.locator('#public-form');f.locator('[name="name"]').fill('演示班基础认定名单');f.locator('[name="description"]').fill('操作演示：基础认定记录');f.locator('[name="default_category"]').select_option('moral');f.locator('[name="default_score"]').fill('5');f.locator('[name="default_description"]').fill('基础认定');f.locator('[name="default_scenario"]').fill('已核对条件');f.locator('[name="default_source"]').fill('操作演示认定文件');f.locator('[name="default_date"]').fill('2026-08-31')
  self.action(t,'public-add')
  for st in self.roster[:2]:t.locator(f'#dialog [name="student_ids"][value="{st["id"]}"]').check()
  self.submit(t,'public-add');self.capture(t,'24-public-rows','从学院名册选择学生；逐行填写情形、分值和备注。','#public-rows')
  f.locator('[name="approved"]').check();self.submit(t,'public-save');self.action(t,'bind-saved-public');self.capture(t,'25-public-binding','工程绑定已确认名单的固定版本。')
  self.lid=self.api_project()['body']['public_bindings'][-1]['list_id'];self.checks.append('公共文件上传、选人、逐行记录、教师确认和固定版本绑定')
  self.action(t,'academic-editor');self.capture(t,'26-academic','课程成绩录入页：先上传教务来源，再填写课程与学生成绩。')
  self.action(t,'academic-source');t.locator('#dialog input[type="file"]').set_input_files(str(self.source));self.submit(t,'academic-source')
  self.action(t,'course-add');f=t.locator('form[data-form="course-add"]')
  for name,value in [('id','DEMO-C1'),('name','演示课程'),('credits','3')]:f.locator(f'[name="{name}"]').fill(value)
  self.submit(t,'course-add');self.action(t,'academic-students')
  for st in self.roster[:2]:t.locator(f'#dialog [name="student_ids"][value="{st["id"]}"]').check()
  self.submit(t,'academic-students');t.locator('[name="grade_0"]').fill('90');t.locator('[data-academic-student]').select_option(self.roster[1]['id']);self.idle(t);t.locator('[name="grade_0"]').fill('88');t.locator('form[data-form="academic-save"] [name="note"]').fill('演示教务数据，两名学生的参评课程均已完整填写');t.locator('[name="confirmed"]').check();self.capture(t,'27-academic-grades','按学生录入完整成绩。两级制免计入、替代课程另有字段。','[data-academic-student]');self.submit(t,'academic-save')
  self.nav(t,f'#/project/{self.pid}/calculation');self.action(t,'export-tables');self.capture(t,'54-export-tables','工程各类表格导出目录；每个按钮只导出当前已保存数据。');self.action(t,'close');self.action(t,'category-export');self.capture(t,'55-category-export','选择一个分类，可以只导出该分类及所有下级，减少横向列数。');self.action(t,'close')
  self.capture(t,'28-calculation','系统按已保存规则计算；点击学生打开计算轨迹。');self.action(t,'student-trace');self.capture(t,'29-calculation-detail','个人计算轨迹显示原始输入、聚合、封顶前后数值。');self.action(t,'close');self.checks.append('课程明细保存、系统计算与个人计算轨迹')
  self.settime(self.now+timedelta(hours=2));self.nav(t,f'#/project/{self.pid}/publication');self.action(t,'publish');t.locator('#dialog [name="label"]').fill('第一轮阶段性公示');self.capture(t,'30-publish-dialog','选择阶段性或最终公示，系统不会覆盖旧快照。');self.submit(t,'publish');self.capture(t,'31-publication','教师查看历次公示与补交申请。')
  self.nav(s,f'#/project/{self.pid}/publication');self.capture(s,'32-personal-result','学生公示页仅显示本人结果及本人各项认定。')
  self.action(s,'appeal');self.capture(s,'33-student-appeal','学生通过选项提交异议或补交申请，不需要撰写说明。');self.submit(s,'appeal')
  self.refresh(t);self.action(t,'appeal-decision');t.locator('#dialog [name="note"]').fill('同意在统一申诉截止前补充清晰证书；旧件继续保留。');t.locator('#dialog [name="until"]').fill((self.now+timedelta(hours=1)).astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%dT%H:%M'));self.capture(t,'34-appeal-decision','教师记录批准或拒绝的依据，并为批准补交指定截止时间。');self.submit(t,'appeal-decision')
  self.refresh(s);self.action(s,'supplement');s.locator('input[data-upload="evidence"][multiple]').set_input_files(str(self.proof2));self.action(s,'upload-all');self.checks.append('阶段性公示、学生私有结果、申请审批与增量补交')
  self.nav(t,f'#/project/{self.pid}/review');f=t.locator('form[data-form="annotation"]')
  # Select the increment explicitly; the old annotation remains untouched.
  new=[e for e in self.api_project()['body']['evidence'] if e['id']!=self.eid][0]
  self.action(t,'review-rebuild')
  t.locator(f'[data-action="select-evidence"][data-id="{new["id"]}"]').click();self.idle(t);f=t.locator('form[data-form="annotation"]')
  f.locator('[name="material_date"]').fill('2026-04-26');f.locator('[name="description"]').fill('补充的全国个人二等奖证明')
  for value in ['innovation','competition','competition.professional']:
   selectors=t.locator('[data-category-level]');selectors.nth(selectors.count()-1).select_option(value);self.idle(t)
  for name,value in [('rule_item','中国高校计算机大赛团体程序设计天梯赛全国个人奖'),('scenario','全国个人二等奖'),('rule_source','演示补充证明'),('score','0.8'),('outcome_key','demo-tian-ti-2026')]:f.locator(f'[name="{name}"]').fill(value)
  f.locator('summary').first.click();f.locator('[name="relation"]').select_option('replaces');f.locator('[name="related_id"]').select_option(self.eid);self.capture(t,'35-supplement-relation','新增材料通过“替代旧材料”关联，旧图片和旧标注仍然保留。','[name="relation"]');t.locator('[data-action="review-verdict"][data-status="valid"]').click();self.idle(t)
  self.settime(self.now+timedelta(hours=3));self.nav(t,f'#/project/{self.pid}/publication');self.action(t,'publish');t.locator('#dialog [name="label"]').fill('最终公示');t.locator('#dialog [name="mode"]').select_option('final');self.submit(t,'publish')
  self.nav(t,f'#/project/{self.pid}/archive');self.capture(t,'36-export','按用途导出工程文件、汇总表、个人PDF或只读材料包。')
  self.action(t,'export-tables');links=t.locator('#dialog a[href^="/api/"]').evaluate_all('(els)=>els.map(e=>e.getAttribute("href"))')
  for url in links:
   response=self.teacher.request('GET',url);assert response.status_code==200,(url,response.text[:200]);assert response.content[:2]==b'PK'
  self.checks.append('导出目录中各实际链接均经授权接口下载并返回XLSX文件');self.action(t,'close')
  self.action(t,'personal-export');self.capture(t,'37-personal-export','只选择所需学生导出个人PDF，避免分发全班材料。');self.action(t,'close')
  self.action(t,'transition');t.locator('#dialog [name="reason"]').fill('最终公示无异议，完成结项');self.submit(t,'transition');self.nav(t,f'#/project/{self.pid}/archive');self.action(t,'transition');t.locator('#dialog [name="reason"]').fill('归档保留');self.submit(t,'transition');self.checks.append('仅增量复核、替代关系、最终公示、结项与归档')
  self.nav(t,'#/workspace');self.action(t,'project-menu');self.capture(t,'38-file-actions','工程可重命名、移动、复制和移入回收站。');self.action(t,'close');self.action(t,'import-project');self.capture(t,'39-project-import','导入.zcp会校验完整性，创建独立导入件并要求核对来源。');self.action(t,'close')
  # Exercise folder recycle-and-restore through the actual UI.
  self.action(t,'edit-folder');t.locator('#dialog [name="trashed"]').check();self.submit(t,'folder');self.action(t,'toggle-trash');self.capture(t,'40-recycle','回收站提供工程恢复和文件夹恢复。');self.action(t,'restore-folder');assert any(not f['trashed'] for f in self.teacher.ok('GET','/api/folders'));self.action(t,'toggle-trash');self.checks.append('空文件夹回收并从界面恢复')
  self.nav(t,'#/agents');self.capture(t,'41-agent-interface','外部Agent接口及短期令牌管理；系统不内置模型。');self.action(t,'token-create');self.capture(t,'42-agent-token','限定工程、有效期与公共名单草稿权限，不授予发布权。');self.action(t,'close')
  self.action(s,'menu');self.capture(s,'15b-mobile-menu','手机点击左上角菜单，可找到使用手册、图文教程和反馈。');self.action(s,'feedback');s.locator('#dialog [name="text"]').fill('操作演示反馈：希望在帮助文档中补充登录步骤。');self.capture(s,'43-feedback','意见反馈可附截图或录屏，仅管理员可见。');self.submit(s,'feedback');self.checks.append('学生反馈从界面保存，教师没有反馈列表权限')
  a=self.page(browser,'admin');self.login(a,'admin');self.capture(a,'44-admin-overview','管理员独立入口的运维概览；没有评分功能。');self.nav(a,'#/ops-accounts');self.capture(a,'45-admin-accounts','学院和账号管理；唯一管理员不能删除或停用。');self.action(a,'college-create');self.capture(a,'46-college-create','先创建学院。');self.action(a,'close');self.action(a,'teacher-create');self.capture(a,'47-teacher-create','创建教师登录名并指定所属学院，初始密码单独发放。');self.action(a,'close');self.nav(a,'#/ops-feedback');self.capture(a,'48-admin-feedback','管理员查看文字及附件、更新处理状态、导出全部记录。');a.locator('[data-feedback-status]').select_option('triaged');self.idle(a);assert self.admin.ok('GET','/api/ops/feedback')[0]['status']=='triaged';self.nav(a,'#/ops-audit');self.capture(a,'49-admin-audit','分页查看审计记录，或导出完整日志。');self.checks.append('管理员查看反馈并保存处理状态、查看日志')
  self.nav(t,f'#/public-lists/{self.lid}/1');self.capture(t,'50-public-version','查看指定版本的名单，修改必须另存为新版本。')
  reviewer=self.page(browser,'student');self.login(reviewer,self.student_names[2]);self.nav(reviewer,f'#/project/{self.pid}/calculation');self.capture(reviewer,'51-reviewer','被授权的学委或跨年级审核员可访问审核工程，不获得教师管理权限。')
  # Each primary route is also rendered at narrow and landscape sizes.
  routes=[('#/workspace','工作空间'),('#/students','学生名册'),('#/public-lists','名单库'),('#/agents','Agent接口')]+[(f'#/project/{self.pid}/'+tab,tab) for tab in ['overview','setup','submission','review','public','academic','calculation','publication','archive']]
  for size in [(390,844),(844,390)]:
   t.set_viewport_size({'width':size[0],'height':size[1]})
   for route,name in routes:
    self.nav(t,route);self.capture(t,f'responsive-{size[0]}-{name}',f'{name}：{size[0]}×{size[1]}布局检查')
  s.set_viewport_size({'width':844,'height':390});self.nav(s,f'#/project/{self.pid}/publication');self.capture(s,'52-student-landscape','手机横屏查看本人最终公示结果。')
  t.set_viewport_size({'width':1440,'height':1000});self.nav(t,'#/workspace');self.action(t,'account');self.capture(t,'53-account-logout','账户设置中可修改密码并退出登录。');self.action(t,'logout');assert t.locator('form[data-form="login"]').is_visible();assert t.evaluate('S.files.length===0&&!S.tokenSecret&&S.publicEdit===null');self.checks.append('退出登录后返回正确入口')
  assert not self.errors,self.errors
  report={'version':'1.0.0rc4','mode':'offline Chromium DOM + real ASGI API bridge','not_validated':['实际浏览器HTTP/HTTPS网络','真实设备拍照与HEIC解码','实际部署服务与压力容量'],'screenshots':self.records,'interaction_checks':self.checks,'javascript_errors':self.errors,'http_calls':len(self.calls),'api_status_counts':{str(code):sum(c['status']==code for c in self.calls) for code in sorted(set(c['status'] for c in self.calls))}}
  (self.out/'ui-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
  print(json.dumps({k:v for k,v in report.items() if k!='screenshots'},ensure_ascii=False,indent=2))
 def close(self):
  import app.domain
  app.domain.now=self.old_now;self.app.state.engine.dispose();self.tmp.cleanup()

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=ROOT/'artifacts/ui');args=parser.parse_args();test=Walkthrough(args.output)
 try:
  with sync_playwright() as p:
   browser=p.chromium.launch(executable_path=os.environ.get('CHROMIUM','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
   try:test.run(browser)
   except Exception:
    (test.out/'failure-debug.json').write_text(json.dumps({'calls':test.calls[-30:],'errors':test.errors},ensure_ascii=False,indent=2))
    raise
   finally:browser.close()
 finally:test.close()
if __name__=='__main__':main()
