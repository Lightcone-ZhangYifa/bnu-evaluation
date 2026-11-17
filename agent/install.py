#!/usr/bin/env python3
"""Copy the self-contained skill into an external agent's configured skill root."""
import argparse,shutil
from pathlib import Path
p=argparse.ArgumentParser(description='安装外部Agent技能；不接收或保存令牌。')
p.add_argument('--skills-dir',required=True,help='所用Agent实际配置的技能根目录');a=p.parse_args()
source=Path(__file__).resolve().parent/'clearledger';target=Path(a.skills_dir).expanduser()/'clearledger'
if target.exists():p.error('目标已存在，请先审阅旧版本并手工移走，避免静默覆盖自定义技能。')
target.parent.mkdir(parents=True,exist_ok=True);shutil.copytree(source,target)
print('已安装：'+str(target)+'\n请为外部Agent进程设置 ZC_BASE_URL、ZC_PROJECT_ID、ZC_TOKEN，并读取 SKILL.md。')
