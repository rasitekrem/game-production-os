"""Decision references in every record type (GOVERNANCE §12.8, 11, 13, 25).

Every field registered in registry `decision_ref_fields` is resolved generically:
existence, ACTIVE status, allowed kind, decider authorization, subject rules and the
registry `decision_value_bindings`. A well-formed id is never authority on its own.
"""

from .. import diagnostics as dg


def get_path(doc, path, default=None):
    """Value at an absolute slash path such as /scope/revision (no wildcards)."""
    node = doc
    for part in path.strip("/").split("/"):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def ref_sites(doc, pointer):
    """(container, ref, concrete JSON pointer) for a registry field path with optional `*` segments."""
    parts = pointer.lstrip("/").split("/")
    sites = [(doc, "")]
    for part in parts[:-1]:
        nxt = []
        for node, where in sites:
            if part == "*" and isinstance(node, list):
                nxt.extend((x, f"{where}/{i}") for i, x in enumerate(node) if isinstance(x, dict))
            elif isinstance(node, dict) and isinstance(node.get(part), (dict, list)):
                nxt.append((node[part], f"{where}/{part}"))
        sites = nxt
    last = parts[-1]
    return [(c, c[last], f"{where}/{last}") for c, where in sites if isinstance(c, dict) and isinstance(c.get(last), str)]


def authorities(config):
    return {a["id"]: set(a["may_decide"]) for a in config["decision_authorities"]}


def resolve_decision_refs(fw, schema_name, record_type, record_id, doc, decisions_by_id, config):
    """Diagnostics for every decision reference registered for `schema_name` in `doc`."""
    out = []
    reg = fw.registry
    auth = authorities(config)
    project_id = config["project"]["id"]
    for key, kinds in reg["decision_ref_fields"].items():
        name, pointer = key.split(":", 1)
        if name != schema_name:
            continue
        for container, ref, path in ref_sites(doc, pointer):
            def d(code, message, details=None):
                out.append(dg.make(code, f"{path}: {message}", record_type=record_type, record_id=record_id, path=path,
                                   related=[ref], details=details))

            dec = decisions_by_id.get(ref)
            if dec is None:
                d("DECISION_REF_NOT_FOUND", f"{ref} does not resolve to a Human Decision record")
                continue
            if dec["status"] != "ACTIVE":
                d("DECISION_NOT_ACTIVE", f"{ref} is not ACTIVE ({dec['status']})")
            if dec["kind"] not in kinds:
                d("DECISION_KIND_MISMATCH", f"{ref} has kind {dec['kind']}, expected {kinds}")
            allowed = auth.get(dec["decided_by"]["id"])
            if allowed is None or not ({"ALL", dec["kind"]} & allowed):
                d("UNAUTHORIZED_DECIDER", f"{ref} decided by {dec['decided_by']['id']}, who is not authorized for {dec['kind']}")
            rule = reg["decision_subject_rules"].get(dec["kind"])
            if rule and isinstance(rule["subject_kinds"], list):
                if dec["subject"]["kind"] not in rule["subject_kinds"]:
                    d("DECISION_SUBJECT_MISMATCH", f"{ref} subject kind {dec['subject']['kind']} is not {rule['subject_kinds']}")
                elif rule.get("subject_ref") == "PROJECT_ID" and dec["subject"]["ref"] != project_id:
                    d("DECISION_SUBJECT_MISMATCH", f"{ref} subject {dec['subject']['ref']} is not this project ({project_id})")
            for b in reg["decision_value_bindings"]:
                if b["ref"] != key:
                    continue
                if b["config"].startswith("/"):
                    cv = get_path(doc, b["config"])
                else:
                    cv = container.get(b["config"], b.get("config_default"))
                dv = get_path(dec, b["decision"])
                if b.get("optional") and cv is None and dv is None:
                    continue
                if b.get("decision_may_omit") and dv is None:
                    continue
                if cv != dv:
                    d("DECISION_VALUE_MISMATCH", f"{b['config']}={cv!r} does not match decision {ref} {b['decision']}={dv!r}; "
                      f"a decision of the right kind but a different value or subject does not authorize it",
                      details={"field": b["config"], "record_value": cv, "decision_value": dv})
    return out
