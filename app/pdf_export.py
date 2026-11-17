"""A4 material PDF. Every page contains exactly one uncropped evidence image.

Long structured fields continue on further pages with the same evidence image,
clearly labelled as a continuation, instead of truncating text or creating a
text-only appendix. No cover or synthetic evidence images are inserted.
"""
from __future__ import annotations
from io import BytesIO
from html import escape
from collections import deque
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Paragraph
from PIL import Image
from .reporting import SYSTEM_NAME, result_records, stamp_label, local_time
from .schemas import STATUS_LABELS


def personal_pdf(domain,project,student,result,snapshot_label='当前预览（非正式成绩）',rule_nodes=None,private=True):
    if not result.get('materials'):
        raise ValueError('该学生没有个人图片材料，不能生成图片材料PDF。请使用“个人成果表”导出公共名单、课程计算和其他认定结果。')
    nodes=rule_nodes or project.body['rule_versions'][-1]['nodes']
    records={r['id']:r for r in result_records(project,student,result,nodes)}
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    font='STSong-Light';width,height=A4;margin=38;table_width=width-2*margin;label_width=76
    body=ParagraphStyle('record',fontName=font,fontSize=9,leading=12.4,wordWrap='CJK',splitLongWords=True,textColor='#000000')
    label_style=ParagraphStyle('label',parent=body,fontSize=8.7,leading=12)
    intro_style=ParagraphStyle('intro',parent=body,fontSize=9.5,leading=13)
    def para(value,style=body):
        text=str(value if value not in (None,'') else '未填写')
        text=''.join(c if c in '\n\t' or ord(c)>=32 else '\ufffd' for c in text)
        return Paragraph(escape(text).replace('\n','<br/>'),style)
    introduction=[para(f"姓名：{student['name']}　｜　学号：{student['number']}　｜　班级：{student['class_name']}",intro_style),
        para('工程：'+project.title+'　｜　学年：'+project.body['meta'].get('year',''),intro_style),
        para('导出依据：'+snapshot_label+('；测试工程，非正式成绩。' if project.body['meta'].get('test_only') else ''),intro_style)]
    intro_height=sum(p.wrap(table_width,10000)[1]+3 for p in introduction)
    top=height-83-intro_height
    budget=min(350,top-65-245-24)
    if budget<70:raise ValueError('工程或学生资料过长，无法保持PDF结构与图片清晰度。请核对身份信息及工程名称。')
    def pages_for(fields):
        pending=deque((label,para(value)) for label,value in fields);pages=[];page=[];used=0
        while pending:
            label,p=pending.popleft();_,h=p.wrap(table_width-label_width-12,100000)
            available=budget-used
            if h+8<=available:
                page.append((label,p,h+8));used+=h+8;continue
            if available>=40:
                parts=p.split(table_width-label_width-12,available-8)
                if parts:
                    first=parts[0];_,ph=first.wrap(table_width-label_width-12,100000)
                    page.append((label,first,ph+8))
                    for rest in reversed(parts[1:]):pending.appendleft((label.removesuffix('（续）')+'（续）',rest))
                else:pending.appendleft((label,p))
            else:pending.appendleft((label,p))
            if not page:raise ValueError('材料文字无法安全分页，请检查其中的特殊字符。')
            pages.append(page);page=[];used=0
        if page:pages.append(page)
        return pages
    plans=[]
    for index,m in enumerate(result['materials'],1):
        r=records[m['id']];a=m['annotation']
        fields=[('日期与状态',(r['material_date'] or '日期待确认')+'　；'+STATUS_LABELS.get(r['status'],r['status'])),
            ('评分与核对','局部认定 '+str(r['score'])+' 分；'+('已人工核对' if r['reviewed'] else '未人工核对')+'；标注版本 '+str(r['annotation_version'])),
            ('分类路径',r['category_path'] or '尚未确定'),('材料内容',r['description']),
            ('细则加分项',r['rule_item']),('加分情形',r['scenario']),('规则出处',r['rule_source']),
            ('计算处理',r['calculation_status']+'；'+r['calculation_note']),
            ('盖章情况','是否有盖章：'+stamp_label(r['has_stamp'])+'；盖章上的文字：'+(r['stamp_text'] or ('未能识读／未填写' if r['has_stamp'] is True else '未填写')))]
        if r.get('base_score') is not None:fields.append(('局部计算','基础分 '+str(r['base_score'])+' × '+(' × '.join(r['factors']) or '1')+' ÷ '+str(r['denominator'])+' = '+r['score']))
        if r['date_override_reason']:fields.append(('日期认定依据',r['date_override_reason']))
        fields.append(('提交及修改','提交：'+(local_time(r['submitted_at']) or '未记录')+'；最后修改：'+(local_time(r['last_modified_at']) or '未记录')+'（北京时间）'))
        if r.get('student_note'):
            fields.append(('向学生说明', r['student_note']))
        if private:
            fields.append(('记录身份','提交人：'+str(r.get('submitted_by') or '旧记录未提供')+'；最后修改人：'+str(r.get('last_modified_by') or '旧记录未提供')))
            if r['human_note']:fields.append(('人工备注',r['human_note']))
            if r['ai_note']:fields.append(('AI备注',r['ai_note']))
        if r['outcome_key']:fields.append(('成果键',r['outcome_key']))
        if r['relations']:fields.append(('材料关联',r['relations']))
        pages=pages_for(fields)
        for part,page in enumerate(pages,1):plans.append((index,m,r,part,len(pages),page))
    output=BytesIO();canvas=Canvas(output,pagesize=A4,pageCompression=1)
    canvas.setTitle('个人图片材料档案 - '+student['number']);canvas.setAuthor(SYSTEM_NAME);canvas.setSubject('只读材料导出；每页一张图片；长说明连续分页')
    for page_number,(index,m,r,part,parts,rows) in enumerate(plans,1):
        canvas.setFillColorRGB(0,0,0);canvas.setFont(font,9)
        canvas.drawString(margin,height-31,SYSTEM_NAME);canvas.drawRightString(width-margin,height-31,'个人图片材料档案')
        canvas.setStrokeColorRGB(.35,.35,.35);canvas.setLineWidth(.4);canvas.line(margin,height-40,width-margin,height-40)
        canvas.setFont(font,13);canvas.drawString(margin,height-64,f'材料 {index:03d} / {len(result["materials"]):03d}')
        canvas.setFont(font,9);canvas.drawRightString(width-margin,height-64,('说明续页 ' if part>1 else '材料说明 ')+f'{part}/{parts}')
        y=height-79
        for p in introduction:
            _,ph=p.wrap(table_width,10000);p.drawOn(canvas,margin,y-ph);y-=ph+3
        y-=4
        for label,p,rh in rows:
            canvas.setFillColorRGB(.955,.955,.955);canvas.rect(margin,y-rh,label_width,rh,stroke=0,fill=1)
            canvas.setStrokeColorRGB(.55,.55,.55);canvas.setLineWidth(.3)
            canvas.rect(margin,y-rh,table_width,rh,stroke=1,fill=0);canvas.line(margin+label_width,y,margin+label_width,y-rh)
            lp=para(label,label_style);_,lh=lp.wrap(label_width-10,10000);lp.drawOn(canvas,margin+5,y-4-lh)
            _,ph=p.wrap(table_width-label_width-12,100000);p.drawOn(canvas,margin+label_width+6,y-4-ph);y-=rh
        image_top=y-22;image_bottom=65;image_height=image_top-image_bottom
        if image_height<210:raise ValueError('PDF排版空间不足，未生成不完整文件。')
        canvas.setFillColorRGB(0,0,0);canvas.setFont(font,8.5)
        canvas.drawString(margin,y-14,'证明图片'+('（同一材料的说明续页，不作为新增成果）' if part>1 else '（保持比例，未裁切）'))
        data=domain.store.preview(m['resource_sha'])
        with Image.open(BytesIO(data)) as image:iw,ih=image.size
        scale=min(table_width/iw,image_height/ih);w,h=iw*scale,ih*scale
        canvas.drawImage(ImageReader(BytesIO(data)),margin+(table_width-w)/2,image_bottom+(image_height-h)/2,width=w,height=h)
        canvas.setStrokeColorRGB(.75,.75,.75);canvas.setLineWidth(.3);canvas.rect(margin,image_bottom,table_width,image_height,stroke=1,fill=0)
        canvas.setFillColorRGB(.2,.2,.2);canvas.setFont(font,7.4)
        canvas.drawString(margin,49,'材料ID：'+m['id'])
        canvas.drawString(margin,37,'原始资源SHA256：'+m['resource_sha'])
        canvas.line(margin,29,width-margin,29);canvas.setFont(font,7.8)
        canvas.drawString(margin,17,'仅供授权人员使用；原始图片和标注历史以工程文件为准。')
        canvas.drawRightString(width-margin,17,f'第 {page_number} 页 / 共 {len(plans)} 页')
        canvas.showPage()
    canvas.save();return output.getvalue()
