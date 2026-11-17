"""Pure deterministic rule engine. No I/O, eval, NLP, model or floating point.

Rules use an arbitrary-depth, flat adjacency list. Evaluation is iterative O(N+M),
not recursive. Inputs and calculation traces are never overwritten by a cap.
"""
from collections import defaultdict, deque
from decimal import Decimal, localcontext, ROUND_HALF_UP
from datetime import date
import calendar
from .schemas import decimal

D = Decimal

def text(d: Decimal) -> str:
    return format(d, "f").rstrip("0").rstrip(".") if "." in format(d, "f") else format(d, "f")

def local_score(base: str, factors: list[str] = (), denominator: str = "1") -> Decimal:
    with localcontext() as c:
        c.prec = 48
        value = decimal(base)
        den = decimal(denominator)
        if den <= 0: raise ValueError("分母必须大于零。")
        for factor in factors:
            f = decimal(factor)
            if f < 0: raise ValueError("系数必须非负。")
            value *= f
        return value / den

def validate_tree(nodes: list[dict]):
    by_id = {n["id"]: n for n in nodes}
    if len(by_id) != len(nodes): raise ValueError("分类 ID 重复。")
    if not nodes: raise ValueError("规则不能为空。")
    roots = [n for n in nodes if n.get("parent") is None]
    if len(roots) != 1: raise ValueError("分类树必须恰有一个根。")
    children = defaultdict(list)
    for node in nodes:
        parent = node.get("parent")
        if parent is not None:
            if parent not in by_id: raise ValueError("父分类不存在：" + str(parent))
            if parent == node["id"]: raise ValueError("分类不能作为自己的父级。")
            children[parent].append(node["id"])
        if node.get("aggregate", "sum") not in ("sum", "max"): raise ValueError("不支持的聚合方式。")
        cap = decimal(node["cap"]) if node.get("cap") is not None else None
        floor = decimal(node["floor"]) if node.get("floor") is not None else None
        if cap is not None and floor is not None and cap < floor: raise ValueError("上限小于下限。")
        if decimal(node.get("weight", "1")) < 0: raise ValueError("节点权重必须非负。")
    order, queue = [], deque([roots[0]["id"]])
    while queue:
        nid = queue.popleft()
        order.append(nid)
        queue.extend(children[nid])
    if len(order) != len(nodes): raise ValueError("存在循环或与根不相连的分类。")
    return by_id, children, order

def category_path(nodes: list[dict], target: str) -> list[str]:
    by_id, _, _ = validate_tree(nodes)
    if target not in by_id: raise ValueError("分类不存在。")
    path = []
    while target is not None:
        path.append(target)
        target = by_id[target].get("parent")
    return list(reversed(path))

def date_interval(value: str):
    parts = value.split("-")
    if len(parts) == 1:
        y = int(value); return date(y, 1, 1), date(y, 12, 31)
    if len(parts) == 2:
        y, m = map(int, parts)
        return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])
    if len(parts) == 3:
        d = date.fromisoformat(value); return d, d
    raise ValueError("日期只支持 YYYY、YYYY-MM、YYYY-MM-DD。")

def within_period(value: str | None, start: str, end: str) -> bool:
    if not value: return False
    try:
        lo, hi = date_interval(value)
        return date.fromisoformat(start) <= lo and hi <= date.fromisoformat(end)
    except (ValueError, TypeError): return False

def calculate(nodes: list[dict], items: list[dict], display_places=4) -> dict:
    by_id, children, order = validate_tree(nodes)
    direct = defaultdict(list)
    seen_ids = set()
    for item in items:
        if item["id"] in seen_ids: raise ValueError("重复计分输入 ID。")
        seen_ids.add(item["id"])
        nid = item["category_id"]
        if nid not in by_id: raise ValueError("未知分类：" + str(nid))
        if children[nid]: raise ValueError("计分只能落在叶子分类；不允许跳过下级规则。")
        value = Decimal(str(item["score"]))
        if not value.is_finite(): raise ValueError("非有限分数。")
        direct[nid].append((item["id"], value))
    results = {}
    with localcontext() as c:
        c.prec = 48
        for nid in reversed(order):
            node = by_id[nid]
            values = direct[nid] + [(key, results[key]["_value"]) for key in children[nid]]
            negatives = []
            aggregate_values = values
            if node.get("negative_policy", "signed") == "separate":
                aggregate_values = [(key, value) for key, value in values if value >= 0]
                negatives = [(key, value) for key, value in values if value < 0]
            agg = node.get("aggregate", "sum")
            all_sum = sum((v for _,v in values), D(0))
            if agg == "max":
                selected_value = max((v for _,v in aggregate_values), default=D(0))
                selected = sorted(key for key,v in aggregate_values if v == selected_value)
                # One tied maximum contributes once; all ties are recorded for audit.
                effective_ids = selected[:1]
            else:
                selected_value = sum((v for _,v in aggregate_values), D(0))
                selected = [key for key,_ in aggregate_values]
                effective_ids = selected
            weight = decimal(node.get("weight", "1"))
            weighted = selected_value * weight
            capped = weighted
            if node.get("cap") is not None: capped = min(capped, decimal(node["cap"]))
            if node.get("floor") is not None: capped = max(capped, decimal(node["floor"]))
            negative_amount = sum((v for _,v in negatives), D(0)) * weight
            value = capped + negative_amount
            missing = node.get("required", False) and not values
            results[nid] = {
                "id":nid, "name":node["name"], "aggregate":agg,
                "inputs":[{"id":key,"value":text(v)} for key,v in values],
                "raw_sum":text(all_sum), "selected_before_weight":text(selected_value),
                "selected_ids":effective_ids, "tied_ids":selected if agg == "max" else [],
                "weight":text(weight), "weighted_before_cap":text(weighted),
                "cap":node.get("cap"), "floor":node.get("floor"),
                "cap_delta":text(weighted-capped), "separate_deductions":text(negative_amount),
                "value":text(value), "missing_required":missing,
                "negative_policy":node.get("negative_policy","signed"), "_value":value,
            }
        total = results[order[0]]["_value"]
        display = format(total.quantize(D(1).scaleb(-display_places), rounding=ROUND_HALF_UP), "f")
    for r in results.values(): r.pop("_value")
    return {"root":order[0], "total":text(total), "display_total":display,
        "complete":not any(r["missing_required"] for r in results.values()),
        "missing_categories":[r["name"] for r in results.values() if r["missing_required"]],
        "nodes":[results[nid] for nid in order], "inputs":items}

def course_result(course: dict, entry: dict):
    """Validate and describe one saved grade, without requiring a whole transcript.

    The import compiler and average calculator share this interpretation. In
    particular, an excluded/two-level course is a valid row, not a zero-credit
    transcript error. The latter belongs to the final transcript calculation.
    """
    if course["scheme"] == "two" or entry.get("excluded"):
        return {"course": course["name"], "included": False,
                "reason": entry.get("exclusion_reason") or "两级制不计入"}
    raw = entry.get("score")
    five = {"优秀": "95", "良好": "85", "中等": "75", "合格": "65", "不合格": "0"}
    if raw in (None, ""):
        score, reason = D(0), "未获得有效成绩，按 0 分计入"
    elif course["scheme"] == "five":
        if raw not in five:
            raise ValueError("无效的五级制成绩。")
        score, reason = decimal(five[raw]), "五级制映射"
    else:
        score, reason = decimal(raw), "数值成绩"
    if not 0 <= score <= 100:
        raise ValueError("课程成绩应在 0—100 之间。")
    credit = decimal(course["credits"])
    with localcontext() as context:
        context.prec = 48
        weighted = score * credit
    return {"course": course["name"], "included": True, "score": text(score),
            "credits": text(credit), "weighted": text(weighted), "reason": reason,
            "substitute_name": entry.get("substitute_name"),
            "substitution_reason": entry.get("substitution_reason", "")}


def academic_average(courses: list[dict], entries: list[dict]):
    lookup = {entry["course_id"]: entry for entry in entries}
    if len(lookup) != len(entries):
        raise ValueError("一个学生同一课程只能有一条成绩。")
    if len({course['id'] for course in courses}) != len(courses):
        raise ValueError("课程编号重复。")
    if set(lookup) - {course["id"] for course in courses}:
        raise ValueError("成绩中存在未定义课程。")
    trace = [course_result(course, lookup.get(course["id"], {})) for course in courses]
    with localcontext() as context:
        context.prec = 48
        total_credits = sum((D(row["credits"]) for row in trace if row["included"]), D(0))
        weighted = sum((D(row["weighted"]) for row in trace if row["included"]), D(0))
        if not total_credits:
            raise ValueError("有效必修课程总学分为 0，不能计算专业加权平均成绩。")
        average = weighted / total_credits
    return {"average": text(average), "credits": text(total_credits),
            "weighted_sum": text(weighted), "courses": trace}


def bnu_template():
    def n(key,parent,name,agg="sum",cap=None,weight="1",required=False,source=""):
        return {"id":key,"parent":parent,"name":name,"aggregate":agg,"cap":cap,"floor":None,
            "weight":weight,"negative_policy":"signed","required":required,"source":source}
    nodes = [
        n("total",None,"综合测评成绩",cap="105",source="第2条；满分105，另行扣分依附件四"),
        n("moral","total","思想品德",cap="5",required=True,source="第3条：学院认定，不默认赋5分"),
        n("academic","total","学业成绩",cap="86",weight="0.86",required=True,source="第4条：专业加权平均成绩×86%"),
        n("innovation","total","学术创新成果",cap="7",source="第5条"),
        n("research","innovation","科研成果",source="附件二"),
        n("research.projects","research","项目成果","max",source="附件二（一）；同子类别取最高"),
        n("research.papers","research","论文成果","max",source="附件二（二）；同子类别取最高"),
        n("research.other","research","其他成果","max",source="附件二（三）；同子类别取最高"),
        n("competition","innovation","竞赛获奖",source="附件二"),
        n("competition.professional","competition","专业竞赛类","max",source="附件二（四）"),
        n("competition.creative","competition","创意策划类","max",source="附件二（五）"),
        n("competition.teaching","competition","教师素养类","max",source="附件二（六）"),
        n("certification","innovation","学科认证","max",source="附件二（三）学科认证：CSP当年、仅一次"),
        n("work","total","学生工作",cap="7",source="第6条"),
        n("positions","work","岗位任职","max",cap="3",source="附件四：所有岗位取最高，不累计"),
        n("activities","work","学生活动",cap="4",source="附件四"),
        n("activities.sports","activities","文体比赛",cap="2",source="附件四：运动会权重A1/B0.8/C0.6"),
        n("activities.large","activities","参与学院大型活动",cap="0.5",source="附件四"),
        n("activities.practice","activities","实践活动",cap="1.5",source="附件四"),
        n("activities.party","activities","党团班活动",cap="2",source="附件四"),
        n("activities.party.dorm","activities.party","文明宿舍",source="附件四：一等1/二等0.7/三等0.5"),
        n("activities.party.lectures","activities.party","学术系列活动",cap="1",source="附件四：每次0.1，上限1"),
        n("activities.party.class","activities.party","班级活动参与","max",cap="0.5",source="附件四：严格大于总数2/3"),
        n("activities.party.deductions","activities.party","党团活动缺勤扣分",source="附件四：不足总次数1/2减0.5；具体叠加须学院确认"),
        n("penalties","total","违规处罚",source="附件四：各处罚依据及是否同一行为需学院认定；禁止自动推断"),
    ]
    # Mandatory total-score penalties stay outside the positive total cap.
    nodes[0]["negative_policy"] = "separate"
    return {"nodes":nodes,"display_places":4,"explanation":
        "依据上传的《2026年9月版》配置。原始细则与解释文档另行存档。本模板不自动推断岗位、评价权重、任期、材料真实性。"
        "科研项目退出扣分与同子类别取最高的交互、党团扣分与封顶顺序及同一行为处罚叠加，均须学院书面明确后配置相应节点，不能由Agent擅自解释。"
        "岗位系数在局部标注中应用，全局只取最高。分数不默认下限0。显示四位小数，不做中途舍入。"}
