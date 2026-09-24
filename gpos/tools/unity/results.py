"""Unity Test Framework results and Editor-log classification (Phase 2C-5).

Pure functions over files the execution wrote into its own workspace. They start no process.

`read_results` accepts only a complete, bounded NUnit 3 `test-run` document whose counts agree with each
other and with its `test-case` elements; a document type declaration or entity declaration is refused
before parsing. It never decides PASS: the adapter does, and only from a document that executed at
least one test.

`classify_log` names why a run produced no results, from Unity's own log text, or says it cannot tell.
An exit code alone is never interpreted: exit 1 has been observed for a script compilation failure,
for a second instance refused on an already-open project and for a disabled Package Manager alike.
"""

import re
import xml.etree.ElementTree as ET

MAX_RESULTS = 64 * 1024 * 1024
MAX_LOG_TAIL = 4 * 1024 * 1024
RESULT = re.compile(r"(Passed|Failed|Skipped|Inconclusive|Warning)(\([A-Za-z]+\))?")
COUNTS = ("total", "passed", "failed", "skipped", "inconclusive")

COMPILE_ERROR = "COMPILE_ERROR"
LICENSE_UNAVAILABLE = "LICENSE_UNAVAILABLE"
PROJECT_LOCKED = "PROJECT_LOCKED"
UNCLASSIFIED = "UNCLASSIFIED"
SIGNATURES = (
    (LICENSE_UNAVAILABLE, ("No valid Unity Editor license found",)),
    (PROJECT_LOCKED, ("another Unity instance is running with this project open",
                      "Multiple Unity instances cannot open the same project")),
    (COMPILE_ERROR, ("Scripts have compiler errors.",)),
)


class ResultsProblem(ValueError):
    """The results file is not one complete, consistent NUnit test-run document."""


def read_results(path):
    """{total, passed, failed, skipped, inconclusive, result, test_cases} from the results file."""
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_RESULTS + 1)
    except OSError:
        raise ResultsProblem("the results file could not be read") from None
    if len(data) > MAX_RESULTS:
        raise ResultsProblem(f"the results file is larger than {MAX_RESULTS} bytes")
    lowered = data.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise ResultsProblem("the results file declares a document type or entities")
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise ResultsProblem("the results file is not complete, well-formed XML") from None
    if root.tag != "test-run":
        raise ResultsProblem("the results file is not an NUnit test-run document")
    counts = {}
    for name in COUNTS:
        raw = root.attrib.get(name, "0" if name == "inconclusive" else None)
        if raw is None or not re.fullmatch(r"[0-9]{1,9}", raw):
            raise ResultsProblem(f"the test-run {name} count is missing or malformed")
        counts[name] = int(raw)
    if counts["passed"] + counts["failed"] + counts["skipped"] + counts["inconclusive"] != counts["total"]:
        raise ResultsProblem("the test-run counts do not add up to its total")
    cases = sum(1 for _ in root.iter("test-case"))
    if cases != counts["total"]:
        raise ResultsProblem("the test-run total does not match its test cases")
    result = root.attrib.get("result", "")
    if not RESULT.fullmatch(result):
        raise ResultsProblem("the test-run result is missing or unknown")
    return dict(counts, result=result, test_cases=cases)


def classify_log(*texts):
    """The first known cause among the given log texts, or UNCLASSIFIED."""
    joined = "\n".join(t for t in texts if t)
    for cause, needles in SIGNATURES:
        if any(n in joined for n in needles):
            return cause
    return UNCLASSIFIED


def log_tail(path):
    """The last MAX_LOG_TAIL bytes of a log as text ("" when absent)."""
    try:
        with open(path, "rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - MAX_LOG_TAIL))
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
