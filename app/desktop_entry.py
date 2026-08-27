"""BillBook Desktop Entry Point — runs the FastAPI backend as a sidecar.

This is the entry point for the Nuitka/PyInstaller compiled binary.
It sets BILLBOOK_DATA_DIR to ./data next to the executable, starts
uvicorn on 127.0.0.1:8000, and exits cleanly on SIGTERM/SIGINT.
"""
import os
import sys
import signal
import socket
from pathlib import Path


def find_free_port(start: int = 8000, max_tries: int = 10) -> int:
    """Find a free port starting from `start`."""
    for port in range(start, start + max_tries):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    return start  # fallback


def main():
    # Set data directory to ./data next to the executable
    if not os.getenv("BILLBOOK_DATA_DIR"):
        if getattr(sys, "frozen", False):
            # Nuitka/PyInstaller: exe dir
            exe_dir = Path(sys.executable).parent
        else:
            # Running from source
            exe_dir = Path(__file__).resolve().parent.parent
        data_dir = exe_dir / "data"
        os.environ["BILLBOOK_DATA_DIR"] = str(data_dir)

    # Add the app package to sys.path (for compiled binaries)
    app_dir = Path(__file__).resolve().parent.parent
    if str(app_dir) not in sys.path:
        sys.path.insert(0, str(app_dir))

    # Find a free port
    port = find_free_port(8000)
    if port != 8000:
        print(f"[desktop] Port 8000 in use, using {port}", flush=True)

    # Print health line for the Tauri shell to detect
    print(f"BILLBOOK_READY port={port}", flush=True)

    # Import and run
    import uvicorn
    from app.main import app

    # Handle clean shutdown
    def shutdown(signum, frame):
        print(f"[desktop] Received signal {signum}, shutting down...", flush=True)
        # The signal handler in main.py clears the dirty flag
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
