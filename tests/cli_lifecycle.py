"""Run a local-process deployment/backup/restore smoke test using installed dependencies.

This deliberately does not run pip, Docker, PostgreSQL, systemd, TLS or ClamAV.
All generated credentials and databases stay inside a temporary directory.
"""
from pathlib import Path
import argparse, json, os, shutil, socket, sys, tempfile
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import httpx
from io import BytesIO
from PIL import Image
from scripts import console


def prepare(target):
    target.mkdir()
    for name in ('app','scripts'):
        shutil.copytree(ROOT/name,target/name,ignore=shutil.ignore_patterns('__pycache__'))
    (target/'.venv/bin').mkdir(parents=True)
    # Use installed dependencies without pretending to have installed a new venv.
    launcher=target/'.venv/bin/python'
    launcher.write_text('#!/bin/sh\nexec '+__import__('shlex').quote(sys.executable)+' \"$@\"\n')
    launcher.chmod(0o700)


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=ROOT/'artifacts/cli-report.json');a=p.parse_args()
    if os.name!='posix':raise SystemExit('本生命周期验证脚本在 Linux 运行；Windows 需单独验收。')
    checks=[];roots=[]
    with tempfile.TemporaryDirectory(prefix='bnu-cli-validation-') as tmp:
        tmp=Path(tmp);first=tmp/'installation';second=tmp/'restored';prepare(first);roots.append(first)
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        env=console.configure('local',port=port,root=first)
        console.init_db(first);checks.append('独立目录生成配置并初始化唯一管理员')
        try:
            console.start(first);assert console.health(first)['status']=='ok';checks.append('真实本机应用进程启动与HTTP健康检查')
            with httpx.Client(base_url='http://127.0.0.1:'+str(port)) as client:
                r=client.post('/api/ops/login',json={'portal':'admin','username':'admin','password':env['BOOTSTRAP_ADMIN_PASSWORD']});assert r.status_code==200,r.text
                client.headers['X-CSRF-Token']=r.json()['csrf']
                r=client.post('/api/auth/password',json={'old_password':env['BOOTSTRAP_ADMIN_PASSWORD'],'new_password':'Lifecycle-Validation-2026-Password'});assert r.status_code==200,r.text
                client.headers['X-CSRF-Token']=r.json()['csrf']
                image=BytesIO();Image.new('RGB',(120,80),(240,240,240)).save(image,'PNG')
                r=client.post('/api/feedback',data={'text':'仅供完整备份恢复测试的合成反馈'},files={'files':('test.png',image.getvalue(),'image/png')});assert r.status_code==200,r.text
                feedback_id=r.json()['id'];checks.append('实际HTTP登录、改密码、写入反馈和原始图片')
            before=console.health(first)['instance_id']
            console.backup(tmp/'backup',first);assert console.health(first)['instance_id']!=before
            manifest=console.verify_backup(tmp/'backup/system-backup.zip');assert any(k.startswith('objects/') for k in manifest['files']);checks.append('暂停写入、备份数据库与原件、自动恢复原运行状态')
            console.app_command('verify',first);checks.append('运行中系统原件SHA256与审计签名校验')
            console.stop(first);checks.append('按进程身份安全停止，不删除数据')
            prepare(second);roots.append(second);old_confirm=console.confirm;console.confirm=lambda *args,**kw:True
            try:console.restore(tmp/'backup/system-backup.zip',second)
            finally:console.confirm=old_confirm
            assert console.load_env(second/'.env')['APP_SECRET']==env['APP_SECRET']
            console.app_command('verify',second);console.start(second)
            with httpx.Client(base_url='http://127.0.0.1:'+str(port)) as client:
                r=client.post('/api/ops/login',json={'portal':'admin','username':'admin','password':'Lifecycle-Validation-2026-Password'});assert r.status_code==200,r.text
                r=client.get('/api/ops/feedback');assert r.status_code==200 and r.json()[0]['id']==feedback_id
                assert len(r.json()[0]['attachments'])==1
            checks.append('恢复到空目录，原密码与密钥保留，反馈及原件引用一致')
            console.stop(second)
            try:console.restore(tmp/'backup/system-backup.zip',second);raise AssertionError('must refuse existing installation')
            except console.OperationError:checks.append('拒绝覆盖非空系统，保留现有数据')
        finally:
            for root in roots:
                if (root/'.runtime/process.json').exists():
                    try:console.stop(root)
                    except console.OperationError:pass
    a.output.parent.mkdir(parents=True,exist_ok=True)
    report={'version':'1.0.0rc4','checks':checks,'passed':len(checks),'failed':0,'environment':sys.version,'mode':'Linux本机独立进程+真实HTTP；使用已安装依赖','not_tested':['pip/离线依赖安装','Windows/macOS','Docker','PostgreSQL/systemd','HTTPS/ClamAV']}
    a.output.write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
