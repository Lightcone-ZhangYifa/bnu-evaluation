"""Small, dependency-free OOXML interchange (never the scoring source of truth).

All text is explicitly typed as inline strings, eliminating spreadsheet formula
injection. Import accepts UTF-8 CSV and the first XLSX sheet with plain headers.
This module is part of the deployed product, not an edit of the supplied workbook.
"""
import csv, io, zipfile, re
from xml.sax.saxutils import escape
from defusedxml import ElementTree as ET
from .storage import safe_zip, InvalidFile

NS={"s":"http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

def col(number):
    out=""
    while number:
        number,r=divmod(number-1,26);out=chr(65+r)+out
    return out

# Compatibility name for read-only parsing; writing requires a semantic compiler.
from .table_reader import read_table as parse_table

def csv_bytes(headers,rows):
    stream=io.StringIO();w=csv.writer(stream);w.writerow(headers)
    for row in rows:
        w.writerow([("'"+str(v)) if str(v).lstrip().startswith(("=","+","-","@")) and not isinstance(v,(int,float)) else v for v in row])
    return b"\xef\xbb\xbf"+stream.getvalue().encode()

# Layout objects belong to the deployed application's deterministic OOXML writer.
# No office suite, macro engine, spreadsheet formula evaluation or network call is used.
from dataclasses import dataclass, field
from decimal import Decimal
from math import ceil, isfinite
import unicodedata
from xml.sax.saxutils import quoteattr

@dataclass
class SheetSpec:
    name: str
    rows: list
    widths: list[float] = field(default_factory=list)
    header_rows: int = 1
    freeze_rows: int | None = None
    freeze_cols: int = 0
    merges: list[str] = field(default_factory=list)
    styles: dict = field(default_factory=dict)  # 1-based (row, column) -> style index
    row_heights: dict = field(default_factory=dict)
    filter_row: int | None = 1
    repeat_rows: int | None = 1
    repeat_cols: int = 0
    landscape: bool = True
    paper_size: int = 9
    fit_width: int = 1
    validations: list = field(default_factory=list)  # (range, allowed strings)
    description: str = ""


def text_width(value):
    return sum(2 if unicodedata.east_asian_width(ch) in ('W','F') else 1 for ch in str(value))


def visual_lines(value, width):
    return sum(max(1, ceil(text_width(line) / max(4, width - 1))) for line in str(value or '').split('\n'))


def split_text(value, width, max_lines=16):
    """Lossless continuation segments, with bounded printed row heights.

    Prefer a whole achievement per segment; exceptionally long individual lines
    are split at a character boundary. Concatenation recreates the original text.
    """
    value=str(value or '');limit=max(4,int(width-1));out=[];piece='';line_width=0;lines=1
    for ch in value:
        cw=2 if unicodedata.east_asian_width(ch) in ('W','F') else 1
        advance=ch=='\n' or (line_width+cw>limit and line_width>0)
        if advance and lines>=max_lines:
            out.append(piece);piece='';line_width=0;lines=1
        elif advance:
            lines+=1;line_width=0
        piece+=ch
        if ch!='\n':line_width+=cw
    if piece or not out:out.append(piece)
    return out


def _xml_text(value):
    text=str(value if value is not None else '')
    # Keep TAB/LF; invalid XML code points cannot be emitted into an XLSX.
    text=''.join(ch if (ch in '\t\n\r' or 0x20<=ord(ch)<=0xD7FF or 0xE000<=ord(ch)<=0xFFFD or 0x10000<=ord(ch)<=0x10FFFF) else '\ufffd' for ch in text)
    if len(text.encode('utf-16-le'))//2 > 32767:
        raise ValueError('单元格内容超过 Excel 的32767字符限制；请拆分为续行，不能截断导出内容。')
    return escape(text)


STYLES='''<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<numFmts count="1"><numFmt numFmtId="164" formatCode="0.############;-0.############;0"/></numFmts>
<fonts count="5"><font><sz val="10"/><color rgb="FF000000"/><name val="宋体"/><family val="1"/><charset val="134"/></font><font><b/><sz val="10"/><color rgb="FF000000"/><name val="宋体"/><charset val="134"/></font><font><b/><sz val="14"/><color rgb="FF000000"/><name val="宋体"/><charset val="134"/></font><font><sz val="9"/><color rgb="FF444444"/><name val="宋体"/><charset val="134"/></font><font><sz val="10"/><color rgb="FF000000"/><name val="宋体"/><charset val="134"/></font></fonts>
<fills count="4"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FFE8E8E8"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFF3F3F3"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border><border><left style="thin"><color rgb="FF777777"/></left><right style="thin"><color rgb="FF777777"/></right><top style="thin"><color rgb="FF777777"/></top><bottom style="thin"><color rgb="FF777777"/></bottom><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="12">
<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>
<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
<xf numFmtId="164" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
<xf numFmtId="0" fontId="3" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>
<xf numFmtId="164" fontId="1" fillId="3" borderId="1" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
<xf numFmtId="0" fontId="3" fillId="0" borderId="1" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
<xf numFmtId="0" fontId="1" fillId="3" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
<xf numFmtId="49" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>
<xf numFmtId="1" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
<xf numFmtId="1" fontId="1" fillId="3" borderId="1" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'''


def xlsx_bytes(sheets):
    """Write deterministic read-only reports or blank interchange templates.

    Accept legacy (name, headers, rows) tuples and fully specified SheetSpec.
    Text is never converted into a formula; numbers are explicitly typed. The
    report contains server-calculated values, not a second scoring engine.
    """
    specs=[]
    for sheet in sheets:
        if isinstance(sheet,SheetSpec):specs.append(sheet)
        else:
            name,headers,rows=sheet
            widths=[max(12,min(42,max(text_width(h)+2, 16 if i<3 else 28))) for i,h in enumerate(headers)]
            specs.append(SheetSpec(name,[headers]+list(rows),widths))
    if not specs:raise ValueError('至少需要一个工作表。')
    out=io.BytesIO();types=[];sheet_tags=[];rels=[];defined=[];used=set()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        for idx,spec in enumerate(specs,1):
            safe=re.sub(r'[\[\]:*?/\\]','_',spec.name).strip("'")[:31] or '工作表'
            base=safe;n=1
            while safe.casefold() in used:
                n+=1;suffix='_'+str(n);safe=base[:31-len(suffix)]+suffix
            used.add(safe.casefold());sheet_tags.append(f'<sheet name={quoteattr(safe)} sheetId="{idx}" r:id="r{idx}"/>')
            rels.append(f'<Relationship Id="r{idx}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{idx}.xml"/>')
            types.append(f'<Override PartName="/xl/worksheets/sheet{idx}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
            rows=spec.rows or [['无记录']];cols=max(len(r) for r in rows)
            if not 1<=cols<=16384 or len(rows)>1048576:
                raise ValueError('导出范围超过 Excel 行列上限，请按班级或分类分别导出。')
            widths=[max(5,min(80,spec.widths[i] if i<len(spec.widths) else 24)) for i in range(cols)]
            end=f'{col(cols)}{len(rows)}';fr=spec.header_rows if spec.freeze_rows is None else spec.freeze_rows;fc=spec.freeze_cols
            xml=['<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
                 f'<sheetPr><pageSetUpPr fitToPage="{1 if spec.fit_width else 0}"/></sheetPr><dimension ref="A1:{end}"/>',
                 '<sheetViews><sheetView showGridLines="0" workbookViewId="0">']
            if fr or fc:
                pane='bottomRight' if fr and fc else 'bottomLeft' if fr else 'topRight';active=f'{col(fc+1)}{fr+1}'
                xml.append(f'<pane xSplit="{fc}" ySplit="{fr}" topLeftCell="{active}" activePane="{pane}" state="frozen"/><selection pane="{pane}" activeCell="{active}" sqref="{active}"/>')
            xml.append('</sheetView></sheetViews><sheetFormatPr defaultRowHeight="24"/><cols>')
            xml += [f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i,w in enumerate(widths,1)]
            xml.append('</cols><sheetData>')
            for rn,row in enumerate(rows,1):
                height=spec.row_heights.get(rn)
                if height is None:
                    height=min(409,max(28 if rn<=spec.header_rows else 24, max((visual_lines(v,widths[i]) for i,v in enumerate(row)),default=1)*13+8))
                if not 0<height<=409:raise ValueError('工作表行高超出允许范围。')
                xml.append(f'<row r="{rn}" ht="{height:g}" customHeight="1">')
                for cn in range(1,cols+1):
                    v=row[cn-1] if cn<=len(row) else ''
                    numeric=isinstance(v,(int,float,Decimal)) and not isinstance(v,bool)
                    style=spec.styles.get((rn,cn),1 if rn<=spec.header_rows else 2 if numeric else 0)
                    address=f'{col(cn)}{rn}'
                    if numeric:
                        d=Decimal(str(v))
                        if not d.is_finite():raise ValueError('不能导出非有限数值。')
                        if d==d.to_integral_value() and style in (2,5):style=10 if style==2 else 11
                        xml.append(f'<c r="{address}" s="{style}" t="n"><v>{format(d,"f")}</v></c>')
                    else:
                        v=('是' if v else '否') if isinstance(v,bool) else v
                        xml.append(f'<c r="{address}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{_xml_text(v)}</t></is></c>')
                xml.append('</row>')
            xml.append('</sheetData>')
            if spec.filter_row is not None:xml.append(f'<autoFilter ref="A{spec.filter_row}:{end}"/>')
            if spec.merges:
                xml.append(f'<mergeCells count="{len(spec.merges)}">'+''.join(f'<mergeCell ref={quoteattr(m)}/>' for m in spec.merges)+'</mergeCells>')
            if spec.validations:
                vals=[]
                for ref,choices in spec.validations:
                    formula='"'+','.join(choices)+'"'
                    vals.append(f'<dataValidation type="list" allowBlank="1" showErrorMessage="1" errorTitle="请按模板填写" error="请从下拉选项中选择。" sqref={quoteattr(ref)}><formula1>{escape(formula)}</formula1></dataValidation>')
                xml.append(f'<dataValidations count="{len(vals)}">'+''.join(vals)+'</dataValidations>')
            xml += ['<printOptions horizontalCentered="0" gridLines="0"/>',
                '<pageMargins left="0.3" right="0.3" top="0.55" bottom="0.5" header="0.2" footer="0.2"/>',
                f'<pageSetup paperSize="{spec.paper_size}" orientation="{"landscape" if spec.landscape else "portrait"}" fitToWidth="{spec.fit_width}" fitToHeight="0" scale="80" useFirstPageNumber="0"/>',
                '<headerFooter><oddHeader>'+escape('&C&"宋体,常规"&9北京师范大学学生综合测评管理系统')+'</oddHeader><oddFooter>'+escape('&L&"宋体,常规"&8仅供授权人员使用&C&A&R第 &P 页 / 共 &N 页')+'</oddFooter></headerFooter></worksheet>']
            z.writestr(f'xl/worksheets/sheet{idx}.xml',''.join(xml))
            quoted="'"+safe.replace("'","''")+"'"
            defined.append(f'<definedName name="_xlnm.Print_Area" localSheetId="{idx-1}">{escape(quoted+"!$A$1:$"+col(cols)+"$"+str(len(rows)))}</definedName>')
            repeats=[]
            if spec.repeat_rows:repeats.append(f'{quoted}!$1:${spec.repeat_rows}')
            if spec.repeat_cols:repeats.append(f'{quoted}!$A:${col(spec.repeat_cols)}')
            if repeats:defined.append(f'<definedName name="_xlnm.Print_Titles" localSheetId="{idx-1}">{escape(",".join(repeats))}</definedName>')
        types += ['<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>','<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>']
        rels.append('<Relationship Id="styles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>')
        z.writestr('[Content_Types].xml','<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'+''.join(types)+'</Types>')
        z.writestr('_rels/.rels','<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="root" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml','<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><bookViews><workbookView/></bookViews><sheets>'+''.join(sheet_tags)+'</sheets><definedNames>'+''.join(defined)+'</definedNames></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels','<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+''.join(rels)+'</Relationships>')
        z.writestr('xl/styles.xml',STYLES)
    return out.getvalue()
