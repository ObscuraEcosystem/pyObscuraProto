"""Audit runner: spawns every scenario in its own subprocess with an external
timeout and faulthandler-based thread dumps, then classifies PASS/FAIL/HANG.
"""

import os
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = os.path.abspath(os.path.join(HERE, "..", "..", ".venv", "bin", "python"))

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


def run_one(name, script):
    path = os.path.join(HERE, script)
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            [PY, path],
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
        if status == "PASS" and proc.returncode != 0:
            status = "FAIL"
        if status == "FAIL" and proc.returncode == 0:
            status = "PASS"
    elif proc.returncode != 0:
        # Non-zero exit without RESULT line: likely faulthandler dump at 10s.
        if "Current thread" in out and "Traceback" in out:
            status = "HANG"
        else:
            status = "FAIL"
    return name, status, wall, out, detail


def main():
    rows = []
    for name, script in SCENARIOS:
        print(f"=== {name} ({script}) ===", flush=True)
        name, status, wall, out, detail = run_one(name, script)
        rows.append((name, status, wall, detail))
        # Print the interesting tail of the output.
        lines = [l for l in out.splitlines() if l.strip()]
        tail = lines[-6:]
        for l in tail:
            print("  | " + l[:220], flush=True)
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


if __name__ == "__main__":
    main()
