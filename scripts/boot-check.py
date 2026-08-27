#!/usr/bin/env python3
"""v8.1 Phase 2 — Boot check script.

Spawns desktop_entry.py headlessly, waits for the BILLBOOK_READY line,
polls /api/setup-status until HTTP 200, confirms static assets serve,
then shuts down cleanly. Must pass without a terminal.

Usage:
    python scripts/boot-check.py

Exit code 0 = pass, non-zero = fail.
"""
import os, sys, time, signal, subprocess, re, httpx
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def run_boot_check():
    results = []
    def check(label, cond, detail=""):
        mark = "PASS" if cond else "FAIL"
        results.append((mark, label, detail))
        print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))

    print("=== BillBook Boot Check ===\n")

    # 1. Spawn desktop_entry.py
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    env["BILLBOOK_DATA_DIR"] = str(Path(tempfile.mkdtemp(prefix="billbook_boot_")))
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.desktop_entry"],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    port = None
    try:
        # 2. Wait for BILLBOOK_READY line (up to 15s)
        ready_line = None
        deadline = time.time() + 15
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                time.sleep(0.1)
                continue
            line = line.strip()
            print(f"  [sidecar] {line}")
            if "BILLBOOK_READY" in line:
                ready_line = line
                break

        check("BILLBOOK_READY line printed", ready_line is not None, ready_line or "not found")

        if ready_line:
            # Parse port
            m = re.search(r"port=(\d+)", ready_line)
            if m:
                port = int(m.group(1))
                check(f"Port parsed: {port}", port > 0)
            else:
                check("Port parsed from READY line", False, f"line: {ready_line}")
                return False

        # 3. Poll /api/setup-status until HTTP 200
        base_url = f"http://127.0.0.1:{port}"
        http_ok = False
        for _ in range(30):
            try:
                r = httpx.get(f"{base_url}/api/setup-status", timeout=3)
                if r.status_code == 200:
                    http_ok = True
                    break
            except Exception:
                pass
            time.sleep(0.3)

        check("GET /api/setup-status returns 200", http_ok)

        # 4. Confirm static assets serve
        asset_ok = False
        try:
            r = httpx.get(f"{base_url}/static/css/design-system.css", timeout=3)
            asset_ok = r.status_code == 200 and len(r.text) > 100
        except Exception:
            pass
        check("Static assets serve (design-system.css)", asset_ok)

        # 5. Confirm login page serves
        login_ok = False
        try:
            r = httpx.get(f"{base_url}/login", timeout=3)
            login_ok = r.status_code == 200 and "BillBook" in r.text
        except Exception:
            pass
        check("Login page serves", login_ok)

    finally:
        # 6. Clean shutdown
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
                proc.wait(timeout=3)

        check("Clean shutdown (exit code 0)", proc.returncode == 0,
              f"exit code: {proc.returncode}")

    # Summary
    print(f"\n{'='*50}")
    passed = sum(1 for m, _, _ in results if m == "PASS")
    failed = sum(1 for m, _, _ in results if m == "FAIL")
    print(f"Boot check: {passed} passed, {failed} failed, {len(results)} total")
    return failed == 0


def test_double_boot():
    """Boot twice in a row — second boot should find port 8000 busy + use 8001."""
    print("\n=== Double Boot Test ===\n")
    results = []
    def check(label, cond, detail=""):
        mark = "PASS" if cond else "FAIL"
        results.append((mark, label, detail))
        print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))

    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    env["BILLBOOK_DATA_DIR"] = str(Path(tempfile.mkdtemp(prefix="billbook_dbl_")))

    # First boot
    proc1 = subprocess.Popen(
        [sys.executable, "-m", "app.desktop_entry"],
        cwd=str(PROJECT_ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    port1 = None
    for _ in range(50):
        line = proc1.stdout.readline()
        if line and "BILLBOOK_READY" in line:
            m = re.search(r"port=(\d+)", line.strip())
            if m:
                port1 = int(m.group(1))
            break
        if proc1.poll() is not None:
            break
        time.sleep(0.1)

    check(f"First boot on port {port1}", port1 is not None, f"port1={port1}")

    # Drain proc1 stdout in background so the pipe doesn't fill + block uvicorn
    import threading
    def drain_stdout(p):
        try:
            for _ in p.stdout:
                pass
        except Exception:
            pass
    drain1 = threading.Thread(target=drain_stdout, args=(proc1,), daemon=True)
    drain1.start()

    # Wait for first process to actually bind the port (uvicorn takes a moment)
    time.sleep(4.0)
    # Check if the first process is still alive
    if proc1.poll() is not None:
        check("First boot is listening", False, f"process exited with code {proc1.returncode}")
    else:
        # Verify first process is listening
        try:
            r = httpx.get(f"http://127.0.0.1:{port1}/api/setup-status", timeout=5)
            check("First boot is listening", r.status_code == 200)
        except Exception as e:
            check("First boot is listening", False, f"could not connect: {e}")

    # Second boot (while first is still running) — use a DIFFERENT data dir
    # so the two processes don't fight over the same SQLite DB
    env2 = env.copy()
    env2["BILLBOOK_DATA_DIR"] = str(Path(tempfile.mkdtemp(prefix="billbook_dbl2_")))
    proc2 = subprocess.Popen(
        [sys.executable, "-m", "app.desktop_entry"],
        cwd=str(PROJECT_ROOT), env=env2,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    port2 = None
    for _ in range(50):
        line = proc2.stdout.readline()
        if line and "BILLBOOK_READY" in line:
            m = re.search(r"port=(\d+)", line.strip())
            if m:
                port2 = int(m.group(1))
            break
        if proc2.poll() is not None:
            break
        time.sleep(0.1)

    # Wait for second to bind — drain its stdout too so it doesn't block
    drain2 = threading.Thread(target=drain_stdout, args=(proc2,), daemon=True)
    drain2.start()
    time.sleep(4.0)
    check(f"Second boot on different port ({port2} != {port1})",
          port2 is not None and port2 != port1,
          f"port1={port1}, port2={port2}")

    # Verify second boot responds
    if port2:
        try:
            r = httpx.get(f"http://127.0.0.1:{port2}/api/setup-status", timeout=3)
            check("Second boot responds on HTTP", r.status_code == 200)
        except Exception as e:
            check("Second boot responds on HTTP", False, str(e))

    # Clean up both
    for p in [proc1, proc2]:
        if p.poll() is None:
            p.send_signal(signal.SIGTERM)
            try:
                p.wait(timeout=5)
            except Exception:
                p.kill()

    return all(m == "PASS" for m, _, _ in results)


if __name__ == "__main__":
    import tempfile
    ok1 = run_boot_check()
    ok2 = test_double_boot()
    sys.exit(0 if (ok1 and ok2) else 1)
