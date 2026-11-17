"""Read-only office reports from versioned domain results.

The scoring engine is the only source of numbers. This module walks its recorded
selection IDs (including ancestor max nodes); it never computes an alternative
score, allocates a cap to individuals, or mutates an annotation/snapshot.
"""
from __future__ import annotations
from collections import defaultdict
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
import re
from .tabular import SheetSpec, xlsx_bytes, col, visual_lines, split_text
from .rules import validate_tree, text
from .schemas import STATUS_LABELS
from .security import canonical

SYSTEM_NAME='北京师范大学学生综合测评管理系统'
XLSX_TYPE='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def stamp_label(value):
    return '是' if value is True else '否' if value is False else '未判定'


def one_line(value):
    return re.sub(r'\s+',' ',str(value or '')).strip()


def local_time(value):
    if not value:return ''
    try:
        date=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return date.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S') if date.tzinfo else str(value)
    except ValueError:return str(value)


def num(value):
    return Decimal(str(value)) if value not in (None,'') else None


def rule_label(node):
    limits=[]
    if node.get('cap') is not None:limits.append('上限'+text(Decimal(node['cap']))+'分')
    if node.get('floor') is not None:limits.append('下限'+text(Decimal(node['floor']))+'分')
    if node.get('aggregate')=='max':limits.append('取最大值')
    if Decimal(node.get('weight','1'))!=1:limits.append('系数'+node['weight'])
    return node['name']+('（'+'；'.join(limits)+'）' if limits else '')


def rule_index(nodes):
    by,children,order=validate_tree(nodes)
    paths={}
    for nid in order:
        parent=by[nid]['parent'];paths[nid]=([*paths[parent],by[nid]['name']] if parent else [by[nid]['name']])
    return by,children,order,paths


def descendants(nodes,target=None):
    by,children,order,_=rule_index(nodes)
    if target is None:return set(order)
    if target not in by:raise ValueError('所选分类不存在于该次导出对应的规则版本。')
    selected=set();stack=[target]
    while stack:
        nid=stack.pop();selected.add(nid);stack.extend(children[nid])
    return selected


def selection_info(result,nodes,input_id,category):
    """Trace a single contribution upward, without distributing cap reductions."""
    by={n['id']:n for n in nodes};traces={n['id']:n for n in result['nodes']}
    inputs={x['id'] for x in result.get('inputs',[])}
    if input_id not in inputs:return {'is_input':False,'discarded':False,'discard_at':[],'explanation':'未进入本次计算输入'}
    discarded=[];explain=[];candidate=input_id;nid=category
    while nid in by:
        t=traces[nid];v=next((x['value'] for x in t['inputs'] if x['id']==candidate),None)
        separate=v is not None and Decimal(v)<0 and t.get('negative_policy')=='separate'
        if t['aggregate']=='max' and candidate not in t['selected_ids'] and not separate:
            discarded.append(t['name']);explain.append(t['name']+'：取最大值时已舍弃'+('（并列最大值仅计一次）' if candidate in t.get('tied_ids',[]) else ''))
        if Decimal(t.get('cap_delta','0'))>0:
            explain.append(t['name']+'：封顶前'+t['weighted_before_cap']+'，上限'+str(t['cap'])+'，分类结果'+t['value']+'；封顶不分摊到单项')
        elif Decimal(t.get('cap_delta','0'))<0:
            explain.append(t['name']+'：按下限'+str(t.get('floor'))+'处理，分类结果'+t['value'])
        if separate:explain.append(t['name']+'：负分单独扣除，不参加正分取最大值／封顶')
        candidate=nid;nid=by[nid].get('parent')
    return {'is_input':True,'discarded':bool(discarded),'discard_at':discarded,
            'explanation':'；'.join(explain) or '参与分类聚合；以逐级小计为准'}


def result_records(project,student,result,nodes,college=''):
    """One row per personal evidence, public-list row or academic average input."""
    _,_,_,paths=rule_index(nodes);sources={e['id']:e for e in project.body.get('evidence',[])}
    records=[]
    def add(source,id,a,extras):
        selection=selection_info(result,nodes,id,a.get('category_id'))
        status=a.get('status','unlabeled')
        excluded=extras.get('excluded_reason')
        # Internal enum codes are not exposed as unexplained office terminology.
        excluded=STATUS_LABELS.get(excluded,excluded)
        reason=excluded or (STATUS_LABELS.get(status,status) if status!='valid' else selection['explanation'])
        record={
            'project_id':project.id,'project':project.title,'year':project.body['meta'].get('year',''),
            'college':college or project.body['meta'].get('college_name',''),'student_id':student['id'],
            'number':student['number'],'name':student['name'],'class_name':student['class_name'],
            'id':id,'source':source,'category_id':a.get('category_id') or '',
            'category_path':' / '.join(paths.get(a.get('category_id'),[])),
            'material_date':a.get('material_date') or '', 'description':a.get('description') or '尚未标注',
            'status':status,'rule_item':a.get('rule_item',''),'scenario':a.get('scenario',''),
            'rule_source':a.get('rule_source',''),'score':a.get('score','0'),
            'base_score':a.get('base_score'),'factors':a.get('factors',[]),'denominator':a.get('denominator','1'),
            'has_stamp':a.get('has_stamp'),'stamp_text':a.get('stamp_text') or '',
            'student_note':a.get('student_note') or '',
            'ai_note':a.get('ai_note') or '', 'human_note':a.get('human_note') or a.get('note') or '',
            'date_override_reason':a.get('date_override_reason') or '',
            'outcome_key':a.get('outcome_key') or '',
            'relations':'；'.join(k+'='+a[k] for k in ('support_for','duplicate_of','replaces') if a.get(k)),
            **extras,**selection,
            'calculation_status':'取最大值时已舍弃' if selection['discarded'] else '参与聚合（封顶不分摊）' if selection['is_input'] else '未独立计入',
            'calculation_note':reason or selection['explanation'],
        }
        records.append(record)
    for m in result.get('materials',[]):
        a=m['annotation'];e=sources.get(m['id'],{});version=None
        for v in e.get('annotations',[]):
            if v['version']==m.get('annotation_version'):version=v;break
        add('个人图片',m['id'],a,dict(resource_sha=m.get('resource_sha',''),
            submitted_at=m.get('submitted_at',''),submitted_by=m.get('submitted_by',e.get('submitted_by','')),
            reviewed=m.get('reviewed',False),annotation_version=m.get('annotation_version',0),
            last_modified_at=m.get('last_modified_at',(version or {}).get('created_at','')),
            last_modified_by=m.get('last_modified_by',(version or {}).get('actor_id','')),
            actor_kind=m.get('actor_kind',(version or {}).get('actor_kind','')),
            excluded_reason=m.get('excluded_reason'),source_name='个人提交图片'))
    for r in result.get('public_rows',[]):
        add('公共名单',f"public:{r['list_id']}:{r['id']}",{**r,'rule_item':r.get('list_name','')},dict(
            resource_sha=r.get('resource_sha',''),source_name=r.get('source_name') or r['list_name'],
            reviewed=True,annotation_version=r['list_revision'],submitted_at='',submitted_by='',
            last_modified_at='',last_modified_by='',actor_kind='teacher',list_id=r['list_id'],
            list_name=r['list_name'],list_revision=r['list_revision']))
    for item in result.get('inputs',[]):
        if item.get('source')=='academic':
            add('课程计算',item['id'],{**item,'status':'valid','rule_item':'专业加权平均成绩',
                'scenario':'课程学分绩之和 / 纳入计算的课程学分之和',
                'rule_source':'工程内已确认的课程成绩版本','material_date':project.body['meta'].get('assessment_start','')+'至'+project.body['meta'].get('assessment_end','')},
                dict(resource_sha='',source_name='课程成绩',reviewed=True,annotation_version='',submitted_at='',submitted_by='',last_modified_at='',last_modified_by='',actor_kind='teacher'))
    return records


def description_line(record):
    date=one_line(record.get('material_date')) or '日期待确认'
    desc=one_line(record.get('description')) or '尚未标注'
    score=text(Decimal(record.get('score') or '0'))
    if record.get('discarded'):tail=score+'，取最大值时已舍弃'
    elif not record.get('is_input'):tail=score+'，未独立计入：'+one_line(record.get('calculation_note') or '未进入计算')
    else:tail=score
    return f'{date} {desc}【{tail}】'


RECORD_COLUMNS=[
 ('学院','college',18),('工程','project',28),('学年','year',14),('班级','class_name',16),('学号','number',20),('姓名','name',12),
 ('来源类型','source',12),('材料／成果ID','id',26),('材料日期','material_date',18),('材料描述','description',44),
 ('完整分类路径','category_path',40),('分类代码','category_id',26),('具体加分项','rule_item',36),('具体加分情形','scenario',36),
 ('细则出处','rule_source',32),('材料状态','status',20),('局部认定分数','score',14),('基础分','base_score',12),
 ('局部系数','factors',16),('分母','denominator',10),('计算处理','calculation_status',26),('处理说明','calculation_note',48),
 ('是否有盖章','has_stamp',12),('盖章上的文字','stamp_text',36),('已人工核对','reviewed',12),('标注／名单版本','annotation_version',14),
 ('提交时间（北京时间）','submitted_at',23),('提交人账号ID','submitted_by',24),('最后修改时间（北京时间）','last_modified_at',23),('最后修改人账号ID','last_modified_by',24),
 ('最后标注身份','actor_kind',14),('AI备注（内部）','ai_note',40),('人工备注（内部）','human_note',40),('日期特殊认定依据','date_override_reason',32),
 ('向学生说明','student_note',40),('成果唯一键','outcome_key',24),('关联材料','relations',32),('原始资源SHA256','resource_sha',48),('原始来源文件','source_name',28),
 ('机械生成描述','formatted_description',48)]


def flat_sheet(name,columns,rows,freeze_cols=0):
    """columns = [(heading, key, width)]. All wide audit sheets print at 80%, not 5%."""
    return SheetSpec(name,[[c[0] for c in columns]]+rows,[c[2] for c in columns],freeze_cols=freeze_cols,
        repeat_cols=freeze_cols,paper_size=8 if len(columns)>8 else 9,fit_width=0 if len(columns)>12 else 1)


def records_sheet(records,name='所有成果',private=True):
    columns=[c for c in RECORD_COLUMNS if private or c[1] not in ('college','ai_note','human_note','submitted_by','last_modified_by','actor_kind')]
    rows=[]
    for record in records:
        row=[]
        for heading,key,width in columns:
            v=record.get(key,'')
            if key=='formatted_description':v=description_line(record)
            elif key=='status':v=STATUS_LABELS.get(v,v)
            elif key=='has_stamp':v=stamp_label(v)
            elif key in ('score','base_score','denominator'):v=num(v)
            elif key=='factors':v=' × '.join(v or [])
            elif key in ('submitted_at','last_modified_at'):v=local_time(v)
            elif key=='actor_kind':v={'teacher':'教师','student':'学委／复核员','agent':'外部Agent','web_ai':'网页AI表格回填'}.get(v,v)
            row.append(v)
        rows.append(row)
    return flat_sheet(name,columns,rows,freeze_cols=6 if private else 5)


def hierarchy_sheet(project,computed,roster,nodes,records_by_student,category_id=None):
    by,children,order,paths=rule_index(nodes);root=order[0]
    chosen=[category_id] if category_id else children[root] or [root]
    if any(n not in by for n in chosen):raise ValueError('分类不存在。')
    layout=[];stack=[(x,0) for x in reversed(chosen)]
    while stack:
        nid,depth=stack.pop();layout.append((nid,depth));stack.extend((x,depth+1) for x in reversed(children[nid]))
    maxdepth=max((d for _,d in layout),default=0);header_start=4;header_end=header_start+maxdepth+1
    span={};starts={};score_cols={};description_cols={};nextcol=6
    for nid,depth in layout:
        starts[nid]=nextcol;score_cols[nid]=nextcol;nextcol+=1
        if not children[nid]:description_cols[nid]=nextcol;nextcol+=1
    for nid,depth in reversed(layout):
        span[nid]=1+sum(span[x] for x in children[nid]) if children[nid] else 2
    issue_col=nextcol;ncols=issue_col
    if ncols>16384:raise ValueError('全部分类超出Excel的列数限制，请选择分类分别导出。')
    rows=[['']*ncols for _ in range(header_end)];styles={};merges=[];widths=[12,20,16,16,15]+[12]*(ncols-5)
    def setv(r,c,v,style=None):
        rows[r-1][c-1]=v
        if style is not None:styles[(r,c)]=style
    def merge(r1,c1,r2,c2):
        if r1!=r2 or c1!=c2:merges.append(f'{col(c1)}{r1}:{col(c2)}{r2}')
    title='综合测评成绩汇总表' if not category_id else '分类成绩汇总表'
    setv(1,1,title,3);merge(1,1,1,min(5,ncols))
    meta=project.body['meta']
    setv(2,1,f"工程：{project.title}　学年：{meta.get('year','')}　班级：{meta.get('class_name','')}　负责人：{meta.get('responsible','')}　规则版本：{computed['rule_version_id']}",4)
    merge(2,1,2,ncols)
    setv(3,1,'说明：分数来自系统计算记录。每级小计保留其独立计算值；上级取最大值／封顶不会覆盖子项。未完整时总分仅为当前可计算部分。'+('【合成／抽样测试工程，非正式成绩】' if meta.get('test_only') else '')+(('　分类范围：'+' / '.join(paths[category_id])) if category_id else ''),4)
    merge(3,1,3,ncols)
    for r in (1,2,3):
        for c in range(1,ncols+1):styles.setdefault((r,c),4)
    for c,h in enumerate(['姓名','学号','班级','完整状态',rule_label(by[root])+'\n当前可计算部分／总分'],1):
        setv(header_start,c,h,1);merge(header_start,c,header_end,c)
    for nid,depth in layout:
        r=header_start+depth;c=starts[nid]
        setv(r,c,rule_label(by[nid]),1)
        if children[nid]:
            merge(r,c,r,c+span[nid]-1);setv(r+1,c,by[nid]['name']+'小计',8);merge(r+1,c,header_end,c)
        else:
            merge(r,c,header_end-1,c+1);setv(header_end,c,'分数',1);setv(header_end,c+1,'具体描述',1)
            widths[c]=42
    setv(header_start,issue_col,'待处理事项／分类未确定材料',1);merge(header_start,issue_col,header_end,issue_col);widths[issue_col-1]=44
    for student in roster:
        result=computed['students'][student['id']];values={x['id']:x for x in result['nodes']}
        record_groups=defaultdict(list)
        for record in records_by_student[student['id']]:record_groups[record['category_id']].append(record)
        row=['']*ncols;row[:5]=[student['name'],student['number'],student['class_name'],'完整' if result['ready'] else '未完整／待认定',num(result['total'])]
        for nid in score_cols:
            row[score_cols[nid]-1]='待认定' if values[nid].get('missing_required') else num(values[nid]['value'])
        for nid,column in description_cols.items():
            entries=record_groups[nid]
            row[column-1]='\n'.join(description_line(x) for x in entries) or ('缺少必需认定材料【未计分】' if values[nid].get('missing_required') else '无【0】')
        issues=[x['message'] for x in result.get('issues',[])]+['缺失：'+x for x in result.get('missing_categories',[])]
        issues += [description_line(r) for r in records_by_student[student['id']] if r['category_id'] not in by]
        row[-1]='\n'.join(issues) or '无'
        chunks={c:split_text(row[c-1],widths[c-1],16) for c in [*description_cols.values(),issue_col]}
        count=max(len(v) for v in chunks.values());start=len(rows)+1
        for part in range(count):
            out=row[:] if part==0 else ['']*ncols
            for c,pieces in chunks.items():out[c-1]=pieces[part] if part<len(pieces) else ''
            rows.append(out);rn=len(rows)
            for c in (1,2,3,4):styles[(rn,c)]=6
            styles[(rn,5)]=5
            for nid,c in score_cols.items():styles[(rn,c)]=5 if children[nid] else 2
        if count>1:
            for c in range(1,ncols+1):
                if c not in chunks:merge(start,c,len(rows),c)
    return SheetSpec('测评结果' if category_id is None else '分类汇总',rows,widths,header_rows=header_end,freeze_rows=header_end,freeze_cols=5,
        merges=merges,styles=styles,row_heights={1:30,2:28,3:32,**{r:32 for r in range(header_start,header_end+1)}},
        filter_row=None,repeat_rows=header_end,repeat_cols=4,paper_size=8,fit_width=0)


def rule_sheet(nodes):
    by,children,order,paths=rule_index(nodes)
    columns=[('完整分类路径','',48),('分类代码','',28),('父分类代码','',24),('层级','',8),('是否叶子分类','',14),('聚合方式','',14),('上限','',12),('下限','',12),('系数','',12),('负分处理','',20),('是否必需','',12),('规则出处','',44)]
    rows=[[ ' / '.join(paths[n]),n,by[n]['parent'],len(paths[n])-1,not children[n],
        '取最大值' if by[n].get('aggregate')=='max' else '累加',num(by[n].get('cap')),num(by[n].get('floor')),num(by[n].get('weight','1')),
        '负分单独扣除' if by[n].get('negative_policy')=='separate' else '含负数一起聚合',by[n].get('required',False),by[n].get('source','')] for n in order]
    return flat_sheet('规则分类',columns,rows,2)


def trace_sheet(roster,computed,nodes):
    *_,paths=rule_index(nodes)
    headers=['学号','姓名','班级','完整分类路径','分类代码','聚合方式','未选择前输入合计','选中值（系数前）','系数','封顶／下限处理前','上限','下限','封顶削减（负数为下限补足）','单独扣分','分类小计','全部输入ID与精确值','选中输入ID','并列最大值ID','必需输入缺失']
    rows=[]
    for s in roster:
        for n in computed['students'][s['id']]['nodes']:
            rows.append([s['number'],s['name'],s['class_name'],' / '.join(paths[n['id']]),n['id'],
                '取最大值' if n['aggregate']=='max' else '累加',*[num(n.get(k)) for k in ('raw_sum','selected_before_weight','weight','weighted_before_cap','cap','floor','cap_delta','separate_deductions','value')],
                '\n'.join(x['id']+' = '+x['value'] for x in n['inputs']), '\n'.join(n['selected_ids']), '\n'.join(n.get('tied_ids',[])),n.get('missing_required',False)])
    # A high-volume leaf can exceed Excel's text capacity. Split only the input
    # listing into continuation rows; numeric columns remain on the first row.
    expanded=[]
    for row in rows:
        parts={c:split_text(row[c],48,60) for c in (15,16,17)};count=max(map(len,parts.values()))
        for i in range(count):
            r=row[:] if i==0 else row[:6]+['']*(len(row)-6)
            for c,chunks in parts.items():r[c]=chunks[i] if i<len(chunks) else ''
            expanded.append(r)
    return flat_sheet('计算轨迹',[(h,'',48 if i in (3,15,16,17) else 24 if i in (0,4) else 14) for i,h in enumerate(headers)],expanded,3)


def numeric_sheet(roster,computed,nodes,category_id=None):
    _,_,order,paths=rule_index(nodes);chosen=descendants(nodes,category_id);by={n['id']:n for n in nodes}
    order=[n for n in order if n in chosen]
    columns=[('学号','',20),('姓名','',12),('班级','',16),('完整状态','',16)]+[(' / '.join(paths[n])+'\n'+rule_label(by[n]),'',20) for n in order]
    rows=[]
    for s in roster:
        r=computed['students'][s['id']];vals={n['id']:n for n in r['nodes']}
        rows.append([s['number'],s['name'],s['class_name'],'完整' if r['ready'] else '未完整／待认定']+[None if vals[n].get('missing_required') else num(vals[n]['value']) for n in order])
    return flat_sheet('分类数值（可筛选）',columns,rows,3)


def academic_sheet(project,roster,result=None):
    """Plain first-row headings intentionally match the course import template."""
    cols=[('学号','',20),('姓名','',12),('班级','',16),('课程ID','',22),('课程名称','',30),('学分','',10),('成绩制','',12),('成绩','',14),('免计入','',12),('免计入依据','',32),('替代课程','',28),('替代依据','',32)]
    lookup={s['id']:s for s in roster};rows=[]
    versions=project.body.get('academic_versions',[])
    if not versions:return flat_sheet('课程成绩',cols,rows,3)
    v=versions[-1];courses={x['id']:x for x in v['courses']}
    for st in v['students']:
        if st['student_id'] not in lookup:continue
        s=lookup[st['student_id']];entry={x['course_id']:x for x in st['entries']}
        for c in v['courses']:
            g=entry.get(c['id'],{})
            rows.append([s['number'],s['name'],s['class_name'],c['id'],c['name'],num(c['credits']),c['scheme'],g.get('score'),
                '是' if g.get('excluded') else '否',g.get('exclusion_reason',''),g.get('substitute_name',''),g.get('substitution_reason','')])
    return flat_sheet('课程成绩',cols,rows,3)


def report_notes(project,computed,snapshot=None):
    return ('导出说明',['字段','说明'],[
        ['系统',SYSTEM_NAME],['工程',project.title],['工程ID',project.id],['工程修订',project.revision],
        ['测试工程',bool(project.body['meta'].get('test_only'))],['导出范围',snapshot['label'] if snapshot else '当前计算预览（不等同于正式公示）'],
        ['规则版本',computed['rule_version_id']],['计算输入指纹',computed['input_hash']],['是否完整',computed['ready']],
        ['总表结构','包括全部层级分类。每个非叶子分类有小计，每个叶子分类有分数和机械生成描述。缺少必需认定显示待认定，不伪造0分。'],
        ['取最大值','按实际计算记录的选中ID逐级向根检查。未选中的项目保留原局部分，标明“取最大值时已舍弃”；并列最大值仅计一次。'],
        ['封顶','子项及各级小计显示该级独立计算结果。不得将上级封顶削减任意分摊到个人成果。详见计算轨迹。'],
        ['日期','证据未标注日期时写“日期待确认”。课程计算的日期为工程参评期，不当作证书颁发日。'],
        ['描述','格式为“日期 描述【分数】”。各项之间使用单元格内换行，描述自身的连续空白折为一个空格；所有成果表保存原描述。'],
        ['长文本','总表自动按学生续行，不截断长描述。长备注仍完整保存在单元格，可在公式栏查看；无法显示的非法XML字符以替代符表示。'],
        ['数值','XLSX为确定性引擎结果的只读副本，不内置第二套求分公式。分数写为数值单元格，学号和ID写为文本；精确输入与审计数据以工程文件为准。'],
        ['盖章','是、否、未判定相互区分。仅记录可见印章情况与文字，不能据此证明印章真实或替代有效性认定。旧版本缺字段按未判定显示，不改写历史记录。'],
        ['筛选与打印','多级总表适合查阅；排序筛选请使用“分类数值（可筛选）”或“所有成果”。横向A3设置，超宽表保持可读比例跨页打印；可按分类另行导出缩小范围。'],
        ['权限与用途','仅供授权人员核对、存档，不得发送到班级公共群。导出文件不能反向覆盖系统评分记录。']])


def report_context(db,domain,project,computed=None,snapshot=None,student_id=None):
    if snapshot:
        computed={'students':deepcopy(snapshot['results']),'rule_version_id':snapshot['rule_version_id'],
            'input_hash':snapshot['input_hash'],'ready':all(r['ready'] for r in snapshot['results'].values())}
        roster=deepcopy(snapshot['roster'])
    else:
        computed=computed or domain.compute(db,project);roster=domain.roster(db,project.body['student_ids'])
    if student_id is not None:
        roster=[s for s in roster if s['id']==student_id]
        if not roster:raise ValueError('学生不在该次导出范围内。')
        computed={**computed,'students':{student_id:computed['students'][student_id]},'ready':computed['students'][student_id]['ready']}
    version=next((v for v in project.body['rule_versions'] if v['id']==computed['rule_version_id']),None)
    if not version:raise ValueError('导出对应的历史规则版本缺失。')
    from .db import College
    college=db.get(College,project.college_id)
    records={s['id']:result_records(project,s,computed['students'][s['id']],version['nodes'],college.name if college else '') for s in roster}
    return computed,roster,version['nodes'],records


def result_workbook(db,domain,project,computed=None,category_id=None,snapshot=None,student_id=None,private=True):
    computed,roster,nodes,records=report_context(db,domain,project,computed,snapshot,student_id)
    chosen=descendants(nodes,category_id);allrecords=[r for rs in records.values() for r in rs if r['category_id'] in chosen or category_id is None]
    sheets=[hierarchy_sheet(project,computed,roster,nodes,records,category_id),numeric_sheet(roster,computed,nodes,category_id),
        records_sheet(allrecords,private=private),trace_sheet(roster,computed,nodes),rule_sheet(nodes)]
    # A publication export must never use later, modified course entries.
    if snapshot is None:sheets.append(academic_sheet(project,roster))
    else:
        rows=[]
        for s in roster:
            academic=computed['students'][s['id']].get('academic')
            if academic:
                for c in academic['courses']:
                    rows.append([s['number'],s['name'],c.get('course'),c.get('included'),num(c.get('score')),num(c.get('credits')),num(c.get('weighted')),c.get('reason'),c.get('substitute_name')])
        sheets.append(('快照课程计算',['学号','姓名','课程','纳入计算','有效分数','学分','学分绩','处理依据','替代课程'],rows))
    sheets.append(report_notes(project,computed,snapshot))
    return xlsx_bytes(sheets)
