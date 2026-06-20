import atexit
import os
import socket
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn


def _pid_file() -> Path:
    base = os.getenv("FILEWHISPER_HOME") or os.path.join(os.path.expanduser("~"), ".filewhisper")
    return Path(base) / "filewhisper.pid"


def _write_pid_file():
    """Record this process's PID so a 'Stop' shortcut can terminate it cleanly."""
    try:
        path = _pid_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(os.getpid()))
        atexit.register(lambda: path.unlink(missing_ok=True))
    except Exception:
        pass  # PID file is a convenience; never block startup over it


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

    _write_pid_file()

    print("\n" + "=" * 60)
    print("  FileWhisper is starting...")
    print(f"  Local Access:   http://127.0.0.1:{port}")
    if local_ip != "127.0.0.1":
        print(f"  Network Access: http://{local_ip}:{port} (For mobile / other devices on the same Wi-Fi)")
    print("=" * 60 + "\n")

    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
