"""Minimal client for the RPFM server's v2 API (JSON-RPC 2.0 over WebSocket),
RPFM 5.3 or later.

Starts rpfm_server.exe if nothing is listening. The server exits once its
last session disconnects, so keep one Rpfm instance open for a whole run.
"""
import itertools
import json
import os
import subprocess
import sys
import time
import urllib.request

try:
    import websocket
except ImportError:
    sys.exit("The websocket-client package is missing. Run:  python -m pip install -r requirements.txt")

DEFAULT_URL = "ws://127.0.0.1:45127/ws"


class RpfmError(RuntimeError):
    pass


class Rpfm:
    def __init__(self, exe, url=DEFAULT_URL, timeout=1800):
        """`exe` is rpfm_server.exe, started if no server is running yet."""
        self.ws = None
        started = False
        _wait_for_shutdown(url)
        for _ in range(120):
            try:
                self.ws = websocket.create_connection(url, timeout=timeout)
                break
            except ConnectionRefusedError:
                if not started:
                    subprocess.Popen([exe], cwd=os.path.dirname(exe),
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    started = True
                time.sleep(0.5)
        if self.ws is None:
            raise RpfmError(f"could not connect to {url}")
        self._ids = itertools.count(1)

    def call(self, method, params=None):
        i = next(self._ids)
        self.ws.send(json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == i:   # anything else is a notification
                if "error" in msg:
                    raise RpfmError(f"{method}: {msg['error']['message']}")
                return msg["result"]

    def job(self, method, params=None):
        """Call a method that runs as a job, and wait for its result."""
        job = self.call(method, params)["job"]
        while True:
            status = self.call("job.wait", {"job": job, "timeout_secs": 600})
            if status["state"] == "finished":
                return status["result"]
            if status["state"] == "failed":
                raise RpfmError(f"{method}: {status['error']['message']}")
            if status["state"] == "cancelled":
                raise RpfmError(f"{method}: cancelled")

    def pages(self, method, params, items):
        """Every item of a paginated method, `items` being the result's list field."""
        offset = 0
        while True:
            res = self.call(method, {**params, "offset": offset, "limit": 10000})
            yield from res[items]
            offset += len(res[items])
            if not res[items] or offset >= res["total"]:
                return

    def close(self):
        if self.ws is None:
            return
        try:
            self.call("session.disconnect")
        finally:
            self.ws.close()
            self.ws = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _wait_for_shutdown(url, limit=10):
    """A server whose last session just disconnected accepts connections for about a
    second while it exits, then drops them. If it has no sessions, wait for it to go."""
    base = url.replace("ws://", "http://", 1).rsplit("/ws", 1)[0]
    try:
        with urllib.request.urlopen(base + "/sessions", timeout=2) as r:
            if json.load(r):
                return
    except (OSError, ValueError):
        return   # nothing listening
    deadline = time.time() + limit
    while time.time() < deadline:
        try:
            urllib.request.urlopen(base + "/version", timeout=2).close()
        except OSError:
            return
        time.sleep(0.25)


def source(name, pack_key):
    """A FileSource: "pack" is the open pack, the rest are the dependencies
    ("game_files", "parent_files", "assembly_kit")."""
    return {"pack": pack_key} if name == "pack" else name
