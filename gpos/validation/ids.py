"""Identifier integrity (GOVERNANCE §12.15; registry record_id_uniqueness).

Duplicates are detected before any index is built. No lookup ever resolves an ambiguous
identifier, and nothing is resolved by last-write-wins.
"""

from .. import diagnostics as dg

# record type -> id field (record ids), and project-config lists whose `id` must be unique
RECORD_ID_FIELDS = (("decision", "decision_id"), ("routing", "task_id"), ("gate", "gate_id"), ("evidence", "evidence_id"))
CONFIG_ID_LISTS = (
    ("/decision_authorities", ("decision_authorities",)),
    ("/human_review/reviewers", ("human_review", "reviewers")),
    ("/human_review/additional_mandatory_triggers", ("human_review", "additional_mandatory_triggers")),
)


def duplicates(values):
    seen, dups = set(), set()
    for v in values:
        (dups if v in seen else seen).add(v)
    return sorted(dups, key=str)


def record_id_problems(records_by_type):
    """records_by_type: {record type: [Record]}. One diagnostic per duplicated id, naming every file."""
    out = []
    for record_type, field in RECORD_ID_FIELDS:
        records = records_by_type.get(record_type, [])
        for dup in duplicates([r.data[field] for r in records]):
            files = [r.file for r in records if r.data[field] == dup and r.file]
            out.append(dg.make("DUPLICATE_RECORD_ID", f"duplicate {field} {dup!r} ({len([r for r in records if r.data[field] == dup])} "
                               f"records); ids must be unique and are never resolved by last-write-wins",
                               record_type=record_type, record_id=dup, path=f"/{field}", related=files,
                               details={"files": files}))
    return out


def config_id_problems(config):
    out = []
    for pointer, keys in CONFIG_ID_LISTS:
        node = config
        for k in keys:
            node = node.get(k, {}) if isinstance(node, dict) else {}
        items = node if isinstance(node, list) else []
        for dup in duplicates([i["id"] for i in items]):
            out.append(dg.make("DUPLICATE_CONFIG_ID", f"duplicate id {dup!r} in {pointer}", record_type="project-config",
                               record_id=config["project"]["id"], path=pointer, related=[dup]))
    return out
