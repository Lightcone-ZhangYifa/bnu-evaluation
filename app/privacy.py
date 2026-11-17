"""One allowlist for the student API, spreadsheet and PDF publication views.

Internal notes are not a public explanation. Teachers can explicitly fill the
separate student_note field; it becomes visible only in a published snapshot.
Future annotation fields stay private until added to this boundary deliberately.
"""
from copy import deepcopy

ANNOTATION_PUBLIC = frozenset('status material_date description category_id rule_item scenario rule_source score base_score factors denominator support_for duplicate_of replaces has_stamp stamp_text student_note'.split())
MATERIAL_PUBLIC = frozenset('id resource_sha submitted_at annotation_version reviewed excluded_reason'.split())
PUBLIC_ROW_PUBLIC = frozenset('id student_id category_id score description scenario rule_source material_date status base_score factors denominator list_id list_name list_revision has_stamp stamp_text'.split())
RESULT_PUBLIC = frozenset('root total display_total complete missing_categories missing_required ready nodes inputs issues academic'.split())


def pick(value, keys):
    return {key: deepcopy(item) for key,item in value.items() if key in keys}


def personal_projection(result):
    out = pick(result, RESULT_PUBLIC)
    out['materials'] = []
    for m in result.get('materials', []):
        safe = pick(m, MATERIAL_PUBLIC)
        safe['annotation'] = pick(m.get('annotation', {}), ANNOTATION_PUBLIC)
        out['materials'].append(safe)
    out['public_rows'] = [pick(r, PUBLIC_ROW_PUBLIC) for r in result.get('public_rows', [])]
    return out
