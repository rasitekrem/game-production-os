"""Target-platform and reference-device binding (GOVERNANCE §12.41–44)."""

TARGET_CONTEXTS = ("TARGET_RUNTIME", "PERFORMANCE_RUNTIME")


def target_platform_problem(ev, config):
    """(code, message) when target-runtime evidence cannot count for this project, else None.

    Applies to TARGET_RUNTIME / PERFORMANCE_RUNTIME evidence that names a target_platform:
    the platform must be declared in project config, and DEVICE_EVIDENCE must come from a
    declared reference device when the platform lists any.
    """
    prov = ev["provenance"]
    if prov["capture_context"] not in TARGET_CONTEXTS or "target_platform" not in prov:
        return None
    targets = {t["platform"]: t for t in config["target_platforms"]}
    platform = prov["target_platform"]
    if platform not in targets:
        return ("TARGET_PLATFORM_NOT_DECLARED",
                f"{ev['evidence_id']} is labelled {prov['capture_context']} on {platform}, which is not a declared project "
                f"target platform; it cannot count as target-runtime proof (record it as DIAGNOSTIC_RUNTIME)")
    refs = targets[platform].get("reference_devices")
    if ev["type"] == "DEVICE_EVIDENCE" and refs and prov.get("device") not in refs:
        return ("REFERENCE_DEVICE_MISMATCH",
                f"{ev['evidence_id']} was captured on {prov.get('device')!r}, not a declared {platform} reference device {refs}")
    return None


def primary_platforms(config):
    return [t["platform"] for t in config["target_platforms"] if t["tier"] == "PRIMARY"]


def covers_platform(ev, etype, platform):
    prov = ev["provenance"]
    return ev["type"] == etype and prov.get("target_platform") == platform and prov["capture_context"] in TARGET_CONTEXTS
