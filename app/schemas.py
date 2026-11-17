from __future__ import annotations
from typing import Literal, Annotated
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator
from datetime import datetime, date, timezone
from decimal import Decimal, InvalidOperation
import re

Status = Literal["unlabeled", "valid", "untrusted", "out_of_scope", "out_of_period", "manual", "invalid_image", "system_error"]
STATUS_LABELS = {"unlabeled":"未标注", "valid":"有效", "untrusted":"不可信", "out_of_scope":"不属于综测规定的加分情形", "out_of_period":"不在加分时间", "manual":"需人工判断", "invalid_image":"图片无效", "system_error":"系统异常"}

class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

class Node(Strict):
    id: str = Field(min_length=1, max_length=120, pattern=r"^[\w.-]+$")
    parent: str | None = None
    name: str = Field(min_length=1, max_length=200)
    aggregate: Literal["sum", "max"] = "sum"
    cap: str | None = None
    floor: str | None = None
    weight: str = "1"
    negative_policy: Literal["signed", "separate"] = "signed"
    required: bool = False
    source: str = Field(default="", max_length=2000)

    @field_validator("cap", "floor", "weight")
    @classmethod
    def validate_decimal(cls, v):
        if v is not None: decimal(v)
        return v

class RuleSet(Strict):
    nodes: list[Node] = Field(min_length=1, max_length=10000)
    display_places: int = Field(default=4, ge=0, le=8)
    explanation: str = Field(default="", max_length=20000)
    @model_validator(mode="after")
    def valid_tree(self):
        from .rules import validate_tree
        validate_tree([n.model_dump() for n in self.nodes])
        return self

class StampInfo(Strict):
    # Unknown is deliberately distinct from no stamp. Old archives default to unknown.
    has_stamp: StrictBool | None = None
    stamp_text: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def stamp_consistency(self):
        if self.stamp_text and self.has_stamp is not True:
            raise ValueError("填写盖章文字前，请将是否有盖章设为‘是’；无法判定时不要推测印章文字。")
        return self

class Annotation(StampInfo):
    status: Status
    material_date: str | None = Field(default=None, max_length=32)
    date_override_reason: str | None = Field(default=None, max_length=2000)
    description: str = Field(default="", max_length=5000)
    category_id: str | None = None
    rule_item: str = Field(default="", max_length=4000)
    scenario: str = Field(default="", max_length=4000)
    rule_source: str = Field(default="", max_length=2000)
    score: str = "0"
    base_score: str | None = None
    factors: list[str] = Field(default_factory=list, max_length=10)
    denominator: str = "1"
    ai_note: str | None = Field(default=None, max_length=10000)
    human_note: str | None = Field(default=None, max_length=10000)
    student_note: str | None = Field(default=None, max_length=4000)
    outcome_key: str | None = Field(default=None, max_length=250)
    support_for: str | None = None
    duplicate_of: str | None = None
    replaces: str | None = None
    @field_validator("score", "base_score", "denominator")
    @classmethod
    def dec(cls, v):
        if v is not None: decimal(v)
        return v
    @model_validator(mode="after")
    def consistent(self):
        if self.status == "valid" and not self.support_for and not self.duplicate_of:
            if not self.category_id or not self.rule_item or not self.scenario or not self.rule_source:
                raise ValueError("有效计分材料必须填写完整分类、细则加分项、情形和规则出处。")
        if self.status != "valid" and decimal(self.score) != 0:
            raise ValueError("非有效状态的计入分必须为 0；拟评分请写入备注。")
        if (self.support_for or self.duplicate_of) and decimal(self.score) != 0:
            raise ValueError("佐证/重复材料不得独立计分。")
        if sum(bool(v) for v in [self.support_for, self.duplicate_of, self.replaces]) > 1:
            raise ValueError("材料关联类型只能选择一种。")
        if decimal(self.denominator) <= 0:
            raise ValueError("共同作者/人数分母必须为正。")
        for f in self.factors:
            if decimal(f) < 0: raise ValueError("系数必须非负，扣分请用负的基础分。")
        if self.base_score is not None:
            from .rules import local_score
            if abs(decimal(self.score) - local_score(self.base_score, self.factors, self.denominator)) > Decimal("0.00000001"):
                raise ValueError("局部分值与基础分 × 系数 ÷ 分母不一致。")
        return self

class AnnotationWrite(Strict):
    revision: int = Field(ge=1)
    expected_annotation_version: int = Field(ge=0)
    annotation: Annotation
    idempotency_key: str = Field(min_length=8, max_length=100)

class PublicRow(StampInfo):
    id: str = Field(min_length=1, max_length=100)
    student_id: str
    category_id: str
    score: str
    description: str = Field(min_length=1, max_length=3000)
    scenario: str = Field(default="", max_length=3000)
    rule_source: str = Field(default="", max_length=2000)
    material_date: str | None = Field(default=None, max_length=32)
    note: str = Field(default="", max_length=5000)
    status: Literal["valid", "manual", "out_of_scope", "out_of_period"] = "valid"
    outcome_key: str | None = Field(default=None, max_length=250)
    base_score: str | None = None
    factors: list[str] = Field(default_factory=list, max_length=10)
    denominator: str = "1"
    @model_validator(mode="after")
    def valid_score(self):
        d = decimal(self.score)
        if decimal(self.denominator)<=0:raise ValueError("分母必须大于0。")
        if any(decimal(f)<0 for f in self.factors):raise ValueError("系数不得为负数。")
        if self.status != "valid" and d != 0: raise ValueError("待认定/无效名单行必须是 0 分。")
        if self.status == "valid" and (not self.scenario or not self.rule_source):
            raise ValueError("有效名单行必须有明确情形和规则出处。")
        if self.base_score is not None:
            from .rules import local_score
            if abs(d - local_score(self.base_score, self.factors, self.denominator)) > Decimal("0.00000001"):
                raise ValueError("名单行的基础分与系数计算结果不一致。")
        return self

class PublicBody(StampInfo):
    name_at_version: str | None = Field(default=None, max_length=200)
    resource_sha: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_name: str = Field(min_length=1, max_length=250)
    description: str = Field(default="", max_length=5000)
    note: str = Field(default="", max_length=10000)
    rows: list[PublicRow] = Field(max_length=20000)
    approved: bool = False
    @model_validator(mode="after")
    def unique(self):
        ids = [r.id for r in self.rows]
        if len(set(ids)) != len(ids): raise ValueError("名单行标识重复。")
        return self

class PublicWrite(Strict):
    revision: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=200)
    body: PublicBody

class ProjectCreate(Strict):
    test_only: bool = False
    title: str = Field(min_length=1, max_length=200)
    year: str = Field(min_length=1, max_length=40)
    class_name: str = Field(min_length=1, max_length=100)
    responsible: str = Field(default="", max_length=200)
    folder_id: str | None = None
    student_ids: list[str] = Field(default_factory=list, max_length=2000)
    assessment_start: date
    assessment_end: date
    @model_validator(mode="after")
    def time(self):
        if self.assessment_end < self.assessment_start: raise ValueError("参评时间区间倒置。")
        if len(set(self.student_ids)) != len(self.student_ids): raise ValueError("学生重复。")
        return self

class ScheduleWrite(Strict):
    revision: int = Field(ge=1)
    opens_at: datetime
    deadline_at: datetime
    appeal_deadline: datetime
    @model_validator(mode="after")
    def times(self):
        for t in [self.opens_at, self.deadline_at, self.appeal_deadline]:
            if not t.tzinfo: raise ValueError("所有时刻必须带时区。")
        if not self.opens_at < self.deadline_at <= self.appeal_deadline:
            raise ValueError("必须满足开放 < 截止 ≤ 申诉截止。")
        return self

class Revision(Strict):
    revision: int = Field(ge=1)

class RuleWrite(Revision):
    rules: RuleSet
    reason: str = Field(min_length=1, max_length=2000)

class InstructionsWrite(Revision):
    text: str = Field(max_length=100000)
    reason: str = Field(min_length=1, max_length=2000)

class MembersWrite(Revision):
    student_ids: list[str] = Field(max_length=2000)
    reviewer_ids: list[str] = Field(default_factory=list, max_length=100)

class Transition(Revision):
    state: Literal["submission", "review", "closed", "archived"]
    reason: str = Field(min_length=1, max_length=2000)

class Publish(Revision):
    label: str = Field(min_length=1, max_length=200)
    mode: Literal["provisional", "final"] = "provisional"

class BindingWrite(Revision):
    list_id: str
    list_revision: int = Field(ge=1)

class AppealCreate(Strict):
    evidence_id: str | None = None
    reason: Literal["missing_evidence", "unclear_image", "wrong_classification", "wrong_score", "system_failure", "other"]

class AppealDecision(Revision):
    decision: Literal["approved", "rejected"]
    note: str = Field(min_length=1, max_length=4000)
    until: datetime | None = None

class TokenCreate(Strict):
    project_id: str
    name: str = Field(min_length=1, max_length=100)
    hours: int = Field(default=24, ge=1, le=168)
    scopes: list[Literal["context:read", "evidence:read", "annotation:write", "public:write"]] = ["context:read", "evidence:read", "annotation:write"]

class Login(Strict):
    username: str = Field(min_length=1, max_length=160)
    password: str = Field(min_length=1, max_length=128)
    portal: Literal["student", "teacher", "admin"]
    totp: str = Field(default="", max_length=8)

class PasswordChange(Strict):
    old_password: str = Field(max_length=128)
    new_password: str = Field(min_length=12, max_length=128)

class CollegeCreate(Strict):
    name: str = Field(min_length=1, max_length=200)

class TeacherCreate(Strict):
    username: str = Field(min_length=3, max_length=160)
    name: str = Field(min_length=1, max_length=100)
    college_id: str

class StudentInput(Strict):
    number: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=100)
    class_name: str = Field(min_length=1, max_length=100)

class RosterImport(Strict):
    rows: list[StudentInput] = Field(min_length=1, max_length=2000)

class FolderWrite(Strict):
    name: str = Field(min_length=1, max_length=100)
    parent_id: str | None = None
    trashed: bool = False

class FileAction(Revision):
    action: Literal["rename", "move", "trash", "restore", "copy"]
    title: str | None = Field(default=None, max_length=200)
    folder_id: str | None = None

class GradeCourse(Strict):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    credits: str
    scheme: Literal["numeric", "five", "two"] = "numeric"
    @field_validator("credits")
    @classmethod
    def credit(cls,v):
        if decimal(v) <= 0: raise ValueError("学分必须大于 0。")
        return v

class GradeEntry(Strict):
    course_id: str
    score: str | None = None
    excluded: bool = False
    exclusion_reason: str = Field(default="", max_length=2000)
    substitute_name: str | None = Field(default=None, max_length=200)
    substitution_reason: str = Field(default="", max_length=2000)
    @model_validator(mode="after")
    def reason(self):
        if self.excluded and not self.exclusion_reason: raise ValueError("免计入课程必须说明依据。")
        if self.substitute_name and not self.substitution_reason: raise ValueError("替代课程必须说明认定依据。")
        return self

class StudentGrades(Strict):
    student_id: str
    entries: list[GradeEntry]

class AcademicWrite(Revision):
    resource_sha: str = Field(pattern=r"^[a-f0-9]{64}$")
    courses: list[GradeCourse] = Field(min_length=1, max_length=200)
    students: list[StudentGrades] = Field(max_length=2000)
    confirmed: bool = False
    note: str = Field(min_length=1, max_length=5000)

_DECIMAL = re.compile(r"^-?(?:\d{1,8})(?:\.\d{1,12})?$")
def decimal(value: str | int | Decimal) -> Decimal:
    if isinstance(value, Decimal):
        d = value
    else:
        s = str(value)
        if not _DECIMAL.fullmatch(s):
            raise ValueError("分数必须是有限十进制数（最多 8 位整数、12 位小数），不接受 NaN/Infinity/科学计数法。")
        d = Decimal(s)
    if not d.is_finite() or abs(d) > Decimal("99999999"):
        raise ValueError("分值超出安全范围。")
    return d
