"""Run all engine tests with no provider credentials or external connections.

Usage: .venv/Scripts/python.exe tests/run_offline.py [pytest arguments]
Task workspaces use a fresh OS temporary directory outside the source checkout.
"""
import os
from pathlib import Path
import socket
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
os.chdir(root)
sys.path.insert(0, str(root))
os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
for key in list(os.environ):
    if any(word in key.upper() for word in ("API_KEY", "TOKEN", "SECRET", "PASSWORD")):
        os.environ.pop(key)
connect, connect_ex = socket.socket.connect, socket.socket.connect_ex


def local(self, address):
    if not isinstance(address, tuple) or address[0] not in ("127.0.0.1", "::1"):
        raise RuntimeError("Offline verification prohibits external network connections")
    return connect(self, address)


def local_ex(self, address):
    if not isinstance(address, tuple) or address[0] not in ("127.0.0.1", "::1"):
        raise RuntimeError("Offline verification prohibits external network connections")
    return connect_ex(self, address)


socket.socket.connect, socket.socket.connect_ex = local, local_ex
import pytest

workspace = Path(tempfile.mkdtemp(prefix="next-code-p7-offline-"))
assert not workspace.is_relative_to(root)
raise SystemExit(pytest.main([*sys.argv[1:], "--basetemp", str(workspace / "cases")]))
