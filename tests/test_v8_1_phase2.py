"""v8.1 Phase 2 — One-Click Boot tests."""
import os, sys, subprocess, signal, time, re, tempfile, shutil
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def test_desktop_entry_has_find_free_port():
    """desktop_entry.py has find_free_port that returns a free port."""
    from app.desktop_entry import find_free_port
    port = find_free_port(9000, 5)
    assert 9000 <= port <= 9004, f"expected 9000-9004, got {port}"


def test_desktop_entry_has_ready_line():
    """desktop_entry.py prints BILLBOOK_READY port=XXXX."""
    src = (PROJECT_ROOT / "app" / "desktop_entry.py").read_text()
    assert "BILLBOOK_READY" in src
    assert "port=" in src
    assert "SIGTERM" in src
    assert "SIGINT" in src


def test_tauri_reads_ready_line():
    """desktop/src/main.rs reads BILLBOOK_READY + parses port."""
    rust = (PROJECT_ROOT / "desktop" / "src" / "main.rs").read_text()
    assert "BILLBOOK_READY" in rust
    assert "port" in rust.lower()


def test_boot_check_script_exists():
    """scripts/boot-check.py exists + has the right checks."""
    script = (PROJECT_ROOT / "scripts" / "boot-check.py").read_text()
    assert "BILLBOOK_READY" in script
    assert "/api/setup-status" in script
    assert "design-system.css" in script
    assert "Clean shutdown" in script
    assert "Double Boot" in script


def test_boot_check_passes():
    """Running boot-check.py exits with code 0 (single boot)."""
    # Kill any lingering processes on ports 8000-8001
    import subprocess as _sp
    try:
        _sp.run("lsof -ti:8000 -ti:8001 2>/dev/null | xargs kill -9 2>/dev/null", shell=True, timeout=5)
    except Exception:
        pass
    time.sleep(3)
    proc = subprocess.run(
        [sys.executable, "scripts/boot-check.py"],
        cwd=str(PROJECT_ROOT),
        capture_output=True, text=True, timeout=90,
    )
    assert proc.returncode == 0, f"boot-check failed:\n{proc.stdout[-500:]}\n{proc.stderr[-500:]}"
    assert "6 passed" in proc.stdout
    assert "Second boot responds on HTTP" in proc.stdout


if __name__ == "__main__":
    test_desktop_entry_has_find_free_port(); print("OK find_free_port")
    test_desktop_entry_has_ready_line(); print("OK READY line in source")
    test_tauri_reads_ready_line(); print("OK Tauri reads READY line")
    test_boot_check_script_exists(); print("OK boot-check script exists")
    test_boot_check_passes(); print("OK boot-check passes")
    print("\nALL v8.1 PHASE 2 TESTS PASSED")
