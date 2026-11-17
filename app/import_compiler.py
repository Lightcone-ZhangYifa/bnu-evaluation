"""Semantic import checking. Validation emits diagnostics, never partial writes."""
from __future__ import annotations
from pydantic import ValidationError
from .table_reader import TableError, normalize_headers
from .schemas import StudentInput, PublicRow, GradeCourse, GradeEntry, STATUS_LABELS
from .rules import validate_tree, course_result, within_period
from .db import uid

STUDENT_ALIASES = {'学号':'number','姓名':'name','班级':'class_name',
                   'number':'number','name':'name','class_name':'class_name'}
PUBLIC_COLUMNS = {'学号':'number','姓名':'name','分类ID':'category_id','分数':'score',
    '描述':'description','情形':'scenario','规则出处':'rule_source','日期':'material_date',
    '备注':'note','状态':'status','成果键':'outcome_key','基础分':'base_score','系数':'factors',
    '分母':'denominator','是否有盖章':'has_stamp','盖章上的文字':'stamp_text','行ID':'id'}
ACADEMIC_COLUMNS = {'学号':'number','课程ID':'course_id','课程名称':'name','学分':'credits',
    '成绩制':'scheme','成绩':'score','免计入':'excluded','免计入依据':'exclusion_reason',
    '替代课程':'substitute_name','替代依据':'substitution_reason'}
STATUS_CODES = {**{v:k for k,v in STATUS_LABELS.items()}, **{k:k for k in STATUS_LABELS},
    '不属于加分情形':'out_of_scope','不属于综测规定的加分情形':'out_of_scope',
    '不在参评期':'out_of_period','不在加分时间':'out_of_period'}


def boolean(value, optional=False):
    if value in ('','未判定','null') and optional: return None
    if value in ('是','true','TRUE','1'): return True
    if value in ('否','false','FALSE','0'): return False
    raise ValueError('仅可填写是/否' + ('/未判定或留空。' if optional else '。'))


def factors(value):
    return [v.strip() for v in value.replace('，',',').split(',') if v.strip()]


def errors_for(row, exc, fallback=''):
    if isinstance(exc, ValidationError):
        return [row.error(str(e['loc'][0]) if e['loc'] else fallback, 'INVALID_VALUE', e['msg']) for e in exc.errors(include_url=False)]
    return [row.error(fallback, 'INVALID_VALUE', str(exc))]


def check_identity(row, roster, *, check_name=True):
    student = roster.get(row.get('number'))
    if not student: raise ValueError('学号不在获准导入的名册中；不会按姓名猜测身份。')
    if check_name and row.get('name') and row['name'] != student['name']:
        raise ValueError('学号与名册姓名不一致。')
    return student


def compile_roster(table, existing):
    rows = normalize_headers(table, STUDENT_ALIASES, ('number','name','class_name'))
    errors=[]; result=[]; seen={}
    if not rows or len(rows)>2000:
        raise TableError([table.header_error('', 'ROW_LIMIT', '每次须有1—2000名学生。')])
    for row in rows:
        try:
            s=StudentInput.model_validate(dict(row))
            if s.number in seen:
                errors.append(row.error('number','DUPLICATE_ID','学号重复；首次出现在第%d行。'%seen[s.number]))
            seen[s.number]=row.row
            old=existing.get(s.number)
            if old and old['name']!=s.name:
                errors.append(row.error('name','IDENTITY_CONFLICT','此学号已有不同姓名；请先在名册页面明确更正身份，导入不会覆盖姓名。'))
            result.append(s)
        except ValueError as exc: errors.extend(errors_for(row,exc))
    if errors: raise TableError(errors)
    return result


def compile_public(table, roster, nodes, meta=None):
    rows=normalize_headers(table,PUBLIC_COLUMNS,('number','category_id','score','description','scenario','rule_source','status'))
    if not rows: raise TableError([table.header_error('','EMPTY_DATA','填写数据表没有名单行。')])
    by,children,_=validate_tree(nodes);errors=[];result=[];seen={};ids=set()
    for row in rows:
        try:
            try: student=check_identity(row,roster)
            except ValueError as exc:
                errors.extend(errors_for(row,exc,'number'));continue
            r=dict(row);r.pop('number',None);r.pop('name',None)
            r['status']=STATUS_CODES.get(r.get('status'),r.get('status'))
            r['id']=r.get('id') or uid();r['student_id']=student['id']
            r['has_stamp']=boolean(r.get('has_stamp',''),True)
            r['stamp_text']=r.get('stamp_text') or None
            r['factors']=factors(r.get('factors',''))
            r['denominator']=r.get('denominator') or '1'
            for key in ('base_score','outcome_key','material_date'):r[key]=r.get(key) or None
            parsed=PublicRow.model_validate(r)
            if parsed.category_id not in by or children[parsed.category_id]:
                errors.append(row.error('category_id','INVALID_CATEGORY','分类必须是参考工程当前规则树中的叶子ID。'))
            if parsed.id in ids: errors.append(row.error('id','DUPLICATE_ROW_ID','名单行ID重复。'))
            ids.add(parsed.id)
            if meta and parsed.status=='valid' and parsed.material_date and not within_period(parsed.material_date,meta['assessment_start'],meta['assessment_end']):
                errors.append(row.error('material_date','INVALID_PERIOD','日期不在参评期内或无法按YYYY[-MM[-DD]]识别；请核对日期或标为需人工判断。'))
            key=(parsed.student_id,parsed.outcome_key)
            if parsed.outcome_key and parsed.status=='valid':
                if key in seen:errors.append(row.error('outcome_key','DUPLICATE_OUTCOME','相同学生的成果键重复；首次出现在第%d行。'%seen[key]))
                seen[key]=row.row
            result.append(parsed.model_dump())
        except ValueError as exc: errors.extend(errors_for(row,exc))
    if errors: raise TableError(errors)
    return result


def compile_academic(table, roster):
    rows=normalize_headers(table,ACADEMIC_COLUMNS,('number','course_id','name','credits','scheme','score'))
    if not rows:raise TableError([table.header_error('','EMPTY_DATA','填写数据表没有课程行。')])
    errors=[];courses={};first_course={};students={};seen={}
    for row in rows:
        try:
            try:student=check_identity(row,roster,check_name=False)
            except ValueError as exc:
                errors.extend(errors_for(row,exc,'number'));continue
            course=GradeCourse(id=row['course_id'],name=row['name'],credits=row['credits'],scheme=row['scheme'])
            previous=courses.get(course.id)
            if previous and previous!=course.model_dump():
                errors.append(row.error('course_id','COURSE_CONFLICT','同一课程ID的名称、学分或成绩制与第%d行不一致。'%first_course[course.id]))
            else:courses[course.id]=course.model_dump();first_course.setdefault(course.id,row.row)
            entry=GradeEntry(course_id=course.id,score=row['score'] or None,
                excluded=boolean(row.get('excluded') or '否'),exclusion_reason=row.get('exclusion_reason',''),
                substitute_name=row.get('substitute_name') or None,substitution_reason=row.get('substitution_reason',''))
            # Validate the grade's scheme and numerical range without evaluating a formula.
            course_result(course.model_dump(), entry.model_dump())
            key=(student['id'],course.id)
            if key in seen:errors.append(row.error('course_id','DUPLICATE_COURSE','此学生此课程重复；首次出现在第%d行。'%seen[key]))
            seen[key]=row.row
            students.setdefault(student['id'],[]).append(entry.model_dump())
        except ValueError as exc:errors.extend(errors_for(row,exc,'score'))
    if len(courses)>200:errors.append(table.header_error('课程ID','COURSE_LIMIT','每工程最多200门课程。'))
    if errors:raise TableError(errors)
    return {'courses':list(courses.values()),'students':[{'student_id':sid,'entries':values} for sid,values in students.items()]}


def roster_fingerprint(db, college_id, numbers, file_sha256):
    """Bind a roster preview to only its target identities, not unrelated classes."""
    from sqlalchemy import select
    from .db import Student
    from .security import canonical
    import hashlib
    matches = {s.number: {"id": s.id, "name": s.name, "class_name": s.class_name, "active": s.active}
               for s in db.scalars(select(Student).where(Student.college_id == college_id)).all()
               if s.number in numbers}
    return hashlib.sha256(canonical({"college": college_id, "file": file_sha256,
        "students": {number: matches.get(number) for number in sorted(numbers)}}).encode()).hexdigest()
