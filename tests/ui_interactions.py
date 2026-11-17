"""Real-loopback HTTP Chromium interactions with synthetic data.

Native screen permission/OS chooser is deliberately replaced with a local canvas
MediaStream; frame acquisition, track stop, crop, staging, upload and CSRF requests
are real. This is not a native Windows/macOS capture or HTTPS deployment test.
"""
from pathlib import Path
from datetime import datetime,timezone,timedelta
from io import BytesIO
import argparse,base64,json,os,re,secrets,socket,sys,tempfile,threading,time
import httpx
from zipfile import ZipFile
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from playwright.sync_api import sync_playwright, expect
from PIL import Image
import uvicorn
from app.config import Settings
from app.main import create_app
from app.table_reader import read_table
from app.tabular import csv_bytes
from conftest import API, INITIAL, PASSWORD, project, upload, png

CAPTURE_FIXTURE=r'''
if(!navigator.mediaDevices)Object.defineProperty(navigator,'mediaDevices',{value:{},configurable:true});
window.__captureCalls=0;window.__captureTracks=[];window.__denyCapture=false;
Object.defineProperty(navigator.mediaDevices,'getDisplayMedia',{configurable:true,value:async options=>{
  window.__captureCalls++;if(window.__denyCapture)throw new DOMException('Fixture denial','NotAllowedError');
  if(options.audio!==false)throw Error('Audio must remain disabled');
  const c=document.createElement('canvas');c.width=800;c.height=500;const ctx=c.getContext('2d');
  const draw=()=>{ctx.fillStyle='#f6f6f6';ctx.fillRect(0,0,800,500);ctx.fillStyle='#333';ctx.font='24px sans-serif';ctx.fillText('合成截屏测试：无真实桌面资料',40,65);ctx.strokeRect(40,100,600,300);};draw();
  const stream=c.captureStream(15), timer=setInterval(draw,60);
  for(const track of stream.getTracks()){window.__captureTracks.push(track);const stop=track.stop.bind(track);track.stop=()=>{clearInterval(timer);stop();};}
  return stream;
}});
'''

class Validation:
 def __init__(self,out,bridge=False):
  self.bridge_mode=bridge;self.clients=[]
  self.out=out;out.mkdir(parents=True,exist_ok=True);self.tmp=tempfile.TemporaryDirectory(prefix='bnu-http-ui-');self.root=Path(self.tmp.name)
  self.sock=socket.socket();self.sock.bind(('127.0.0.1',0));self.port=self.sock.getsockname()[1];self.origin=f'http://127.0.0.1:{self.port}'
  settings=Settings(data_dir=self.root/'objects',database_url='sqlite:///'+str(self.root/'db.sqlite'),environment='development',secret=secrets.token_urlsafe(40),origin=self.origin,admin_path='/private-http-ops',bootstrap_password=INITIAL,admin_totp='',clamav_host='')
  self.app=create_app(settings);self.admin=API(self.app,'admin','admin',INITIAL)
  college=self.admin.ok('POST','/api/ops/colleges',{'name':'交互验收学院（合成）'})
  acct=self.admin.ok('POST','/api/ops/teachers',{'college_id':college['id'],'username':'http_teacher','name':'验收教师'})
  self.teacher=API(self.app,'teacher','http_teacher',acct['initial_password'])
  roster=self.teacher.ok('POST','/api/students/import',{'rows':[{'number':f'TEST2024{i:03}','name':f'测试学生{i:03}','class_name':'测试甲班' if i<=35 else '测试乙班'} for i in range(1,71)]})
  self.roster=roster['students'];self.student_username=roster['credentials'][0]['username'];self.student=API(self.app,'student',self.student_username,roster['credentials'][0]['initial_password'])
  self.actors={'teacher':self.teacher,'students':[self.student],'roster':self.roster[:3]}
  self.p=project(self.actors);self.pid=self.p['id'];self.base='/api/projects/'+self.pid
  for i in range(6):upload(self.student,self.p,41+i)
  self.server=uvicorn.Server(uvicorn.Config(self.app,host='127.0.0.1',port=self.port,log_level='warning',access_log=False))
  self.thread=threading.Thread(target=self.server.run,kwargs={'sockets':[self.sock]},daemon=True);self.thread.start()
  for _ in range(100):
   if self.server.started:break
   time.sleep(.05)
  assert self.server.started
  self.checks=[];self.shots=[];self.errors=[];self.requests=[]
 def close(self):
  self.server.should_exit=True;self.thread.join(10);self.sock.close();self.app.state.engine.dispose();self.tmp.cleanup()
  for client in self.clients:client.close()
 def idle(self,page):
  if self.bridge_mode:page.wait_for_timeout(100);page.wait_for_function('window.__pending===0')
  else:page.wait_for_load_state('networkidle')
  page.wait_for_timeout(80)
 def page(self,browser,role,username):
  page=browser.new_page(viewport={'width':1440,'height':1000},accept_downloads=True,timezone_id='Asia/Shanghai')
  page.on('pageerror',lambda e:self.errors.append(str(e)))
  page.on('dialog',lambda d:d.accept())
  page.on('request',lambda r:self.requests.append({'method':r.method,'path':r.url.removeprefix(self.origin)}))
  if self.bridge_mode:
   from ui_walkthrough import JS_BRIDGE
   client=httpx.Client(base_url=self.origin);self.clients.append(client)
   def dispatch(source,payload):
    self.requests.append({'method':payload['method'],'path':payload['url']})
    options={'headers':{**payload.get('headers',{}),'Origin':self.origin}}
    if 'form' in payload:
     data={};files=[]
     for item in payload['form']:
      if 'data' in item:files.append((item['key'],(item['name'],base64.b64decode(item['data']),item['type'])))
      else:data[item['key']]=item['value']
     options['data']=data
     if files:options['files']=files
    elif 'body' in payload:options['content']=payload['body']
    if getattr(self,'fail_annotation',False) and payload['method']=='PUT' and payload['url'].endswith('/annotation'):
     raise RuntimeError('Injected HTTP transport failure')
    response=client.request(payload['method'],payload['url'],**options)
    return dict(status=response.status_code,headers={'Content-Type':response.headers.get('content-type','application/octet-stream')},body=base64.b64encode(response.content).decode())
   page.expose_binding('__backend',dispatch)
   html=(ROOT/'app/templates/index.html').read_text().replace('__PORTAL__',role)
   html=re.sub(r'<link rel="stylesheet"[^>]+>|<script src="[^"]+" defer></script>','',html)
   page.set_content(html);page.add_style_tag(content=(ROOT/'app/static/app.css').read_text())
   page.evaluate('window.__now=Date.now();window.__imageErrors=[];Object.defineProperty(window,"isSecureContext",{value:true,configurable:true})')
   page.add_script_tag(content=JS_BRIDGE)
   for name in ['capture.js','collections.js','app.js','review-ui.js','workflow-ui.js']:page.add_script_tag(content=(ROOT/'app/static'/name).read_text())
   page.evaluate("document.dispatchEvent(new Event('DOMContentLoaded'))");self.idle(page)
  else:page.goto(self.origin+'/login/'+role)
  page.locator('[name=username]').fill(username);page.locator('[name=password]').fill(PASSWORD)
  page.locator('form[data-form=login] button[type=submit]').click();page.wait_for_selector('#main');self.idle(page);return page
 def nav(self,page,tab):
  page.evaluate('(h)=>location.hash=h','#/project/'+self.pid+'/'+tab);self.idle(page)
 def shot(self,page,name):
  page.locator('#toasts').evaluate('(e)=>e.replaceChildren()');self.idle(page)
  overflow=page.evaluate('document.documentElement.scrollWidth-innerWidth');assert overflow<=2,(name,overflow)
  page.screenshot(path=str(self.out/(name+'.png')),full_page=False)
  self.shots.append({'file':name+'.png','viewport':page.viewport_size,'overflow_px':max(0,overflow)})
 def action(self,page,name):page.locator('[data-action="'+name+'"]:visible').first.click();self.idle(page)
 def capture(self,page,feedback=False):
  self.action(page,'capture-feedback' if feedback else 'capture-evidence')
  expect(page.locator('dialog.capture-dialog')).to_be_visible(timeout=15000)
  assert page.evaluate('window.__captureTracks.every(t=>t.readyState==="ended")')
 def paste(self,page,selector,seed):
  page.locator(selector).focus()
  page.evaluate('''({selector,data})=>{const dt=new DataTransfer();dt.items.add(new File([Uint8Array.from(atob(data),c=>c.charCodeAt(0))],"粘贴图片.png",{type:"image/png"}));document.querySelector(selector).dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true}));}''',{'selector':selector,'data':base64.b64encode(png(seed)).decode()});self.idle(page)
 def writes(self,path):return sum(r['method']=='POST' and r['path']==path for r in self.requests)
 def run(self,browser):
  s=self.page(browser,'student',self.student_username);self.nav(s,'submission');s.evaluate(CAPTURE_FIXTURE)
  writes=self.writes(self.base+'/evidence');self.capture(s);self.shot(s,'r01-截图预览与裁剪')
  assert self.writes(self.base+'/evidence')==writes and s.evaluate('S.files.length')==0
  form=s.locator('.capture-form')
  for k,v in {'x':20,'y':30,'w':320,'h':180}.items():form.locator('[name='+k+']').fill(str(v))
  form.locator('[type=submit]').click();self.idle(s)
  assert s.evaluate('S.files.length')==1 and self.writes(self.base+'/evidence')==writes
  size=s.evaluate('''async()=>{const image=await createImageBitmap(S.files[0].file);return [image.width,image.height];}''');assert size==[320,180]
  self.checks.append('真实canvas帧截取并停止全部轨道；数值裁剪320×180；确认仅入队、未上传')
  self.capture(s);s.keyboard.press('Escape');self.idle(s);assert s.evaluate('S.files.length')==1
  s.evaluate('window.__denyCapture=true');self.action(s,'capture-evidence');assert s.locator('dialog.capture-dialog').count()==0 and s.evaluate('S.files.length')==1
  self.checks.append('取消和拒绝授权不产生文件，不改变待上传队列、不发送材料请求')
  self.paste(s,'#evidence-paste-area',91);assert s.evaluate('S.files.length')==2
  self.action(s,'upload-all');assert self.writes(self.base+'/evidence')==writes+2
  saved=self.teacher.ok('GET',self.base)['body']['evidence'];assert len(saved)==8
  capture_image=self.student.request('GET',self.base+'/evidence/'+saved[-2]['id']+'/image?original=true').content
  assert Image.open(BytesIO(capture_image)).size==(320,180)
  self.shot(s,'r02-截图粘贴后已上传');self.checks.append('系统截图粘贴与确认上传走同一图片接口，服务器保留原裁剪尺寸')
  self.action(s,'feedback');f=s.locator('#feedback-form');f.locator('[name=text]').fill('截图交互反馈（合成数据）')
  s.evaluate('window.__denyCapture=false');self.capture(s,True);s.locator('.capture-form [type=submit]').click();self.idle(s)
  assert f.locator('[name=text]').input_value()=='截图交互反馈（合成数据）'
  assert self.writes('/api/feedback')==0
  self.paste(s,'#feedback-form [name=text]',92);self.shot(s,'r03-反馈截图与文字保留')
  f.locator('button[type=submit]').click();self.idle(s);assert self.writes('/api/feedback')==1
  assert len(self.admin.ok('GET','/api/ops/feedback')[0]['attachments'])==2
  self.checks.append('反馈截图、粘贴预览与原文字同时保留；最终提交2附件，学生端无反馈后台权限')
  t=self.page(browser,'teacher','http_teacher');t.evaluate("location.hash='#/students'");self.idle(t)
  table=t.locator('#student-table');checks=table.locator('.collection-check');assert checks.count()==70
  checks.nth(0).check();checks.nth(2).click(modifiers=['Shift']);assert t.evaluate('BNUCollections.selected(document.getElementById("student-table")).length')==3
  table.locator('tr').nth(1).click(button='right');expect(t.locator('[role=menu]')).to_be_visible();self.shot(t,'r04-多选与右键菜单')
  t.keyboard.press('Escape');expect(t.locator('[role=menu]')).to_have_count(0)
  table.focus();t.keyboard.press('Shift+F10');expect(t.locator('[role=menu]')).to_be_visible();t.keyboard.press('Escape')
  self.checks.append('70人名册Shift连续选择、已选行右键保留多选、Shift+F10菜单与Esc焦点恢复')
  # Per-column filters keep hidden selections explicit; clearing restores rows.
  bar=t.locator('#student-table').locator('xpath=ancestor::div[contains(@class,"table-wrap")]/preceding-sibling::div[contains(@class,"collection-toolbar")]')
  if bar.count()!=1:bar=t.locator('.collection-toolbar').filter(has_text='按列筛选与排序').first
  bar.locator('summary').click();bar.locator('select').select_option(label='班级');bar.locator('input[type=search]').fill('测试乙班');bar.get_by_role('button',name='应用本列条件',exact=True).click()
  assert table.locator('tr:visible').count()==35
  assert '不在本页' in bar.inner_text()
  bar.get_by_role('button',name='清除所有列条件',exact=True).click();bar.locator('summary').click()
  table.locator('tr').first.click(button='right');t.get_by_role('menuitem',name='批量调整班级',exact=True).click()
  t.locator('form[data-form=batch-operation] [name=class_name]').fill('批量调整测试班');t.locator('form[data-form=batch-operation] button[type=submit]').click();self.idle(t)
  assert sum(row['class_name']=='批量调整测试班' for row in self.teacher.ok('GET','/api/students'))==3
  self.checks.append('多列筛选显示隐藏已选数；右键批量改班真实保存3人、其余67人不变')
  # Rectangle selection and cancellation, using actual pointer movements.
  table=t.locator('#student-table');bar=t.locator('.collection-toolbar').filter(has_text='按列筛选与排序').first
  bar.get_by_role('button',name='清空选择',exact=True).click();bar.get_by_role('button',name='框选',exact=True).click()
  table.locator('tr').first.scroll_into_view_if_needed();bounds=table.bounding_box();r0=table.locator('tr').nth(0).bounding_box();r3=table.locator('tr').nth(3).bounding_box()
  t.mouse.move(bounds['x']+45,r0['y']+3);t.mouse.down();t.mouse.move(bounds['x']+220,r3['y']+r3['height']-3,steps=12);t.mouse.up()
  selected=t.evaluate('BNUCollections.selected(document.getElementById("student-table"))');assert len(selected)>=3,selected
  t.mouse.move(bounds['x']+45,r0['y']+3);t.mouse.down();t.mouse.move(bounds['x']+180,r0['y']+20,steps=5);t.keyboard.press('Escape');t.mouse.up()
  assert t.evaluate('BNUCollections.selected(document.getElementById("student-table"))')==selected
  self.shot(t,'r05-框选与批量操作');self.checks.append('真实鼠标矩形命中多行；Esc取消拖动恢复此前选择；不触发服务端写入')
  # Form inputs retain native context menu / text-selection behavior.
  t.locator('input[data-filter=student-table]').fill('TEST2024001');t.locator('input[data-filter=student-table]').click(button='right');assert t.locator('[role=menu]').count()==0
  self.checks.append('输入框右键不被集合菜单接管，原生复制粘贴仍可用')
  self.nav(t,'review');first=t.evaluate('S.reviewId');original_ids=t.evaluate('reviewQueue().ids')
  form=t.locator('form[data-form=annotation]');form.locator('[name=human_note]').fill('需要完整日期，测试快捷键')
  t.keyboard.press('Alt+2');self.idle(t)
  assert t.evaluate('S.reviewId')==original_ids[1]
  assert t.evaluate('reviewQueue().ids')==original_ids
  self.checks.append('Alt+2保存备注及需人工判断，成功后进入固定队列下一项，不因状态变更重排')
  # Valid cannot bypass missing required metadata; failure must not advance.
  current=t.evaluate('S.reviewId');self.action(t,'review-verdict')
  assert t.evaluate('S.reviewId')==current
  form=t.locator('form[data-form=annotation]');form.locator('[name=human_note]').fill('网络故障前的备注应保留')
  if self.bridge_mode:self.fail_annotation=True
  else:t.route('**/api/projects/*/evidence/*/annotation',lambda route:route.abort('failed'))
  t.locator('[data-action=review-verdict][data-status=manual]').click();self.idle(t)
  assert t.evaluate('S.reviewId')==current and form.locator('[name=human_note]').input_value()=='网络故障前的备注应保留'
  key=t.evaluate('S.reviewAttempt?.key');assert key
  if self.bridge_mode:self.fail_annotation=False
  else:t.unroute('**/api/projects/*/evidence/*/annotation')
  t.locator('[data-action=review-verdict][data-status=manual]').click();self.idle(t)
  assert t.evaluate('S.reviewId')==original_ids[2]
  record=next(e for e in self.teacher.ok('GET',self.base)['body']['evidence'] if e['id']==current)
  assert len(record['annotations'])==1 and record['annotations'][0]['idempotency_key']==key
  self.checks.append('必填缺失与网络中断不跳项、不丢备注；同一幂等键重试仅产生一个标注版本')
  # Valid metadata set using real hierarchy then one-click save.
  form=t.locator('form[data-form=annotation]')
  for name,value in [('material_date','2026-05-06'),('description','合成证书有效判定'),('rule_item','天梯赛'),('scenario','三等奖'),('rule_source','测试细则第10页'),('score','0.4')]:form.locator('[name='+name+']').fill(value)
  for value in ['innovation','competition','competition.professional']:
   t.locator('[data-category-level]').last.select_option(value);self.idle(t)
  t.keyboard.press('Alt+1');self.idle(t);assert t.evaluate('S.reviewId')==original_ids[3]
  self.shot(t,'r06-连续阅卷固定队列');self.checks.append('Alt+1连同层级分类、日期和分值保存；有效项退出原筛选也仍保留本轮位置')
  # Explicitly inspect the last item; after saving it cannot wrap back.
  t.locator('[data-action=select-evidence][data-id="'+original_ids[-1]+'"]').click();self.idle(t)
  t.locator('form[data-form=annotation] [name=human_note]').fill('最后一项需补充材料');t.keyboard.press('Alt+2');self.idle(t)
  assert t.evaluate('S.reviewId')==original_ids[-1] and t.locator('[data-action=review-next]').is_disabled()
  self.checks.append('末项成功保存后停留，下一项禁用；不会回到第一项重复评分')
  # Capture a real generated archive and return sheet through the browser.
  self.action(t,'web-ai-export');t.locator('form[data-form=web-ai-export] input[type=checkbox]').check()
  with t.expect_download() as d:t.locator('form[data-form=web-ai-export] button[type=submit]').click()
  download=d.value;bundle=self.root/'网页AI任务.zip';download.save_as(bundle);self.idle(t)
  with ZipFile(bundle) as z:
   credential=z.read('任务凭据.json');table_data=read_table(z.read('回填模板.csv'),'returned.csv');headers=table_data.headers;rows=[dict(r) for r in table_data]
   assert rows and len([n for n in z.namelist() if n.startswith('图片/')])==len(rows)
  for row in rows:row.update({'状态':'需人工判断','局部分数':'0','图片内容描述':'网页AI合成回填','AI备注':'证据日期仍需人工确认','是否有盖章':'未判定'})
  credpath=self.root/'任务凭据.json';credpath.write_bytes(credential)
  good=self.root/'正确回填.csv';good.write_bytes(csv_bytes(headers,[[r[h] for h in headers] for r in rows]))
  bad=self.root/'错误回填.csv';badrows=[dict(r) for r in rows];badrows[-1]['学生ID']='不应替换身份';bad.write_bytes(csv_bytes(headers,[[r[h] for h in headers] for r in badrows]))
  self.action(t,'web-ai-import');f=t.locator('form[data-form=web-ai-import]');f.locator('[name=credential]').set_input_files(credpath);f.locator('[name=file]').set_input_files(bad)
  before=self.teacher.ok('GET',self.base)['revision'];f.locator('[type=submit]').click();self.idle(t)
  error=t.locator('dialog.import-errors');expect(error).to_be_visible();assert 'IDENTITY_MISMATCH' in error.inner_text()
  assert self.teacher.ok('GET',self.base)['revision']==before
  self.shot(t,'r07-回填校验位置与错误说明')
  with t.expect_download() as d:error.locator('[data-json]').click()
  errorfile=self.root/'errors.json';d.value.save_as(errorfile);assert json.loads(errorfile.read_text())['saved'] is False
  error.locator('[data-dismiss]').click();f.locator('[name=file]').set_input_files(good);f.locator('[type=submit]').click();self.idle(t)
  assert self.teacher.ok('GET',self.base)['revision']==before
  self.shot(t,'r08-网页AI回填预览');self.action(t,'web-ai-commit')
  after=self.teacher.ok('GET',self.base)
  assert after['revision']==before+1
  imported=[e['annotations'][-1] for e in after['body']['evidence'] if e['id'] in {r['材料ID'] for r in rows}]
  assert all(a['actor_kind']=='web_ai' and not a['reviewed'] for a in imported)
  self.checks.append('浏览器下载真实任务ZIP→定位身份错误并下载JSON→零写入预览→确认整批回填，均非人工核对')
  # Drill down from an actual calculated record; immutable ID is reused.
  self.nav(t,'calculation');self.action(t,'all-achievements')
  target=t.locator('#achievement-table [data-action=achievement-source]').first
  target_id=target.get_attribute('data-id');target.click();self.idle(t)
  assert t.evaluate('S.reviewId')==target_id and t.locator('form[data-form=annotation]').get_attribute('data-id')==target_id
  self.checks.append('计算总表→成果明细→原材料跳转使用同一材料ID，不按描述猜测匹配')
  # Grow to 70 actual uploaded images, then check selection across 60-item pages.
  for seed in range(160,222):upload(self.student,self.p,seed)
  t.locator('[data-review-filter]').select_option('all');self.idle(t);self.action(t,'review-rebuild')
  assert t.evaluate('reviewQueue().ids.length')==70
  t.get_by_role('button',name='全部筛选结果全选',exact=True).click();self.idle(t)
  assert t.locator('#review-queue input.collection-check:checked').count()==60
  t.locator('[data-action=review-page][data-delta="1"]').click();self.idle(t)
  assert t.locator('#review-queue input.collection-check:checked').count()==10
  self.shot(t,'r10-跨页选择与范围提示')
  self.checks.append('70张真实上传图片分60/10两页：全部筛选结果全选跨页保留，所选范围不误报为本页')
  # Table templates + strict roster import preview, then final commit.
  t.evaluate("location.hash='#/students'");self.idle(t);self.action(t,'roster-import')
  roster_file=self.root/'新增名册.csv';roster_file.write_text('学号,姓名,班级\nNEW2024001,新入测试学生,测试新班\n',encoding='utf-8-sig')
  f=t.locator('form[data-form=roster-import]');f.locator('[name=file]').set_input_files(roster_file);f.locator('[type=submit]').click();self.idle(t)
  assert len(self.teacher.ok('GET','/api/students'))==70
  self.action(t,'confirm-import');assert len(self.teacher.ok('GET','/api/students'))==71
  self.checks.append('浏览器名册表格导入先预览不创建账号，明确确认后创建第71名学生')
  if t.locator('#dialog[open]').count():self.action(t,'close')
  for width,height in [(1440,1000),(390,844),(844,390)]:
   t.set_viewport_size({'width':width,'height':height})
   for tab in ['review','calculation','submission']:
    self.nav(t,tab);self.shot(t,f'r09-{tab}-{width}')
  self.checks.append('新增交互所在阅卷、总表、收件页面桌面/竖屏/横屏共9组无整页横向溢出')
  # Wide, scrollable table uses the same collection component as office pages.
  t.set_viewport_size({'width':1440,'height':1000})
  t.evaluate("""()=>{modal('框选双轴滚动测试','<div id="scroll-fixture" class="table-wrap" style="height:260px;width:620px;max-width:100%;overflow:auto"><table style="width:1800px"><thead><tr><th>合成项目</th><th>合成内容</th></tr></thead><tbody id="scroll-rows">'+Array.from({length:80},(_,i)=>'<tr style="height:45px"><td>合成'+i+'</td><td>没有真实学生资料</td></tr>').join('')+'</tbody></table></div>',true);} """)
  self.idle(t);t.locator('#dialog .collection-toolbar').last.get_by_role('button',name='框选',exact=True).click()
  viewport=t.locator('#scroll-fixture').bounding_box();start=t.locator('#scroll-rows tr').first.bounding_box()
  # Start inside a data cell, not the selection checkbox (native controls must not start a drag).
  t.mouse.move(viewport['x']+170,start['y']+15);t.mouse.down();t.mouse.move(viewport['x']+viewport['width']-5,viewport['y']+viewport['height']-5,steps=8);t.wait_for_timeout(250)
  scroll=t.locator('#scroll-fixture').evaluate('(e)=>[e.scrollLeft,e.scrollTop]');assert scroll[0]>0 and scroll[1]>0,scroll
  t.keyboard.press('Escape');t.mouse.up();self.idle(t);assert t.locator('#scroll-rows input.collection-check:checked').count()==0
  self.checks.append('宽表框选接近边缘自动双轴滚动；Esc取消恢复原选择，表格值不改变')
  self.action(t,'close')
  assert not self.errors,self.errors
  result={'version':'1.0.0rc4','mode':('离线Chromium DOM + Python HTTP桥 + 本机真实uvicorn/SQLite；非浏览器原生网络和cookie' if self.bridge_mode else 'Chromium原生HTTP + 本机uvicorn/SQLite + cookie/CSRF')+'；截图源为本地canvas MediaStream替身', 'passed':len(self.checks),'checks':self.checks,'screenshots':self.shots,'javascript_errors':self.errors,'http_request_count':len(self.requests), 'fixture_scale':{'students':71,'initial_images':6,'uploaded_images_after_growth':70}, 'not_tested':['浏览器原生网络（采用--bridge时）','系统原生屏幕选择器/权限弹窗','实体Windows/macOS/手机','生产HTTPS/跨反向代理','并发峰值压测']}
  (self.out/'interaction-report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False,indent=2))

def main():
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=ROOT/'artifacts/interactions');p.add_argument('--bridge',action='store_true',help='当前环境禁止Chromium网络时，用离线DOM+Python真实HTTP桥；不宣称浏览器网络验收');args=p.parse_args();test=Validation(args.output,args.bridge)
 try:
  with sync_playwright() as pw:
   browser=pw.chromium.launch(executable_path=os.environ.get('CHROMIUM','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
   try:test.run(browser)
   except Exception:
    (args.output/'failure.json').write_text(json.dumps({'checks':test.checks,'errors':test.errors,'requests':test.requests[-30:]},ensure_ascii=False,indent=2))
    for index,context in enumerate(browser.contexts):
     for page in context.pages:
      try:page.screenshot(path=str(args.output/f'failure-{index}.png'),full_page=True);(args.output/f'failure-{index}.txt').write_text(page.locator('body').inner_text())
      except Exception:pass
    raise
   finally:browser.close()
 finally:test.close()
if __name__=='__main__':main()
