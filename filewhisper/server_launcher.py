import atexit
import json
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

# NOTE: uvicorn (and the app itself) are imported inside main(), after
# _ensure_output_streams() has run. Importing them at module level means any
# import failure under pythonw.exe (no console, stdout/stderr = None) dies
# before we can write a log line — the app just silently "doesn't open".


def _state_dir() -> Path:
    base = os.getenv("FILEWHISPER_HOME") or os.path.join(os.path.expanduser("~"), ".filewhisper")
    return Path(base)


def _lan_enabled() -> bool:
    """LAN access (phones/other devices) is opt-in. Default is localhost-only:
    binding 0.0.0.0 exposes /browse and the user's documents to everyone on the
    same network, and triggers the Windows Firewall popup on first launch."""
    return os.getenv("FILEWHISPER_LAN", "").strip().lower() in ("1", "true", "yes")


def _ensure_output_streams():
    """Guarantee sys.stdout/sys.stderr are real, writable streams.

    On Windows the Desktop shortcut launches us with pythonw.exe (no console),
    where sys.stdout and sys.stderr are None. uvicorn's log formatter calls
    sys.stdout.isatty() while starting up, which then raises AttributeError and
    the server never starts - the app looks like it "won't open". Point the
    missing streams at the log file so the server runs and we still get logs.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        _state_dir().mkdir(parents=True, exist_ok=True)
        log = open(_state_dir() / "filewhisper.log", "a", buffering=1, encoding="utf-8")
    except Exception:
        log = open(os.devnull, "w")
    if sys.stdout is None:
        sys.stdout = log
    if sys.stderr is None:
        sys.stderr = log


def _pid_file() -> Path:
    return _state_dir() / "filewhisper.pid"


def _port_file() -> Path:
    return _state_dir() / "filewhisper.port"


def _is_filewhisper(port: int) -> bool:
    """True if a FileWhisper server (not just any app) answers on this port."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1.5) as resp:
            if resp.status != 200:
                return False
            body = json.loads(resp.read().decode("utf-8"))
            return body.get("app") == "filewhisper"
    except Exception:
        return False


def _existing_port():
    """If a FileWhisper server is already running, return its port; else None.

    Uses an HTTP health check (cross-platform, no os.kill) so repeat launches
    reuse the running instance instead of spawning another server. The health
    body is verified so a stale port file pointing at some other local app
    doesn't make us open the wrong page.
    """
    pf = _port_file()
    if not pf.exists():
        return None
    try:
        port = int(pf.read_text().strip())
    except (OSError, ValueError):
        return None
    return port if _is_filewhisper(port) else None


def _write_state(port: int):
    """Record PID + port so repeat launches can reuse the running instance.
    Files are cleaned up on a clean exit."""
    try:
        _state_dir().mkdir(parents=True, exist_ok=True)
        pidf, portf = _pid_file(), _port_file()
        pidf.write_text(str(os.getpid()))
        portf.write_text(str(port))
        atexit.register(lambda: (pidf.unlink(missing_ok=True), portf.unlink(missing_ok=True)))
    except Exception:
        pass  # state files are a convenience; never block startup over them


def find_free_port(host: str, start: int = 8001, end: int = 8100) -> int:
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
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
    # Must run before anything prints or uvicorn starts: on Windows (pythonw)
    # there is no console, so stdout/stderr are None and uvicorn would crash.
    _ensure_output_streams()

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
        import uvicorn
        try:
            from filewhisper.main import app
        except ImportError:
            from main import app
    except Exception:
        # With pythonw there is no console: make sure the reason the app
        # "won't open" ends up in the log before dying.
        import traceback
        print("FileWhisper failed to start:")
        traceback.print_exc()
        print(f"(log file: {_state_dir() / 'filewhisper.log'})")
        raise

    lan = _lan_enabled()
    host = "0.0.0.0" if lan else "127.0.0.1"

    env_port = os.getenv("FILEWHISPER_PORT")
    port = int(env_port) if env_port else find_free_port(host)
    os.environ["FILEWHISPER_PORT"] = str(port)

    def open_browser():
        # Wait until the server actually answers before opening the browser, so
        # a slow cold start (model/onnx imports) doesn't open a dead page.
        url = f"http://127.0.0.1:{port}"
        for _ in range(60):  # up to ~30s
            if _is_filewhisper(port):
                break
            time.sleep(0.5)
        try:
            webbrowser.open(url)
        except Exception as e:
            print(f"Could not open browser automatically: {e}")

    # Launch browser thread
    threading.Thread(target=open_browser, daemon=True).start()

    _write_state(port)

    print("\n" + "=" * 60)
    print("  FileWhisper is starting...")
    print(f"  Local Access:   http://127.0.0.1:{port}")
    if lan:
        local_ip = get_local_ip()
        if local_ip != "127.0.0.1":
            print(f"  Network Access: http://{local_ip}:{port} (For mobile / other devices on the same Wi-Fi)")
    else:
        print("  (localhost only - set FILEWHISPER_LAN=1 to allow other devices on your Wi-Fi)")
    print("=" * 60 + "\n")

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
