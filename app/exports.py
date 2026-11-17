import hashlib
from io import BytesIO
from html import escape
import json, zipfile
from .tabular import xlsx_bytes
from .security import canonical
from .schemas import STATUS_LABELS


from .reporting import result_workbook, result_records, description_line, stamp_label
from .pdf_export import personal_pdf


def result_xlsx(db,domain,project,computed=None,**options):
    return result_workbook(db,domain,project,computed,**options)


def material_bundle(db, domain, project):
    """Read-only, browser-readable bundle with one copy of each original resource.

    This is not a project import format. Identity and result pages are private;
    only an authorized teacher may request this export.
    """
    from .db import Resource
    result = domain.compute(db, project)
    roster = domain.roster(db, project.body['student_ids'])
    sources = {d['sha256']: d.get('name', '来源文件') for d in project.body['source_documents']}
    for item in project.body['evidence']:
        sources.setdefault(item['resource_sha'], '学生证明图片')
    for listing, version in domain.public_versions(db, project, history=True):
        sources.setdefault(version.body['resource_sha'], version.body.get('source_name') or listing.name)
    for version in project.body['academic_versions']:
        sources.setdefault(version['resource_sha'], '课程成绩来源')
    resources = [db.get(Resource, sha) for sha in sorted(sources)]
    resource_map={r.sha256:r for r in resources if r is not None}
    if any(r is None for r in resources):
        raise ValueError('材料资源缺失，不能导出。请联系管理员核对。')
    if sum(r.size for r in resources) > domain.settings.max_archive_bytes:
        raise ValueError('本工程原始资源超过材料包大小限制，请分别导出个人材料。')
    extensions = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp', 'image/heic': '.heic',
                  'application/pdf': '.pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
                  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': '.xlsx'}
    paths = {r.sha256: '原始资源/' + r.sha256 + extensions.get(r.media_type, '.bin') for r in resources}
    def html(title, body):
        return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                '<title>' + escape(title) + '</title><style>body{max-width:1100px;margin:32px auto;padding:0 20px;font:15px/1.8 "Microsoft YaHei",sans-serif;color:#263442}'
                'h1{font-size:23px}h2{font-size:18px}table{width:100%;border-collapse:collapse}td,th{padding:9px;border:1px solid #dce0e5;text-align:left}'
                'td{overflow-wrap:anywhere}th{background:#efefef}a{color:#254b75}img{max-width:100%;max-height:680px}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>'
                '<h1>' + escape(title) + '</h1>' + body + '</html>')
    out = BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for r in resources:
            original=domain.store.path(r.sha256).read_bytes()
            if len(original)!=r.size or hashlib.sha256(original).hexdigest()!=r.sha256:
                raise ValueError("材料资源校验失败，未生成材料包："+r.sha256)
            z.writestr(paths[r.sha256],original)
        links = []
        for student in roster:
            r = result['students'][student['id']]
            filename = '个人记录/' + student['id'] + '.html'
            links.append('<tr><td>' + escape(student['number']) + '</td><td><a href="' + filename + '">' + escape(student['name']) + '</a></td><td>' + escape(student['class_name']) + '</td><td>' + escape(r['display_total']) + '</td><td>' + ('完整' if r['ready'] else '待认定或缺失') + '</td></tr>')
            body = '<p>当前计算结果，仅用于教师核对，不替代正式公示。原始资源均保留在本材料包内。</p><p>学号：' + escape(student['number']) + '</p>'
            body += '<p>当前可计算数值：' + escape(r['display_total']) + '；状态：' + ('完整' if r['ready'] else '待认定或缺失') + '</p>'
            records={x['id']:x for x in result_records(project,student,r,project.body['rule_versions'][-1]['nodes'])}
            for material in r['materials']:
                a = material['annotation']; href = '../' + paths[material['resource_sha']]
                body += '<h2>' + escape(a.get('description') or '未标注材料') + '</h2><p>结论：' + escape(STATUS_LABELS[a['status']]) + '；局部分值：' + escape(a['score']) + '</p>'
                body += '<p>' + escape(a.get('rule_item', '')) + ' / ' + escape(a.get('scenario', '')) + '</p><p><a href="' + href + '">打开原始材料</a></p>'
                detail=records[material['id']]
                fields=[('材料日期',detail['material_date'] or '日期待确认'),('完整分类',detail['category_path']),('具体加分项',detail['rule_item']),('加分情形',detail['scenario']),('规则出处',detail['rule_source']),('是否有盖章',stamp_label(detail['has_stamp'])),('盖章上的文字',detail['stamp_text'] or '未填写'),('计算处理',detail['calculation_status']+'；'+detail['calculation_note']),('机械生成描述',description_line(detail)),('已人工核对','是' if detail['reviewed'] else '否'),('材料ID',material['id']),('原始资源SHA256',material['resource_sha'])]
                body += '<table>'+''.join('<tr><th>'+escape(k)+'</th><td>'+escape(str(v))+'</td></tr>' for k,v in fields)+'</table>'
                resource = resource_map[material['resource_sha']]
                if resource.media_type in ('image/jpeg', 'image/png', 'image/webp'):
                    body += '<img src="' + href + '" alt="证明图片">'
            body += '<h2>公共名单中的本人记录</h2><table><tr><th>名单／版本</th><th>机械生成描述</th><th>是否有盖章</th><th>盖章上的文字</th></tr>'
            for row in r['public_rows']:
                detail=records[f"public:{row['list_id']}:{row['id']}"]
                body += '<tr><td>' + escape(row['list_name'])+' / v'+str(row['list_revision']) + '</td><td>' + escape(description_line(detail)) + '</td><td>' + stamp_label(detail['has_stamp']) + '</td><td>'+escape(detail['stamp_text'] or '未填写')+'</td></tr>'
            body += '</table><h2>计算轨迹</h2><pre>' + escape(json.dumps(r['nodes'], ensure_ascii=False, indent=2)) + '</pre>'
            z.writestr(filename, html(student['name'] + ' — 个人记录', body))
        body = '<p>北京师范大学学生综合测评管理系统</p><p>只读材料包。包含个人信息，仅限授权人员使用。此文件不能重新导入系统。</p>'
        body += '<p>工程修订：' + str(project.revision) + '；计算输入指纹：' + result['input_hash'] + '</p>'
        body += '<table><tr><th>学号</th><th>姓名</th><th>班级</th><th>当前可计算数值</th><th>状态</th></tr>' + ''.join(links) + '</table>'
        body += '<h2>集中保存的原始资源</h2><ul>' + ''.join('<li><a href="' + paths[sha] + '">' + escape(sources[sha]) + '（' + sha[:12] + '）</a></li>' for sha in paths) + '</ul>'
        z.writestr('请先打开.html', html(project.title + ' — 材料目录', body))
        z.writestr('计算结果.json', json.dumps(result, ensure_ascii=False, indent=2))
        z.writestr('分类汇总与全部成果.xlsx', result_xlsx(db,domain,project,result))
        z.writestr('材料清单.json', json.dumps({'project_id': project.id, 'revision': project.revision,
            'resources': [{'sha256': r.sha256, 'size': r.size, 'path': paths[r.sha256]} for r in resources]}, ensure_ascii=False, indent=2))
    return out.getvalue()
