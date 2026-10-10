"""Bounded Windows production Build Core through the fixed request/lease/process foundation."""
import os
from pathlib import Path
from .. import diagnostics as dg, paths as tp, process as proc
from ..execution import AdapterOutcome
from ..artifacts import ArtifactSpec
from . import build as ub, build_win32 as wb, bridge_install as bi, live, project_lock as pl, results as ur
from .adapter import (ADAPTER_ID, _refuse, _locked, _command, _with_lock_state, _quarantine,
                      upm_environment, UPM_CACHE, UPM_USER_NAME, UPM_GLOBAL_NAME, ADAPTER_VERSION)

LIMITATIONS = (
    "Windows Git provenance is available in alpha.28 within its qualified bounded subset. "
    "Unity Build Core does not invoke Git or verify a revision: build_revision remains CALLER_SUPPLIED. "
    "A qualified source/build relationship requires separate pre/post Git observations. "
    "This operational manifest is not authenticated Git attestation; no git_verified claim.",
    "Operational build facts only; no Player execution, runtime, visual, performance, test or Human evidence.",
    "Project Editor code and build callbacks ran. Configuration tokens are pre/post observations, not a repository lock.",
    "NTFS manifest-last no-overwrite publication; no power-loss durability or byte-reproducibility claim.",
    "The consumer must revalidate full payload hashes and NTFS identities before use; this is a local workspace contract.",
)

def run(adapter, cap, request, context, root, project, summary):
    building = cap == ub.BUILD
    rid = request.request_id
    token = (request.inputs or {}).get("expected_configuration_token")
    if request.build_id is not None:
        return _refuse(cap, f"{cap} takes no build_id: a build's id is always build-<request id>, so a supplied one is "
                            f"meaningless for creation")
    if request.target_platform not in (None, "WINDOWS"):   # provenance only, never a build input
        return _refuse(cap, f"{cap} is Windows-only; target_platform must be absent or WINDOWS, not "
                            f"{request.target_platform!r}")
    if building:
        if not ub.BUILD_REQUEST_ID.fullmatch(rid):
            return _refuse(cap, f"request id {rid!r} cannot name a build: build-<request id> needs lower-case letters, "
                                f"digits and inner hyphens only, at most 64 characters")
        if not (isinstance(request.build_revision, str) and ub.REVISION.fullmatch(request.build_revision)):
            return _refuse(cap, "a build requires request.build_revision: an exact caller-supplied 40- or 64-hex "
                                "revision; HEAD is never inferred and Unity Build Core does not invoke Git or verify it")
        if not (isinstance(token, str) and ub.TOKEN.fullmatch(token)):
            return _refuse(cap, "expected_configuration_token must be the 64-hex token an inspection returned")
    required, installed = summary["editor_version"], context.probe.tool_version
    if required != installed:   # the exact version, as for a test run
        return AdapterOutcome(ok=True, diagnostics=(dg.make(
            "ENGINE_EDITOR_VERSION_UNAVAILABLE",
            f"the project requires Unity {required}; the installed Editor is {installed}; no other version, "
            f"upgrade or downgrade is used", ADAPTER_ID, cap),))
    if installed != "6000.6.4f1":
        return _refuse(cap, "Windows build qualified for Unity 6000.6.4f1 only")
    entry, _ = bi.inspect_target(project, bi.load_manifest())
    if entry != bi.EXACT:
        return AdapterOutcome(ok=True, data={"build_entry_package": entry}, diagnostics=(dg.make(
            "BUILD_ENTRY_UNAVAILABLE", f"Packages/{bi.PACKAGE_ID} is {entry}, not exactly {bi.PACKAGE_ID} "
                                       f"{bi.BRIDGE_VERSION}, so the fixed build entry is not available; install or "
                                       f"upgrade it with {live.INSTALL} on the closed project", ADAPTER_ID, cap),))
    workspace = Path(context.workspace)   # the build root
    proof = adapter._lock_proof(project, context.probe.tool_path)
    if proof.state not in pl.PROCEED:   # before the workspace is used
        return _locked(cap, proof)
    if context.dry_run:
        what = f"build {summary['unity_project']!r} as {ub.build_id(rid)}" if building else \
            f"inspect the build configuration of {summary['unity_project']!r}"
        return AdapterOutcome(
            plan=(f"would {what} with Unity {installed} in a fresh batch-mode Editor running the fixed GPOS build "
                  f"entry {ub.BUILD_ENTRY_METHOD}",
                  "the configuration, its buildability, the build result and the payload are only established by a "
                  "real execution",
                  "no Unity process, workspace, package cache, build output or manifest is created by a dry run"),
            data=dict(summary, project_lock=proof.state, **({"build_id": ub.build_id(rid)} if building else {})))
    with wb.pinned_directory(workspace):
        present = ub.unfresh(workspace)
        if present:
            return AdapterOutcome(ok=True, diagnostics=(dg.make(
                "BUILD_WORKSPACE_NOT_FRESH", f"the execution workspace of request {rid} already holds {present[:6]}; an "
                                             f"earlier build is never adopted, reused or overwritten", ADAPTER_ID, cap),))
        cache = tp.runtime_dir(root, *UPM_CACHE)
        cache.mkdir(parents=True, exist_ok=True)
        for name in (UPM_USER_NAME, UPM_GLOBAL_NAME):   # GPOS-owned, empty: nothing is inherited from the user
            with open(workspace / name, mode="x", encoding="utf-8"):
                pass
        ub.write_request(workspace, "BUILD" if building else "INSPECT", rid, token if building else None)
        spec = proc.ToolProcessSpec(executable=context.probe.tool_path, argv=ub.build_argv(project, workspace),
                                    cwd=str(workspace), timeout=context.timeout, env=upm_environment(workspace, cache),
                                    capture_bytes=adapter._capture_bytes)
        proof = adapter._lock_proof(project, context.probe.tool_path)   # again, immediately before the launch
        if proof.state not in pl.PROCEED:   # a writer appeared meanwhile
            return _locked(cap, proof)
        outcome = context.run(spec)
        record = dict(command=_command(spec, project, workspace), environment=spec.env.metadata())
        result = adapter._classify_build(cap, request, context, outcome, record, workspace, summary)
        return _with_lock_state(cap, proof, result)


def classify(adapter, cap, request, context, outcome, record, workspace, summary):
    building, rid = cap == ub.BUILD, request.request_id
    log_path = workspace / ub.LOG_NAME
    log = tuple(ArtifactSpec("editor-log", "LOG", str(log_path), media_type="text/plain",
                             description="Unity Editor log of this run (machine and session identifiers, local paths)")
                for _ in (0,) if log_path.is_file())
    data = dict(unity_project=summary["unity_project"], editor_version=summary["editor_version"],
                exit_code=outcome.exit_code, **({"build_id": ub.build_id(rid)} if building else {}))

    def done(*diagnostics, ok=True, artifacts=log, detail=""):
        return AdapterOutcome(ok=ok, exit_code=outcome.exit_code, data=data, diagnostics=diagnostics, detail=detail,
                              artifacts=artifacts, process=outcome, mutation_performed=True, **record)

    def diag(code, message, **details):
        return dg.make(code, message, ADAPTER_ID, cap, details=details or None)

    def unknown(why):
        return done(diag("BUILD_OUTCOME_UNKNOWN", f"{why}; no completed build is accepted and nothing is retried"),
                    *_quarantine(cap, workspace))

    if not outcome.integrity_ok:
        return unknown("process containment or capture was not proven")
    began = building and ub.started(workspace)
    response, problem = None, "the Editor did not exit normally"
    if not outcome.timed_out and outcome.exit_code == 0:
        try:
            response = ub.read_response(workspace, rid, "BUILD" if building else "INSPECT")
        except (ub.ResponseProblem, OSError) as exc:
            problem = str(exc)
    if response is None:
        if began:
            return unknown(f"the build started but no trustworthy final response exists ({problem})")
        if outcome.timed_out:
            return done(ok=False, detail=f"{cap} timed out before any build started")
        cause = ur.classify_log(ur.log_tail(log_path), outcome.stdout)   # pre-entry failures only
        if cause == ur.COMPILE_ERROR:
            return done(diag("BUILD_COMPILE_FAILED", "script compilation failed while the batch Editor opened the "
                                                     "project, before the fixed build entry could run"))
        if cause == ur.PROJECT_LOCKED:
            return done(diag("ENGINE_PROJECT_LOCKED", "Unity refused the project because another instance has it open"))
        if cause == ur.LICENSE_UNAVAILABLE:
            return done(diag("ENGINE_LICENSE_UNAVAILABLE", "Unity reported that no valid Editor licence was "
                                                           "available; no licence action is ever taken"))
        return done(diag("BUILD_ENTRY_FAILED", f"the fixed build entry gave no trustworthy answer (exit "
                                               f"{outcome.exit_code}; {problem}) and never started a build"))
    conf = response["configuration"]
    profile = conf["profile"]
    bases = ub.path_bases(Path(context.project_root) / summary["unity_project"], context.project_root)
    clean = lambda text, bound: ub.clean_message(text, bases, bound)   # every text the entry or Unity wrote
    problems = [{"rule": p["rule"], "message": clean(p["message"], ub.MAX_PROBLEM_CHARS)} for p in response["problems"]]
    data.update(buildable=response["buildable"], problems=problems,
                configuration_token=response["configuration_token"], configuration=conf,
                unity_version=response["unity_version"], active_target=conf["active_target"],
                mode=conf["mode"], development=conf["development"])
    if not building:
        if response["buildable"]:
            return done()
        rules = [p["rule"] for p in response["problems"]]
        return done(diag("BUILD_CONFIGURATION_NOT_BUILDABLE", f"the configuration is not buildable by this release: "
                                                              f"{', '.join(rules)}", rules=rules))
    if response["outcome"] == "REFUSED":
        if began:
            return unknown("the build entry both refused and recorded a started build")
        rule, message = response["refusal"]["rule"], clean(response["refusal"]["message"], ub.MAX_PROBLEM_CHARS)
        return done(diag(ub.RULE_CODES.get(rule, "BUILD_CONFIGURATION_UNSUPPORTED"), f"{rule}: {message}", rule=rule))
    b, post = response["build"], response["post"]
    if not began or b is None:
        return unknown("the build entry reported a build without a started marker or without a BuildReport")
    # outputPath is used below only to verify the exact staging path; it never leaves GPOS
    data["build"] = {k: b[k] for k in ("result", "guid", "total_errors", "total_warnings", "total_size",
                                       "development_observed", "error_message_count")}
    data["build"]["messages"] = [{"step": clean(m["step"], ub.MAX_STEP_CHARS), "type": m["type"],
                                  "text": clean(m["text"], ub.MAX_MESSAGE_CHARS)} for m in b["messages"]]
    data["build"]["duration_seconds"] = round(b["duration_ms"] / 1000.0, 3)
    if b["result"] != "Succeeded" or b["total_errors"] != 0:
        return done(diag("BUILD_FAILED", f"Unity reported result {b['result']} with {b['total_errors']} error(s)",
                         result=b["result"]), *_quarantine(cap, workspace))
    staging_app = workspace / ub.STAGING / wb.APP
    mismatched = [name for name, holds in (
        ("the inspected configuration token", response["configuration_token"] == request.inputs[
            "expected_configuration_token"]),
        ("the post-build configuration token", post["configuration_token"] == response["configuration_token"]),
        ("the active target", post["active_target"] == conf["active_target"] == b["platform"] == wb.TARGET),
        ("the active Build Profile", post["profile_path"] == (profile["path"] if profile else None)),
        ("the development state", post["development"] is conf["development"] is b["development_observed"]),
        ("the Unity build GUID", bool(ub.GUID.fullmatch(b["guid"])) and b["guid"] != "0" * 32),
        ("the output path", os.path.realpath(b["output_path"]) == os.path.realpath(staging_app / wb.EXE)),
    ) if not holds]
    if mismatched:
        return unknown(f"the post-build checks failed ({'; '.join(mismatched)})")
    try:
        started_at = wb.started_utc(workspace, rid)
        problem = wb.configuration_problem(conf)
        if problem:
            raise wb.PayloadProblem(problem)
        names = sorted(os.listdir(workspace / ub.STAGING))
        if names != [wb.APP]:
            raise wb.PayloadProblem(f"staging/ holds {names[:6]}, not exactly {wb.APP}")
        app, tree = wb.validate_player(staging_app, b["guid"], b["files"])
        if b["total_size"] != tree["bytes"]:
            raise wb.PayloadProblem("BuildReport total size differs from its complete payload files")
    except wb.VALIDATION_ERRORS as exc:
        return done(diag("BUILD_PAYLOAD_INVALID", f"the payload is not published: {exc}"), *_quarantine(cap, workspace))
    try:
        wb.publish(workspace)
    except wb.VALIDATION_ERRORS as exc:
        return unknown(f"the payload could not be committed ({type(exc).__name__})")
    manifest = windows_manifest(request, response, app, tree, started_at, context.clock.now())
    try:
        wb.write_manifest(workspace, manifest)
    except wb.VALIDATION_ERRORS as exc:
        return unknown(f"the payload was committed but its manifest could not be written ({type(exc).__name__}), so "
                       f"this is not a completed build")
    _, problem = wb.revalidate(workspace)
    if problem:
        return unknown(problem)
    data.update(payload={k: manifest["payload"][k] for k in manifest["payload"]}, manifest=ub.MANIFEST_NAME,
                build_revision=request.build_revision, build_revision_source="CALLER_SUPPLIED")
    artifacts = (ArtifactSpec("build-manifest", "REPORT", str(workspace / ub.MANIFEST_NAME),
                              media_type="application/json",
                              description="GPOS build manifest: binds the workspace payload by its tree digest"),) + log
    return done(diag("BUILD_PUBLISHED", f"{manifest['build_id']}: {ub.PAYLOAD}/{wb.APP} ({tree['entries']} entries, "
                                        f"{tree['bytes']} bytes) committed; {ub.MANIFEST_NAME} written last"),
                artifacts=artifacts)


def windows_manifest(request, response, app, tree, started_at, built_at):
    import gpos
    conf, b = response["configuration"], response["build"]
    profile = conf["profile"]
    return {
        "schema": wb.MANIFEST_SCHEMA,
        "build_id": ub.build_id(request.request_id),
        "request_id": request.request_id,
        "capability": ub.BUILD,
        "adapter": {"id": ADAPTER_ID, "version": ADAPTER_VERSION},
        "gpos_version": gpos.__version__,
        "subject": {"kind": request.subject.kind, "ref": request.subject.ref},
        "build_revision": request.build_revision,
        "build_revision_source": "CALLER_SUPPLIED",
        "configuration_token": response["configuration_token"],
        "configuration": conf,
        "unity_version": response["unity_version"],
        "target": wb.TARGET,
        "development": conf["development"],
        "configuration_mode": conf["mode"],
        "build_profile": None if not profile else {k: profile[k] for k in ("path", "guid", "sha256")},
        "scenes": conf["scenes"],
        "unity_build": {"guid": b["guid"], "result": b["result"], "total_errors": b["total_errors"],
                        "total_warnings": b["total_warnings"], "total_size": b["total_size"],
                        "development_observed": b["development_observed"],
                        "duration_seconds": round(b["duration_ms"] / 1000.0, 3)},
        "build_entry": {"package": bi.PACKAGE_ID, "version": bi.BRIDGE_VERSION, "method": ub.BUILD_ENTRY_METHOD,
                        "package_digest": bi.load_manifest()["package_digest"]},
        "payload": {"path": "payload/Player", "kind": "WINDOWS_STANDALONE_PLAYER",
                    "tree_algorithm": tree["algorithm"], "tree_digest": tree["digest"], "entries": tree["entries"],
                    "bytes": tree["bytes"], "root_identity": tree["root_identity"], "inventory": tree["inventory"], **app},
        "started_at": started_at,
        "built_at": built_at,
        "duration_seconds": round(b["duration_ms"] / 1000.0, 3),
        "limitations": list(LIMITATIONS),
    }
