#!/usr/bin/env python3
"""Start AgentNet and open it in your browser.   python run.py   (options: --port 8000 --reset --no-browser)"""
import argparse, os, threading, webbrowser

ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
ap.add_argument("--reset", action="store_true", help="delete the local database first")
ap.add_argument("--no-browser", action="store_true")
args = ap.parse_args()

if args.reset:
    from agentnet.db import DB_PATH
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(DB_PATH + suffix):
            os.remove(DB_PATH + suffix)

from agentnet import server  # noqa: E402  (imported after the optional reset)

httpd = server.serve(args.port)
url = f"http://127.0.0.1:{args.port}"
print(f"AgentNet running at {url}   (Ctrl+C to stop)")
if not args.no_browser:
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
try:
    httpd.serve_forever()
except KeyboardInterrupt:
    print("\nbye")
