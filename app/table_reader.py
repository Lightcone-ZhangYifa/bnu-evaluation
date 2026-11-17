"""Bounded plain-table reader with source coordinates; no coercion or execution.

CSV line numbers are physical source lines (quoted multiline cells count). XLSX
coordinates are the original worksheet coordinates, including gaps/empty rows.
Semantic import compilers consume these rows without losing their provenance.
"""
from __future__ import annotations
from dataclasses import dataclass
import csv
import io
import re
from defusedxml import ElementTree as ET
from .storage import safe_zip

NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
MAX_ROWS = 20000
MAX_COLS = 200
MAX_ERRORS = 200


def column_name(index: int) -> str:
    value = ''
    while index:
        index, r = divmod(index - 1, 26)
        value = chr(65 + r) + value
    return value


@dataclass(frozen=True)
class Diagnostic:
    sheet: str
    row: int
    column: str
    field: str
    code: str
    message: str

    def as_dict(self):
        return vars(self)


class TableError(ValueError):
    def __init__(self, errors, total=None):
        self.errors = list(errors)[:MAX_ERRORS]
        self.total = total if total is not None else len(errors)
        super().__init__('表格未导入。发现 %d 个问题，请按位置修正后重新校验。' % self.total)

    def response(self):
        return {'message': str(self), 'errors': [e.as_dict() for e in self.errors],
                'error_count': self.total, 'truncated': self.total > len(self.errors),
                'saved': False}


class TableRow(dict):
    def __init__(self, values, sheet, row, headers):
        super().__init__(values)
        self.sheet, self.row, self.headers = sheet, row, headers
        self.labels = dict(zip(headers,headers))

    def error(self, field, code, message):
        col = column_name(self.headers.index(field) + 1) if field in self.headers else ''
        return Diagnostic(self.sheet, self.row, col, self.labels.get(field,field), code, message)


class PlainTable(list):
    def __init__(self, rows, sheet, header_row, headers):
        super().__init__(rows)
        self.sheet, self.header_row, self.headers = sheet, header_row, headers

    def header_error(self, field, code, message):
        col = column_name(self.headers.index(field) + 1) if field in self.headers else ''
        return Diagnostic(self.sheet, self.header_row, col, field, code, message)


def read_table(data: bytes, filename: str) -> PlainTable:
    errors = []
    rows = []
    sheet_name = 'CSV'
    try:
        if filename.lower().endswith('.csv'):
            try:
                text = data.decode('utf-8-sig')
            except UnicodeDecodeError:
                raise ValueError('CSV 必须使用 UTF-8 编码；请另存为 CSV UTF-8。')
            reader = csv.reader(io.StringIO(text, newline=''), strict=True)
            previous_line = 0
            for row in reader:
                rows.append((previous_line + 1, row))
                previous_line = reader.line_num
                if len(rows) > MAX_ROWS + 1:
                    raise ValueError('表格最多 20000 行数据；请分批导入。')
        elif filename.lower().endswith('.xlsx'):
            with safe_zip(data, 80 * 1024 * 1024, 1500) as archive:
                if any(n.lower().endswith('vbaproject.bin') or n.startswith('xl/externalLinks/') for n in archive.namelist()):
                    raise ValueError('导入文件不得包含宏或外部工作簿链接。')
                shared = []
                if 'xl/sharedStrings.xml' in archive.namelist():
                    root = ET.fromstring(archive.read('xl/sharedStrings.xml'))
                    shared = [''.join(t.text or '' for t in si.iterfind('.//s:t', NS)) for si in root.findall('s:si', NS)]
                workbook = ET.fromstring(archive.read('xl/workbook.xml'))
                sheet = workbook.find('s:sheets/s:sheet', NS)
                if sheet is None:
                    raise ValueError('工作簿没有工作表。')
                sheet_name = sheet.attrib.get('name', '工作表1')
                if sheet.attrib.get('state', 'visible') != 'visible':
                    raise ValueError('第一张数据工作表不能隐藏。')
                rid = sheet.attrib.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
                rels = ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
                relation = next((r for r in rels if r.attrib.get('Id') == rid), None)
                if relation is None or relation.attrib.get('TargetMode') == 'External':
                    raise ValueError('工作表路径缺失或引用外部文件。')
                target = relation.attrib['Target']
                path = target.lstrip('/') if target.startswith('/') else 'xl/' + target
                if '..' in path.split('/') or '\\' in path:
                    raise ValueError('工作表路径不安全。')
                root = ET.fromstring(archive.read(path))
                if root.find('s:mergeCells', NS) is not None:
                    raise ValueError('数据工作表不能包含合并单元格；请使用系统下载的填写模板。')
                previous_row = 0
                for row in root.findall('s:sheetData/s:row', NS):
                    number = int(row.attrib.get('r', '0'))
                    if number <= previous_row or number > 1048576:
                        raise ValueError('工作表行号重复、未按顺序排列或超出范围。')
                    previous_row = number
                    if len(rows) > MAX_ROWS:
                        raise ValueError('表格最多 20000 行数据；请分批导入。')
                    values = {}
                    for cell in row.findall('s:c', NS):
                        ref = cell.attrib.get('r', '')
                        match = re.fullmatch(r'([A-Z]{1,3})([1-9][0-9]*)', ref)
                        if not match or int(match[2]) != number:
                            raise ValueError('单元格地址与所在行不一致：' + ref)
                        index = 0
                        for char in match[1]:
                            index = index * 26 + ord(char) - 64
                        if not 1 <= index <= MAX_COLS or index in values:
                            raise ValueError('列数超过200列或存在重复单元格：' + ref)
                        if cell.find('s:f', NS) is not None:
                            errors.append(Diagnostic(sheet_name, number, match[1], '', 'FORMULA_NOT_ALLOWED', '不接受公式，即使有缓存结果也不能导入；请复制并粘贴为值。'))
                        v = cell.find('s:v', NS)
                        kind = cell.attrib.get('t')
                        value = v.text or '' if v is not None else ''
                        if kind == 's':
                            if not value.isdigit() or int(value) >= len(shared):
                                raise ValueError('共享字符串索引无效：' + ref)
                            value = shared[int(value)]
                        elif kind == 'inlineStr':
                            inline = cell.find('s:is', NS)
                            value = ''.join(t.text or '' for t in inline.iterfind('.//s:t', NS)) if inline is not None else ''
                        elif kind == 'e':
                            errors.append(Diagnostic(sheet_name, number, match[1], '', 'CELL_ERROR', '该单元格是电子表格错误值。'))
                        values[index] = value
                    rows.append((number, [values.get(i, '') for i in range(1, max(values, default=0) + 1)]))
        else:
            raise ValueError('仅支持 UTF-8 CSV 或 XLSX 文件。')
    except TableError:
        raise
    except (ValueError, KeyError, IndexError, TypeError, csv.Error, ET.ParseError) as exc:
        raise TableError([Diagnostic(sheet_name, 0, '', '', 'FILE_STRUCTURE', str(exc))]) from exc
    nonempty = [(n, r) for n, r in rows if any(str(v).strip() for v in r)]
    if not nonempty:
        raise TableError([Diagnostic(sheet_name, 1, '', '', 'EMPTY_TABLE', '表格为空，没有表头。')])
    header_row, raw_headers = nonempty[0]
    # Ignore styled trailing blank columns, but never an interior blank header.
    headers = [str(v).strip() for v in raw_headers]
    while headers and not headers[-1]:
        headers.pop()
    seen = set()
    for i, header in enumerate(headers, 1):
        if not header or header in seen:
            errors.append(Diagnostic(sheet_name, header_row, column_name(i), header, 'DUPLICATE_OR_EMPTY_HEADER', '表头为空或重复。'))
        seen.add(header)
    records = []
    for number, row in nonempty[1:]:
        if len(row) > MAX_COLS:
            errors.append(Diagnostic(sheet_name, number, '', '', 'TOO_MANY_COLUMNS', '最多200列。'))
        for i in range(len(headers), len(row)):
            if str(row[i]).strip():
                errors.append(Diagnostic(sheet_name, number, column_name(i + 1), '', 'EXTRA_CELL', '该单元格没有对应表头，不能静默丢弃。'))
        values = {h: str(row[i]).strip() if i < len(row) else '' for i, h in enumerate(headers)}
        records.append(TableRow(values, sheet_name, number, headers))
    if errors:
        raise TableError(errors)
    return PlainTable(records, sheet_name, header_row, headers)


def normalize_headers(table: PlainTable, aliases: dict, required=()):
    errors = []
    targets = {}
    for header in table.headers:
        if header not in aliases:
            errors.append(table.header_error(header, 'UNKNOWN_COLUMN', '此导入类型没有该字段；请使用对应的可填写模板，不能导入计算结果总表。'))
        elif aliases[header] in targets:
            errors.append(table.header_error(header, 'DUPLICATE_FIELD', '与“%s”映射到同一个字段。' % targets[aliases[header]]))
        else:
            targets[aliases[header]] = header
    for field in required:
        if field not in targets:
            errors.append(table.header_error(field, 'MISSING_COLUMN', '缺少必填列：' + field))
    if errors:
        raise TableError(errors)
    normalized=[]
    for row in table:
        result=TableRow({aliases[h]: row[h] for h in table.headers}, row.sheet, row.row, [aliases[h] for h in table.headers])
        result.labels={aliases[h]:h for h in table.headers}
        normalized.append(result)
    return normalized
