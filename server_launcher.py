import os
import socket

import uvicorn


def find_free_port(start: int = 8001, end: int = 8100) -> int:
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("No free local port found.")


if __name__ == "__main__":
    from main import app
    port = int(os.getenv("FILEWHISPER_PORT", find_free_port()))
    os.environ["FILEWHISPER_PORT"] = str(port)
    print(f"\n  FileWhisper is starting...")
    print(f"  Open in your browser: http://127.0.0.1:{port}\n")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
