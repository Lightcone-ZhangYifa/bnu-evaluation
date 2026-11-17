"""rc3 reports: exact trace selection, lossless text, formats, templates and scopes."""
from copy import deepcopy
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from datetime import timedelta
from pathlib import Path
import json, zipfile, xml.etree.ElementTree as ET, uuid
import pytest
from pydantic import ValidationError
from conftest import API, project, upload, annotate
from app.schemas import Annotation, PublicRow, PublicBody
from app.rules import calculate
from app.reporting import selection_info, description_line, hierarchy_sheet, records_sheet, result_records, trace_sheet, rule_sheet
from app.tabular import xlsx_bytes, SheetSpec, parse_table, split_text
from app.import_templates import template_bytes

NS={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}

def workbook(data):
    with zipfile.ZipFile(BytesIO(data)) as z:
        for n in z.namelist():
            if n.endswith('.xml') or n.endswith('.rels'): ET.fromstring(z.read(n))
        root=ET.fromstring(z.read('xl/workbook.xml'))
        names=[n.attrib['name'] for n in root.findall('s:sheets/s:sheet',NS)]
        return {name:ET.fromstring(z.read(f'xl/worksheets/sheet{i}.xml')) for i,name in enumerate(names,1)}

def values(sheet):
    return {c.attrib['r']:(''.join(c.itertext()) if c.attrib.get('t')=='inlineStr' else c.findtext('s:v',namespaces=NS)) for c in sheet.findall('.//s:sheetData/s:row/s:c',NS)}

def all_text(data):
    return '\n'.join(str(x) for sheet in workbook(data).values() for x in values(sheet).values())

def node(id,parent=None,aggregate='sum',**kw):
    return dict(id=id,name=id,parent=parent,aggregate=aggregate,weight='1',cap=None,floor=None,negative_policy='signed',**kw)

def item(id,category,score):return dict(id=id,category_id=category,score=score)

@pytest.mark.parametrize('field,value',[('has_stamp','true'),('has_stamp',1),('has_stamp','是'),('stamp_text','伪造默认值')])
def test_stamp_rejects_implicit_coercion_and_ungrounded_text(field,value):
    with pytest.raises(ValidationError):Annotation(status='unlabeled',**{field:value})

@pytest.mark.parametrize('has',[True,False,None])
def test_stamp_tristate_and_legacy_default(has):
    a=Annotation(status='unlabeled',has_stamp=has,stamp_text='演示印章' if has else None)
    assert a.has_stamp is has
    assert Annotation(status='unlabeled').has_stamp is None


def test_ancestor_max_ties_and_separate_deductions():
    nodes=[node('root'),node('positions','root','max'),node('school','positions','max'),node('class','positions','max')]
    nodes[2]['negative_policy']='separate'
    inputs=[item('a','school','0.25'),item('b','school','0.25'),item('minus','school','-0.1'),item('c','class','2')]
    result=calculate(nodes,inputs)
    assert result['total']=='2'
    for id in ('a','b','minus'):
        info=selection_info(result,nodes,id,'school')
        assert info['discarded'] and 'positions' in info['discard_at']
    assert 'school' in selection_info(result,nodes,'b','school')['discard_at']
    assert 'school' not in selection_info(result,nodes,'minus','school')['discard_at']
    assert not selection_info(result,nodes,'c','class')['discarded']
    line=description_line(dict(material_date='2026-06-30',description='工作人员',score='0.25',**selection_info(result,nodes,'a','school')))
    assert line=='2026-06-30 工作人员【0.25，取最大值时已舍弃】'


def test_cap_not_allocated_and_negative_separate_explained():
    nodes=[node('root'),node('sport','root')];nodes[1].update(cap='2',negative_policy='separate')
    r=calculate(nodes,[item('a','sport','1.2'),item('b','sport','1.2'),item('neg','sport','-0.5')])
    assert r['total']=='1.5'
    info=selection_info(r,nodes,'a','sport');assert not info['discarded']
    assert '封顶前2.4' in info['explanation'] and '不分摊' in info['explanation']
    assert description_line(dict(material_date=None,description='比赛\n参赛',score='1.2',**info))=='日期待确认 比赛 参赛【1.2】'


def test_multilevel_header_covers_each_node_with_numeric_subtotals():
    nodes=[node('root')]+[node(f'n{i}',f'n{i-1}' if i else 'root') for i in range(20)]
    nodes+=[node('sibling','root')]
    r=calculate(nodes,[item('x','n19','0.25')]);r.update(ready=True,issues=[])
    p=SimpleNamespace(id='p',title='多级表验证',revision=1,body={'meta':{'class_name':'演示班','year':'2025-2026'}})
    roster=[dict(id='s',name='演示甲',number='00000001',class_name='演示班')]
    record=dict(category_id='n19',description='测试成果',material_date='2026-04-26',score='0.25',is_input=True)
    spec=hierarchy_sheet(p,dict(rule_version_id='v',students={'s':r}),roster,nodes,{'s':[record]})
    sheet=workbook(xlsx_bytes([spec]))['测评结果'];data=values(sheet)
    for n in nodes[1:]:assert any(str(v)==n['name'] for v in data.values()),n['id']
    assert spec.header_rows==24
    # Numeric types, no fallback to numeric-looking text.
    numeric=[c for c in sheet.findall('.//s:c',NS) if c.attrib.get('t')=='n']
    assert len(numeric)>=22
    assert any(c.text=='2026-04-26 测试成果【0.25】' for c in sheet.findall('.//s:t',NS))
    # A merged range must not overlap any other merged range.
    def bounds(ref):
        import re
        def cell(v):
            a,b=re.match(r'([A-Z]+)(\d+)',v).groups();c=0
            for ch in a:c=c*26+ord(ch)-64
            return c,int(b)
        a,b=ref.split(':');return (*cell(a),*cell(b))
    ranges=[bounds(x.attrib['ref']) for x in sheet.findall('s:mergeCells/s:mergeCell',NS)]
    for i,(x1,y1,x2,y2) in enumerate(ranges):
        for a1,b1,a2,b2 in ranges[i+1:]:assert x2<a1 or a2<x1 or y2<b1 or b2<y1


def test_long_description_is_losslessly_split_and_not_truncated():
    text=('2026-06-30 很长的逐项说明【0.25】\n'*1400)+'终止标记'
    chunks=split_text(text,42,16)
    assert len(chunks)>1 and ''.join(chunks)==text
    with pytest.raises(ValueError):xlsx_bytes([('超限',['文字'],[['a'*32768]])])
    spec=SheetSpec('正确续行',[['文字']]+[[c] for c in chunks],[42])
    body=''.join(v for k,v in values(workbook(xlsx_bytes([spec]))['正确续行']).items() if k!='A1')
    assert body==text


def test_formula_injection_leading_zero_and_decimal_cells():
    out=xlsx_bytes([('导出',['学号','内容','分数'],[['00000123456789012345','=HYPERLINK("https://invalid")',Decimal('0.25')],['00002','+1+1',Decimal('-0.5')]])])
    s=workbook(out)['导出'];assert not s.findall('.//s:f',NS)
    cells={c.attrib['r']:c for c in s.findall('.//s:c',NS)}
    assert cells['A2'].attrib['t']=='inlineStr' and values(s)['A2']=='00000123456789012345'
    assert cells['C2'].attrib['t']=='n' and values(s)['C2']=='0.25'

@pytest.mark.parametrize('kind',['students','public','academic'])
def test_templates_blank_primary_sheet_and_readable_examples(kind):
    data,name,mime=template_bytes(kind)
    assert parse_table(data,name)==[]
    wb=workbook(data);assert list(wb)[0]=='填写数据' and len(wb)==3
    assert 'DEMO202400001' in all_text(data)
    assert 'DEMO202400001' not in str(values(wb['填写数据']))
    csv,name,mime=template_bytes(kind,format='csv');assert parse_table(csv,name)==[]


def test_all_export_routes_and_permissions(actors,clock):
    p=project(actors,clock);t=actors['teacher'];s=actors['students'][0];a=actors['admin'];base='/api/projects/'+p['id']
    e=upload(s,p,80);annotate(t,p,e,has_stamp=True,stamp_text='合成测试学院',human_note='内部测试备注')
    for k in ['students','public','academic']:
        r=t.request('GET','/api/export-templates/'+k+'?pid='+p['id']);assert r.status_code==200;workbook(r.content)
    paths=['/api/students/export']+['/api/export-tables/'+k for k in ['workspace','achievements','public-lists']]
    paths += [base+'/export/table/'+k for k in ['achievements','rules','students','submissions','public','academic','appeals','publications','audit']]
    paths += [base+'/export/xlsx',base+'/export/student/'+s.me['student_id']+'/xlsx']
    before=t.ok('GET',base)['body']
    for path in paths:
        r=t.request('GET',path);assert r.status_code==200,(path,r.text[:200]);workbook(r.content)
        assert s.request('GET',path).status_code in (403,404)
        assert a.request('GET',path).status_code in (403,404)
    after=t.ok('GET',base)['body'];assert after==before
    for kind in ['accounts','feedback','audit']:
        r=a.request('GET','/api/ops/export-table/'+kind);assert r.status_code==200,r.text[:200]
        assert 'password_hash' not in all_text(r.content)
        assert t.request('GET','/api/ops/export-table/'+kind).status_code in (403,404)
    full=t.request('GET',base+'/export/xlsx');assert '合成测试学院' in all_text(full.content)
    assert '内部测试备注' in all_text(full.content)


def test_export_scope_unassigned_teacher_and_agent(actors,clock,app):
    p=project(actors,clock);t=actors['teacher'];admin=actors['admin'];base='/api/projects/'+p['id']
    record=admin.ok('POST','/api/ops/teachers',dict(college_id=actors['college']['id'],username='unassigned',name='未授权教师'))
    other=API(app,'teacher','unassigned',record['initial_password'])
    assert other.request('GET',base+'/export/table/achievements').status_code==403
    assert p['title'] not in all_text(other.request('GET','/api/export-tables/achievements').content)
    token=t.ok('POST','/api/agent-tokens',dict(project_id=p['id'],name='测试Agent',hours=1))
    from fastapi.testclient import TestClient
    agent=TestClient(app);agent.headers['Authorization']='Bearer '+token['token']
    for path in [base+'/export/xlsx',base+'/export/table/achievements','/api/export-tables/achievements','/api/export-templates/public']:
        assert agent.get(path).status_code==403


def test_public_stamp_inherits_but_explicit_false_overrides(actors,clock):
    t=actors['teacher'];p=project(actors,clock);base='/api/projects/'+p['id']
    source=t.client.post('/api/uploads',files={'file':('test.pdf',b'%PDF-1.4\ntest\n%%EOF','application/pdf')}).json()
    rows=[]
    for i,s in enumerate(actors['roster'][:2]):
        rows.append(dict(id=str(uuid.uuid4()),student_id=s['id'],category_id='positions',score='0.25',description='工作人员',scenario='全年任职',rule_source='合成规则',material_date='2026-06-30',has_stamp=False if i else None))
    body=dict(resource_sha=source['sha256'],source_name='test.pdf',description='合成公共材料',rows=rows,approved=True,has_stamp=True,stamp_text='合成测试学院')
    l=t.ok('POST','/api/public-lists',dict(revision=0,name='盖章名单',body=body))
    p=t.ok('POST',base+'/public-bindings',dict(revision=p['revision'],list_id=l['id'],list_revision=l['revision']))
    calc=t.ok('GET',base+'/calculation')
    assert calc['students'][rows[0]['student_id']]['public_rows'][0]['has_stamp'] is True
    assert calc['students'][rows[1]['student_id']]['public_rows'][0]['has_stamp'] is False
    out=t.request('GET','/api/public-lists/'+l['id']+'/export');assert out.status_code==200
    parsed=parse_table(out.content,'x.xlsx');assert parsed[0]['是否有盖章']=='是' and parsed[1]['是否有盖章']=='否'
    assert parsed[1]['盖章上的文字']==''
    assert parsed[0]['分数']=='0.25'


def test_published_personal_table_and_historical_export_are_pinned(actors,clock):
    t=actors['teacher'];s=actors['students'][0];p=project(actors,clock);base='/api/projects/'+p['id']
    e=upload(s,p,85);annotate(t,p,e,has_stamp=True,stamp_text='旧版合成盖章',human_note='SECRET_HUMAN',ai_note='SECRET_AI')
    clock[0]+=timedelta(hours=2);p=t.ok('GET',base);p=t.ok('POST',base+'/publish',dict(revision=p['revision'],mode='provisional',label='第一次测试公示'))
    snap=p['body']['publication_id']
    annotate(t,p,e,score='0.8',has_stamp=False,stamp_text=None,description='新版内容')
    old=t.request('GET',base+'/export/xlsx?snapshot_id='+snap);new=t.request('GET',base+'/export/xlsx')
    assert '旧版合成盖章' in all_text(old.content) and '新版内容' not in all_text(old.content)
    assert '新版内容' in all_text(new.content)
    own=s.request('GET',base+'/export/student/'+s.me['student_id']+'/xlsx');assert own.status_code==200
    text=all_text(own.content);assert '旧版合成盖章' in text and '新版内容' not in text
    assert 'SECRET_HUMAN' not in text and 'SECRET_AI' not in text
    assert actors['roster'][1]['id'] not in text and actors['roster'][1]['name'] not in text
    assert s.request('GET',base+'/export/student/'+actors['roster'][1]['id']+'/xlsx').status_code==403


def test_category_filter_and_filtered_export(actors,clock):
    t=actors['teacher'];s=actors['students'][0];p=project(actors,clock);base='/api/projects/'+p['id']
    e=upload(s,p,86);annotate(t,p,e,has_stamp=None)
    e2=upload(s,p,87)
    r=t.request('GET',base+'/export/table/achievements?mode=attention&status=unlabeled&source=personal');assert r.status_code==200
    assert len(parse_table(r.content,'rows.xlsx'))==1
    r=t.request('GET',base+'/export/xlsx?category_id=competition.professional');assert r.status_code==200
    assert '分类汇总' in workbook(r.content)
    for param in ['status=not-a-status','category_id=no-category','mode=invalid','source=invalid']:
        assert t.request('GET',base+'/export/table/achievements?'+param).status_code==422
    assert t.request('GET',base+'/export/xlsx?snapshot_id=no-snapshot').status_code==404


def test_personal_pdf_without_images_is_explicit_not_empty_cover(actors,clock):
    p=project(actors,clock);s=actors['students'][0]
    r=actors['teacher'].request('GET','/api/projects/'+p['id']+'/export/student/'+s.me['student_id'])
    assert r.status_code==422 and '图片' in r.text
