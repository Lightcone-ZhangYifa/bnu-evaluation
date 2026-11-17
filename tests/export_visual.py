"""Optional rendered-PDF verification for export_scale.py artifacts.

Run after installing requirements-visual.txt. Does not mutate export files.
Checks every page; renders first/last pages for human inspection.
"""
from pathlib import Path
import argparse,json
import fitz


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    reports=[]
    for filename in ('合成学生_个人图片材料.pdf','边界测试_长说明分页.pdf'):
        path=args.input/filename
        with fitz.open(path) as document:
            counts=[];min_image_height=10000;observed=set()
            for index,page in enumerate(document):
                images=page.get_image_info()
                assert len(images)==1,(filename,index+1,len(images))
                image=images[0];rect=fitz.Rect(image['bbox'])
                assert rect.x0>=37.9 and rect.x1<=page.rect.width-37.9
                assert rect.y0>=140 and rect.y1<=page.rect.height-64.9
                assert abs(rect.width/rect.height-image['width']/image['height'])<0.002
                min_image_height=min(min_image_height,rect.height)
                text=page.get_text()
                assert '北京师范大学学生综合测评管理系统' in text
                assert f'共 {len(document)} 页' in text
                assert '原始资源SHA256' in text
                for block in page.get_text('dict')['blocks']:
                    if block['type']!=0:continue
                    for line in block['lines']:
                        for span in line['spans']:
                            if not span['text'].strip():continue
                            x0,y0,x1,y1=span['bbox']
                            assert x0>=35 and x1<=page.rect.width-34,(filename,index+1,'horizontal',span)
                            assert y0>=10 and y1<=page.rect.height-5,(filename,index+1,'vertical',span)
                            assert y1<=rect.y0+0.8 or y0>=page.rect.height-60,(filename,index+1,'text overlaps image',span)
                if '是否有盖章' in text:observed.add('stamp')
                if '盖章上的文字' in text:observed.add('stamp_text')
                counts.append(len(images))
            assert observed=={'stamp','stamp_text'}
            for idx in sorted({0,min(1,len(document)-1),len(document)-1}):
                document[idx].get_pixmap(matrix=fitz.Matrix(1.4,1.4)).save(str(args.output/f'{path.stem}-{idx+1}.png'))
            reports.append({'file':filename,'pages':len(document),'image_count_per_page':counts,'minimum_image_height_points':round(min_image_height,2),'text_and_image_bounds_valid':True,'stamp_fields_present':True})
    result={'version':'1.0.0rc4','renderer':'PyMuPDF '+fitz.VersionBind,'files':reports,'passed':len(reports),'failed':0,'scope':'检查全部PDF页面的一图数量、无裁切等比、页眉页脚、文字边界及文字在图片上方；另保存代表页供人工查看。非实体打印机验收。'}
    (args.output/'pdf-report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
