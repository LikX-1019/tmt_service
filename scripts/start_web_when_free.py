"""Wait for an old web process to release its port, then replace it.

This is intentionally tiny and accepts only a fixed command; it exists so the
service hub can restart Uvicorn without leaving the port in TIME_WAIT conflict.
"""

from __future__ import annotations

import os
import socket
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError:
        return False


def main() -> int:
    if len(sys.argv) < 5:
        print("Usage: start_web_when_free.py OLD_PID HOST PORT COMMAND [ARG ...]", file=sys.stderr)
        return 2
    old_pid = int(sys.argv[1])
    host = sys.argv[2]
    port = int(sys.argv[3])
    command = sys.argv[4:]
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if not _pid_alive(old_pid) and not _port_open(host, port):
            break
        time.sleep(0.25)
    else:
        print("Timed out waiting for the old web process to exit", file=sys.stderr)
        return 1
    os.chdir(PROJECT_ROOT)
    os.execv(command[0], command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
