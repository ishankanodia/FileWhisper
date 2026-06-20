import atexit
import os
import socket
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn


def _state_dir() -> Path:
    base = os.getenv("FILEWHISPER_HOME") or os.path.join(os.path.expanduser("~"), ".filewhisper")
    return Path(base)


def _pid_file() -> Path:
    return _state_dir() / "filewhisper.pid"


def _port_file() -> Path:
    return _state_dir() / "filewhisper.port"


def _existing_port():
    """If a FileWhisper server is already running, return its port; else None.

    Uses an HTTP health check (cross-platform, no os.kill) so repeat launches
    reuse the running instance instead of spawning another server.
    """
    pf = _port_file()
    if not pf.exists():
        return None
    try:
        port = int(pf.read_text().strip())
    except (OSError, ValueError):
        return None
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1.5) as resp:
            if resp.status == 200:
                return port
    except Exception:
        return None
    return None


def _write_state(port: int):
    """Record PID + port so the Stop shortcut can find us and repeat launches can
    reuse the running instance. Files are cleaned up on a clean exit."""
    try:
        _state_dir().mkdir(parents=True, exist_ok=True)
        pidf, portf = _pid_file(), _port_file()
        pidf.write_text(str(os.getpid()))
        portf.write_text(str(port))
        atexit.register(lambda: (pidf.unlink(missing_ok=True), portf.unlink(missing_ok=True)))
    except Exception:
        pass  # state files are a convenience; never block startup over them


def find_free_port(start: int = 8001, end: int = 8100) -> int:
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("0.0.0.0", port))
            except OSError:
                continue
            return port
    raise RuntimeError("No free local port found.")


def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main():
    # If FileWhisper is already running, just reopen it instead of starting
    # a second server (e.g. when the Desktop icon is double-clicked again).
    existing = _existing_port()
    if existing:
        print(f"FileWhisper is already running at http://127.0.0.1:{existing} - opening it in your browser.")
        try:
            webbrowser.open(f"http://127.0.0.1:{existing}")
        except Exception:
            pass
        return

    try:
        from filewhisper.main import app
    except ImportError:
        from main import app

    port = int(os.getenv("FILEWHISPER_PORT", find_free_port()))
    os.environ["FILEWHISPER_PORT"] = str(port)

    local_ip = get_local_ip()

    def open_browser():
        time.sleep(1.5)
        try:
            webbrowser.open(f"http://127.0.0.1:{port}")
        except Exception as e:
            print(f"Could not open browser automatically: {e}")

    # Launch browser thread
    threading.Thread(target=open_browser, daemon=True).start()

    _write_state(port)

    print("\n" + "=" * 60)
    print("  FileWhisper is starting...")
    print(f"  Local Access:   http://127.0.0.1:{port}")
    if local_ip != "127.0.0.1":
        print(f"  Network Access: http://{local_ip}:{port} (For mobile / other devices on the same Wi-Fi)")
    print("=" * 60 + "\n")

    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
