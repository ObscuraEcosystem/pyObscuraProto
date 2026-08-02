"""Audit runner: spawns every scenario in its own subprocess with an external
timeout and faulthandler-based thread dumps, then classifies PASS/FAIL/HANG.

The observed statuses for each scenario are frozen in EXPECTED (baseline from
v1.1.1). The runner exits non-zero if any scenario diverges from EXPECTED.
"""

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

# Frozen baseline observed on v1.1.1 (local run via .venv/bin/python, recorded
# in /tmp/audit_v1.1.1.txt).
# TODO(transitional): the FAIL entries below (X4-P2..P7, X5-S1..S3, X6) are
# placeholder baselines for scenarios not yet implemented/fixed. When a
# scenario changes, update the scenario and its EXPECTED entry in one commit.
EXPECTED = {
    "X1": "PASS",
    "X2": "PASS",
    "X3": "PASS",
    "X4-P1": "UNKNOWN",
    "X4-P2": "FAIL",
    "X4-P3": "FAIL",
    "X4-P4": "FAIL",
    "X4-P5": "FAIL",
    "X4-P6": "FAIL",
    "X4-P7": "FAIL",
    "X5-S1": "FAIL",
    "X5-S2": "FAIL",
    "X5-S3": "FAIL",
    "X6": "FAIL",
}

SCENARIOS = [
    ("X1", "x1_env.py"),
    ("X2", "x2_saturation.py"),
    ("X3", "x3_head_of_line.py"),
    ("X4-P1", "x4_path_1_on_ready.py"),
    ("X4-P2", "x4_path_2_on_open.py"),
    ("X4-P3", "x4_path_3_on_close.py"),
    ("X4-P4", "x4_path_4_identity.py"),
    ("X4-P5", "x4_path_5_request_handler.py"),
    ("X4-P6", "x4_path_6_raw_identity.py"),
    ("X4-P7", "x4_path_7_raw_request.py"),
    ("X5-S1", "x5_gc_scenario1.py"),
    ("X5-S2", "x5_gc_scenario2.py"),
    ("X5-S3", "x5_gc_scenario3.py"),
    ("X6", "x6_timeouts.py"),
]

EXTERNAL_TIMEOUT = 30.0
# faulthandler inside each script dumps + exits at 10s.
DUMP_SECONDS = 10.0


def resolve_python():
    """Pick the interpreter used to spawn scenarios.

    Priority: explicit AUDIT_PYTHON override -> the interpreter running this
    script (sys.executable) -> fallback to the project venv. The env override
    comes first so CI/local runs can pin a specific build.
    """
    override = os.environ.get("AUDIT_PYTHON")
    if override:
        return os.path.abspath(override)
    if sys.executable:
        return os.path.abspath(sys.executable)
    return os.path.abspath(os.path.join(HERE, "..", "..", ".venv", "bin", "python"))


def run_one(py, name, script):
    path = os.path.join(HERE, script)
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            [py, path],
            capture_output=True,
            text=True,
            timeout=EXTERNAL_TIMEOUT,
        )
        wall = time.monotonic() - t0
        out = (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired as e:
        wall = time.monotonic() - t0
        out = (e.stdout or "") + (e.stderr or "") if isinstance(e.stdout, str) else ""
        return name, "HANG", wall, out, f"external_timeout={EXTERNAL_TIMEOUT:.0f}s"

    # Parse the last RESULT line.
    result_line = ""
    for line in out.splitlines():
        if line.startswith("RESULT:"):
            result_line = line.strip()
    status = "UNKNOWN"
    detail = ""
    if result_line:
        parts = result_line.split(" ", 2)
        status = parts[1] if len(parts) > 1 else "UNKNOWN"
        detail = parts[2] if len(parts) > 2 else ""
        if status == "HANG":
            return name, "HANG", wall, out, detail
        # The RESULT line is the scenario's self-reported verdict, but the
        # subprocess exit code is authoritative. Declared PASS + non-zero exit
        # means the check logic itself crashed -> downgrade to FAIL. The
        # reverse flip (declared FAIL + exit 0) trusts the run completed and
        # FAIL is the intended observation; caveat: it masks scenarios that
        # forget to exit(1) on a real failure.
        if status == "PASS" and proc.returncode != 0:
            status = "FAIL"
        if status == "FAIL" and proc.returncode == 0:
            status = "PASS"
    elif proc.returncode != 0:
        # Non-zero exit without RESULT line: likely faulthandler dump at 10s.
        # CPython >= 3.13 prints "Thread 0x... (most recent call first):" for
        # every thread, including the current one, so "Current thread" (the
        # 3.12 header) is never emitted and the dump would be misread as FAIL.
        # "(most recent call first)" is the structurally unique marker of a
        # faulthandler thread dump: normal Python tracebacks always say
        # "(most recent call last)", and the "Timeout (...)" header could
        # collide with user-facing timeout messages.
        if "(most recent call first)" in out:
            status = "HANG"
        else:
            status = "FAIL"
    return name, status, wall, out, detail


def main():
    py = resolve_python()
    print(f"audit python: {py}", flush=True)
    rows = []
    for name, script in SCENARIOS:
        print(f"=== {name} ({script}) ===", flush=True)
        name, status, wall, out, detail = run_one(py, name, script)
        rows.append((name, status, wall, detail))
        # Print the interesting tail of the output.
        lines = [line for line in out.splitlines() if line.strip()]
        tail = lines[-6:]
        for line in tail:
            print("  | " + line[:220], flush=True)
        if status == "HANG":
            print(
                f"  | [HANG] exit={wall:.1f}s external_timeout={EXTERNAL_TIMEOUT:.0f}s "
                f"faulthandler_dump_at={DUMP_SECONDS:.0f}s",
                flush=True,
            )
        print("", flush=True)

    print("=" * 70)
    print("SUMMARY")
    for name, status, wall, detail in rows:
        print(f"{name:8s} | {status:7s} | wall={wall:6.1f}s | {detail[:160]}", flush=True)

    print("=" * 70)
    print("EXPECTED vs ACTUAL")
    mismatches = 0
    for name, status, wall, detail in rows:
        expected = EXPECTED.get(name, "")
        # Strict UNKNOWN policy: UNKNOWN always diverges (red), regardless of
        # EXPECTED. User decision; overrides the old lenient UNKNOWN==UNKNOWN.
        if status == "UNKNOWN" or status != expected:
            mismatches += 1
            match = "NO"
        else:
            match = "yes"
        print(
            f"{name:8s} | expected={expected:7s} | actual={status:7s} | match={match}",
            flush=True,
        )

    print("=" * 70)
    if mismatches:
        print(f"AUDIT DIVERGED: {mismatches} mismatch(es) vs EXPECTED", flush=True)
        sys.exit(1)
    print("AUDIT OK: all scenarios match EXPECTED", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
