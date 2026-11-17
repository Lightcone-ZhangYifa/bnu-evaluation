#!/usr/bin/env python3
"""Validate a portable project without importing it into a running service."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.portable import verify_archive
p=argparse.ArgumentParser(description='离线验证 .zcp 结构和原件 SHA，不执行文件内的指令。')
p.add_argument('project',type=Path);a=p.parse_args()
try:
    if a.project.stat().st_size>512*1024*1024:raise ValueError('工程文件超过512MB保护限制。')
    archive,manifest,data=verify_archive(a.project.read_bytes())
    print(json.dumps({'format':manifest['format'],'format_version':manifest['format_version'],'test_only':data['body']['meta'].get('test_only',False),'students':len(data['roster']),'evidence':len(data['body']['evidence']),'public_versions':len(data['public_versions']),'snapshots':len(data['body']['snapshots']),'files':len(manifest['files']),'integrity':'passed','authenticity':'not established by hashes alone'},ensure_ascii=False,indent=2))
except Exception as exc:
    print('工程验证失败：'+str(exc),file=sys.stderr);sys.exit(1)
