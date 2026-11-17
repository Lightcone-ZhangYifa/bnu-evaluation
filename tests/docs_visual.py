"""Render delivered HTML docs offline and check headings, anchors, images and layout."""
from pathlib import Path
import argparse,json,os,re
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]

def main():
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=ROOT/'artifacts/docs');a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True);reports=[]
 with sync_playwright() as pw:
  browser=pw.chromium.launch(executable_path=os.environ.get('CHROMIUM','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
  try:
   for name in ['使用手册','图文操作教程','部署与维护说明','逐项核查与验收记录','外部AI接入与表格回填指南']:
    text=(ROOT/'docs'/(name+'.html')).read_text();assert '澄明' not in text and '北京师范大学学生综合测评管理系统' in text
    for width,height in [(1440,1050),(390,844)]:
     page=browser.new_page(viewport=dict(width=width,height=height));page.set_content(text,wait_until='load');page.evaluate("document.querySelectorAll('img').forEach(i=>i.loading='eager')");page.wait_for_function("Array.from(document.images).every(i=>i.complete && i.naturalWidth>0)");page.wait_for_timeout(100)
     assert page.locator('h1').count()==1
     missing=page.evaluate("Array.from(document.querySelectorAll('a[href^=\"#\"]')).filter(a=>!document.getElementById(a.getAttribute('href').slice(1))).map(a=>a.hash)")
     assert not missing,(name,missing)
     overflow=page.evaluate('document.documentElement.scrollWidth-innerWidth');assert overflow<=2,(name,width,overflow)
     path=a.output/f'{name}-{width}.png';page.screenshot(path=str(path),full_page=False)
     if name=='图文操作教程' and width==1440:
      for id in ['open','upload','annotation','academic','admin','export-buttons','read-workbook','templates-guide','pdf-guide','capture-guide','multi-select-guide','web-ai-guide','import-errors-guide']:
       page.locator('#'+id).scroll_into_view_if_needed();page.wait_for_timeout(100);page.screenshot(path=str(a.output/f'教程-{id}.png'),full_page=False)
     reports.append(dict(document=name,viewport=f'{width}x{height}',sections=page.locator('section').count(),embedded_images=page.locator('img[src^="data:"]').count(),missing_anchors=len(missing),page_overflow_px=overflow,broken_images=0))
     page.close()
  finally:browser.close()
 result={'checks':reports,'passed':len(reports),'failed':0,'mode':'离线HTML/Chromium排版验证；非打印机或所有浏览器兼容性验收'}
 (a.output/'docs-report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
