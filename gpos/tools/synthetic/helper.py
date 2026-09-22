"""TEST_ONLY helper program for the synthetic reference adapter.

This is not a production tool and drives nothing real. It is a small, deterministic program the
synthetic adapter executes through the ordinary process boundary, so the foundation's process
safety, timeout, capture-bound, redaction and artifact rules are exercised end to end without
depending on any real production tool or device being installed.

It is executed as `<python> <this file> <verb> [args]` — an executable plus an argument vector,
never a command string.
"""

import sys
import time


def main(argv):
    verb = argv[1] if len(argv) > 1 else "inspect"
    if verb == "version":
        print("gpos-synthetic-helper 1.0.0")
        return 0
    if verb == "inspect":
        print(f"inspected {argv[2] if len(argv) > 2 else 'nothing'}")
        return 0
    if verb == "write":
        path, text = argv[2], argv[3]
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"wrote {path}")
        return 0
    if verb == "fail":
        print("synthetic failure on stdout")
        print("synthetic failure on stderr", file=sys.stderr)
        return 3
    if verb == "sleep":
        time.sleep(float(argv[2]))
        return 0
    if verb == "noisy":
        chunk = "x" * 1024
        for _ in range(int(argv[2])):
            print(chunk)
        return 0
    if verb == "leak":
        print("starting run")
        print("API_KEY=super-secret-test-value")
        print("Authorization: Bearer test-bearer-token-value")
        print("fps=59.8 level=forest-02")
        return 0
    print(f"unknown verb {verb!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
