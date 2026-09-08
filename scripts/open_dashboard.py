#!/usr/bin/env python3
"""One-click Paodan Local Dashboard opener.

自动寻找可用端口（默认 8765，被占用则 +1），启动 Dashboard 并打开浏览器。
"""
from __future__ import annotations

import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def main() -> int:
    from app.dashboard.server import create_app
    from app.dashboard.config import load_dashboard_config

    cfg = load_dashboard_config()
    port = cfg.port
    while not _port_free(port):
        port += 1
    url = f"http://127.0.0.1:{port}"
    print("Paodan Local Dashboard")
    print(url)
    threading.Timer(1.2, webbrowser.open, args=[url]).start()
    import uvicorn
    try:
        uvicorn.run(create_app(), host=cfg.host, port=port, log_level="warning")
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
