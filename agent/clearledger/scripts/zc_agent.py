#!/usr/bin/env python3
"""A minimal, auditable REST harness. No AI, model dependency, or autonomous loop."""
from __future__ import annotations
import argparse,json,os,sys,urllib.request,urllib.error,urllib.parse
from pathlib import Path

class Client:
    def __init__(self):
        self.base=os.environ.get('ZC_BASE_URL','').rstrip('/')
        self.pid=os.environ.get('ZC_PROJECT_ID','')
        self.token=os.environ.get('ZC_TOKEN','')
        u=urllib.parse.urlsplit(self.base)
        if '/' in self.pid or len(self.pid)>100:raise ValueError('工程ID格式不正确。')
        if not self.token or not self.pid:raise ValueError('必须提供 ZC_TOKEN、ZC_PROJECT_ID。')
        if u.scheme!='https' and not (u.scheme=='http' and u.hostname in ('localhost','127.0.0.1','::1')):raise ValueError('仅允许 HTTPS，或本机回环 HTTP 验证。')
        if u.username or u.password or u.query or u.fragment:raise ValueError('ZC_BASE_URL 不是安全的站点地址。')
    def request(self,path,method='GET',payload=None,binary=False):
        req=urllib.request.Request(self.base+path,method=method,headers={'Authorization':'Bearer '+self.token,'Accept':'application/json' if not binary else '*/*'})
        if payload is not None:req.data=json.dumps(payload,ensure_ascii=False).encode();req.add_header('Content-Type','application/json')
        try:
            # Refuse redirects: a token must never follow a resource URL to another site.
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self,*args,**kwargs):return None
            with urllib.request.build_opener(NoRedirect).open(req,timeout=120) as response:
                data=response.read(50*1024*1024+1)
                if len(data)>50*1024*1024:raise ValueError('响应超过50 MB。')
                return data if binary else json.loads(data)
        except urllib.error.HTTPError as exc:
            message=exc.read(5000).decode('utf-8','replace')
            raise ValueError(f'HTTP {exc.code}: {message}') from None
    def context(self):return self.request('/api/agent/context/'+urllib.parse.quote(self.pid,safe=''))

def check_context(judged,current,eid,pid,key):
    """Refresh only the aggregate revision; never silently refresh the judged facts."""
    if judged.get('project_id')!=pid or current.get('project_id')!=pid:
        raise ValueError('判读上下文不属于当前授权工程。')
    for field in ('rules','supplementary_instructions','source_documents','meta'):
        if judged.get(field)!=current.get(field):
            raise ValueError('规则、说明、原始来源或参评范围已变化。请重新获取上下文并重新判读，不能沿用旧结论。')
    old=next((r for r in judged.get('queue',[]) if r['id']==eid),None)
    new=next((r for r in current.get('queue',[]) if r['id']==eid),None)
    if not old or not new or (old['student_id'],old['resource_sha'])!=(new['student_id'],new['resource_sha']):
        raise ValueError('材料身份/图片与判读上下文不匹配。')
    latest=new.get('latest')
    replay=bool(latest and latest.get('idempotency_key')==key)
    if not replay and latest and latest.get('reviewed'):
        raise ValueError('该项已有人工结论，不能覆盖。')
    if not replay and old['annotation_version']!=new['annotation_version']:
        raise ValueError('该项标注版本已变化，请重新获取并核对。')
    return old

def main():
    p=argparse.ArgumentParser(description='北京师范大学学生综合测评管理系统：受限外部标注 REST 工具')
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('manifest')
    context=sub.add_parser('context');context.add_argument('--output')
    for name in ['image','resource']:
        t=sub.add_parser(name);t.add_argument('id');t.add_argument('--output',required=True)
    a=sub.add_parser('annotate');a.add_argument('evidence_id');a.add_argument('file');a.add_argument('--key',required=True,help='同一次提交重试必须使用同一幂等键');a.add_argument('--context',required=True,help='实际判读前下载并使用的context JSON文件')
    a=sub.add_parser('public-draft');a.add_argument('file')
    args=p.parse_args();client=Client()
    if args.command=='manifest':result=client.request('/api/agent/manifest')
    elif args.command=='context':result=client.context()
    elif args.command in ('image','resource'):
        ident=urllib.parse.quote(args.id,safe='')
        path='/api/resources/'+ident if args.command=='resource' else '/api/projects/'+client.pid+'/evidence/'+ident+'/image?original=true'
        data=client.request(path,binary=True);out=Path(args.output)
        with out.open('xb') as f:f.write(data)
        os.chmod(out,0o600);print('已保存到 '+str(out));return
    elif args.command=='annotate':
        annotation=json.loads(Path(args.file).read_text(encoding='utf-8'))
        judged=json.loads(Path(args.context).read_text(encoding='utf-8'));ctx=client.context()
        item=check_context(judged,ctx,args.evidence_id,client.pid,args.key)
        result=client.request('/api/projects/'+client.pid+'/evidence/'+urllib.parse.quote(args.evidence_id,safe='')+'/annotation','PUT',{'revision':ctx['revision'],'expected_annotation_version':item['annotation_version'],'annotation':annotation,'idempotency_key':args.key})
    else:
        payload=json.loads(Path(args.file).read_text());payload['revision']=0;payload['body']['approved']=False
        result=client.request('/api/projects/'+client.pid+'/public-drafts','POST',payload)
    text=json.dumps(result,ensure_ascii=False,indent=2)
    if getattr(args,'output',None):
        with open(args.output,'x',encoding='utf-8') as f:f.write(text)
        os.chmod(args.output,0o600);print('已保存到 '+args.output)
    else:print(text)
if __name__=='__main__':
    try:main()
    except (ValueError,OSError,urllib.error.URLError) as exc:print('未完成：'+str(exc),file=sys.stderr);sys.exit(1)
