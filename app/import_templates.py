"""Blank, round-trip-compatible office import templates (no sample data in input)."""
from .tabular import SheetSpec,xlsx_bytes,csv_bytes,col
from .reporting import rule_sheet

TEMPLATES={
    'students': {
        'name':'学生名册导入模板',
        'headers':['学号','姓名','班级'],
        'example':['DEMO202400001','演示学生甲','演示班'],
        'required':'学号、姓名、班级均必填。学号是身份键，应按文本录入并保留前导0。一次最多2000名学生，可分批导入。',
    },
    'public': {
        'name':'名单类奖项导入模板',
        'headers':['学号','姓名','分类ID','分数','描述','情形','规则出处','日期','备注','状态','成果键','基础分','系数','分母','是否有盖章','盖章上的文字'],
        'example':['DEMO202400001','演示学生甲','positions','1.6','班长任职','评价权重80%，全年任职','附件四岗位任职','2026-06-30','','有效','position-2026-01','2','0.8','1','是','演示学院（仅为样例文字）'],
        'required':'必需表头：学号、分类ID、分数、描述、情形、规则出处、状态。不存在页面默认值补齐。有效行必须填写实际分类、描述、情形和出处；分数不明时填0与需人工判断，并说明缺失事实。',
    },
    'academic': {
        'name':'课程成绩导入模板',
        'headers':['学号','课程ID','课程名称','学分','成绩制','成绩','免计入','免计入依据','替代课程','替代依据'],
        'example':['DEMO202400001','CS001','数据结构','3','numeric','86','否','','',''],
        'required':'每行是某学生的一门课程。必需表头为学号、课程ID、课程名称、学分、成绩制、成绩。成绩可空，表示尚未取得该门成绩；不要删除其应参评课程行。',
    },
}


def template_bytes(kind,nodes=None,roster=None,format='xlsx'):
    if kind not in TEMPLATES:raise ValueError('不存在此导入模板。')
    t=TEMPLATES[kind];headers=t['headers']
    if format=='csv':return csv_bytes(headers,[]),t['name']+'.csv','text/csv; charset=utf-8'
    if format!='xlsx':raise ValueError('模板格式仅支持xlsx或csv。')
    widths=[20 if h in ('学号','课程ID','分类ID','成果键','日期') else 36 if h in ('描述','情形','规则出处','备注','盖章上的文字','免计入依据','替代依据') else 26 if h=='课程名称' else 14 for h in headers]
    entry=SheetSpec('填写数据',[headers]+[['']*len(headers) for _ in range(20)],widths,freeze_cols=1,repeat_cols=1,fit_width=0 if len(headers)>10 else 1)
    for j in range(1,len(headers)+1):
        for i in range(2,22):entry.styles[(i,j)]=9
    for header,options in [('状态',['有效','需人工判断','不属于综测规定的加分情形','不在加分时间']),('是否有盖章',['是','否','未判定']),('成绩制',['numeric','five','two']),('免计入',['是','否'])]:
        if header in headers:
            c=col(headers.index(header)+1);entry.validations.append((f'{c}2:{c}20001',options))
    notes=[['用途',t['name']],['开始填写','请在第一张“填写数据”工作表的第二行起录入；表内没有任何预填学生或分数。示例仅在第三张表，不能代替实际资料。'],
        ['必需信息',t['required']],['保留表头','不要修改第一行表头，不要新增标题行、合并单元格、公式或插图。导入程序仅读取第一张工作表。'],
        ['学号与代码','学号、课程ID、分类ID和成果键均按文本录入。不使用科学计数法，不自行按姓名匹配。'],
        ['日期','使用文本YYYY、YYYY-MM或YYYY-MM-DD。不要输入Excel日期序列号。未给日期时保留空白，不擅自填写提交时间。'],
        ['状态','名单类奖项可填中文状态，系统转换为相应枚举代码。无法唯一确定分数时填写0与需人工判断，并在备注中说明。'],
        ['系数','名单类奖项基础分可空。填写基础分时，系数用逗号分隔，分母默认为1；系统校验基础分×各系数÷分母与局部分数一致。不要在单元格内写公式。'],
        ['盖章','名单类奖项行的盖章列留空或写未判定，表示未作单独判断，沿用来源记录；明确的是/否覆盖来源。来源本身也可标记未判定。盖章文字只能在确有盖章时填写，不推测模糊文字。'],
        ['课程','numeric为百分制；five为优秀/良好/中等/合格/不合格；two为两级制，不纳入加权平均。免计入及替代课程必须写明依据。'],
        ['导入前核对','名单类奖项导入追加到编辑草稿；课程导入替换当前编辑草稿。必须预先核对后保存新版本，导入不会修改历史版本。'],
        ['模板参考','从工程页下载名单类奖项模板时附带当前工程的分类及参评学生参考；参考表不属于导入数据。']]
    sheets=[entry,('填写说明',['项目','说明'],notes),('填写示例（勿作为实际数据导入）',headers,[t['example']])]
    if nodes and kind=='public':sheets.append(rule_sheet(nodes))
    if roster and kind in ('public','academic'):
        sheets.append(('参评学生参考',['学号','姓名','班级'],[[s['number'],s['name'],s['class_name']] for s in roster]))
    return xlsx_bytes(sheets),t['name']+'.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
