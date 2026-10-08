"""The baseline gate every mutation harness runs behind (alpha.23).

A mutation harness counts a mutant as CAUGHT when the suite fails in a copy of the repository that carries the
mutation. That only means something when the same suite passes in the same kind of copy without one: a suite
that is already red would "catch" every mutant. So every harness qualifies through `qualify`:

    every baseline PASS -> each mutant runs independently -> QUALIFIED (exit 0) or MISSED (exit 1)
    any baseline RED, crashed, timed out or NOT_RUN -> BLOCKED (exit 2); no mutant runs and none is counted CAUGHT

A baseline is the harness's own `run()` applied to a mutation with no edits, so it is prepared exactly like every
mutant: the same copy, the same preparation steps, the same interpreter flags and the same suite command. A
harness whose mutants target several suites passes one baseline per suite it is about to use.

Edits are applied byte for byte (UTF-8 in, UTF-8 out, no newline translation), so a harness running on Windows
never rewrites a mutated LF file with CRLF line endings.
"""

import concurrent.futures
import sys

QUALIFIED_EXIT, MISSED_EXIT, BLOCKED_EXIT = 0, 1, 2
BASELINE = "baseline (no mutation)"

# The interpreter a harness runs its suite with. On Windows the suites need Python's UTF-8 Mode, and -X utf8 is not
# inherited by a child interpreter, so it is passed explicitly; elsewhere the command is unchanged.
PYTHON = [sys.executable] + (["-X", "utf8"] if sys.platform == "win32" else [])
# On Windows each suite runs in its own hidden console (CREATE_NO_WINDOW), so a mutant that reintroduces a console
# control event (os.kill(pid, 0) is one on Windows) can only interrupt its own suite: that is CAUGHT, never a hazard to
# the harness or to a sibling mutant.
ISOLATED = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
HOST = "WINDOWS" if sys.platform == "win32" else "POSIX"


def for_host(mutations, only_on):
    """(run here, not run here) for mutations whose name `only_on` maps to "WINDOWS" or "POSIX"; unmapped ones run on
    every host. A mutation that is not run on this host is reported NOT_RUN and never counted as caught."""
    here = [m for m in mutations if only_on.get(m[0], HOST) == HOST]
    return here, [m for m in mutations if m not in here]


def read(path):
    """A source file's text, decoded from its exact bytes."""
    return path.read_bytes().decode("utf-8")


def write(path, text):
    """Write text back as exact UTF-8 bytes, with no newline translation."""
    path.write_bytes(text.encode("utf-8"))


def baseline_status(verdict):
    """PASS, RED or NOT_RUN for what a harness's run() said about a mutation with no edits.

    run() reports MISSED when the suite passed and CAUGHT when it failed (including a hang); anything else (NOT
    APPLIED, a missing prerequisite, an error) means the baseline did not run as a qualification."""
    if verdict == "MISSED":
        return "PASS"
    if verdict == "CAUGHT":
        return "RED"
    return "NOT_RUN"


def _verdict(run, mutation):
    try:
        return run(mutation)[1]
    except Exception as exc:  # a crashing baseline is not a passing one
        return f"ERROR ({type(exc).__name__}: {exc})"


def qualify(run, mutations, jobs=4, baselines=None, out=None, not_run=()):
    """Run the baselines, then the mutants, and return the harness exit code.

    `run(mutation)` returns (name, verdict). `baselines` are no-edit mutations in this harness's own shape (default
    `[(BASELINE, [])]`). No mutant is run, and none is counted, unless every baseline passed. `not_run` are mutations
    that do not apply to this host; they are listed as NOT_RUN and never counted."""
    out = out or sys.stdout
    for mutation in not_run:
        print(f"{'NOT_RUN':<12} {mutation[0]} (applies to another host)", file=out)
    baselines = list(baselines) if baselines is not None else [(BASELINE, [])]
    blocked = []
    if not baselines:
        blocked.append("no baseline was defined")
    for mutation in baselines:
        verdict = _verdict(run, mutation)
        status = baseline_status(verdict)
        print(f"{status:<12} {mutation[0]}: {verdict}", file=out)
        if status != "PASS":
            blocked.append(f"{mutation[0]} is {status}")
    if blocked:
        print(f"mutation qualification BLOCKED ({'; '.join(blocked)}): no mutant was run or counted", file=out)
        return BLOCKED_EXIT
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        results = list(pool.map(run, mutations))
    for name, verdict in results:
        print(f"{verdict:<12} {name}", file=out)
    caught = sum(v == "CAUGHT" for _, v in results)
    print(f"caught {caught} of {len(results)}" + (f" ({len(not_run)} NOT_RUN on this host)" if not_run else ""),
          file=out)
    return QUALIFIED_EXIT if caught == len(results) else MISSED_EXIT
