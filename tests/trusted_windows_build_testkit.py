"""TEST_ONLY qualification orchestration; no production provenance API or gate.

Callbacks invoke the existing Git/Unity implementations. A successful operational
manifest remains CALLER_SUPPLIED; this verdict is only a workflow observation.
"""
import re


def qualify(resolve, inspect, build, revalidate):
    record = {"qualified": False, "git_revisions": []}

    def observe(stage, expected=None):
        answer = resolve(stage)
        revision = (answer.data or {}).get("repository_revision")
        record[stage] = answer.to_dict()
        if answer.status != "SUCCESS" or not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", revision):
            record["refusal"] = stage
            return None
        record["git_revisions"].append(revision)
        if expected is not None and revision != expected:
            record["refusal"] = stage
            return None
        return revision

    revision = observe("git_before")
    if revision is None:
        return record
    inspection = inspect()
    record["inspection"] = inspection.to_dict()
    data = inspection.data or {}
    token = data.get("configuration_token")
    if inspection.status != "SUCCESS" or data.get("buildable") is not True or not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{64}", token):
        record["refusal"] = "inspection"
        return record
    if observe("git_between", revision) is None:
        return record
    built = build(token, revision)
    record["build"] = built.to_dict()
    if built.status != "SUCCESS" or "BUILD_PUBLISHED" not in {d.code for d in built.diagnostics}:
        record["refusal"] = "build"
        return record
    if observe("git_after", revision) is None:
        return record
    manifest, problem = revalidate(built)
    record["manifest_error"] = problem
    if problem is not None or not isinstance(manifest, dict):
        record["refusal"] = "manifest"
        return record
    if manifest.get("build_revision_source") != "CALLER_SUPPLIED" or manifest.get("build_revision") != revision or "git_verified" in manifest:
        record["refusal"] = "manifest_attribution"
        return record
    record.update(qualified=True, revision=revision, token=token, manifest=manifest)
    return record
