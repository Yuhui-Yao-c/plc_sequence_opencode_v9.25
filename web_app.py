#!/usr/bin/env python3
"""Compatibility entry: every Web launcher now starts the same Node backend."""
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    node = os.environ.get("PLC_NODE") or shutil.which("node")
    if not node:
        print("Node.js 18+ is required. Install Node.js and reopen this terminal.", file=sys.stderr)
        return 1
    root = Path(__file__).resolve().parent
    child = subprocess.Popen([node, str(root / "server.js"), "--open"], cwd=str(root))
    try:
        return child.wait()
    except KeyboardInterrupt:
        if os.name != "nt":
            child.send_signal(__import__("signal").SIGINT)
        try:
            return child.wait(timeout=8)
        except subprocess.TimeoutExpired:
            child.terminate()
            return child.wait()


if __name__ == "__main__":
    sys.exit(main())
