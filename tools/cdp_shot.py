#!/usr/bin/env python3
"""cdp_shot.py - screenshot a page once it says it is ready.

Why this exists: `msedge --screenshot` fires when the load event settles, and
`--virtual-time-budget` does not reliably wait for an async glTF/texture load,
so a WebGL page comes out as a blank canvas.  This drives the browser over the
DevTools protocol instead: navigate, poll an expression until it is true, then
capture.  No dependencies -- the WebSocket client below is ~60 lines of stdlib.

    python tools/cdp_shot.py URL OUT.png --ready "document.title==='rendered'"
    python tools/cdp_shot.py URL OUT.png --ready-expr "!!window.__done" --size 1280x800

Starts its own headless Edge (found automatically) unless --port points at one
already running.
"""
import argparse
import base64
import hashlib
import json
import os
import pathlib
import socket
import struct
import subprocess
import sys
import time
import urllib.request

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def find_browser():
    for p in EDGE_CANDIDATES:
        if os.path.exists(p):
            return p
    raise SystemExit("no Edge/Chrome found")


# --- minimal WebSocket client ---------------------------------------------

class WS:
    def __init__(self, url, timeout=30):
        # ws://host:port/path
        rest = url.split("://", 1)[1]
        hostport, _, path = rest.partition("/")
        host, _, port = hostport.partition(":")
        self.sock = socket.create_connection((host, int(port or 80)), timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\n"
               f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise SystemExit("websocket handshake failed")
            buf += chunk
        head, _, tail = buf.partition(b"\r\n\r\n")
        if b"101" not in head.split(b"\r\n")[0]:
            raise SystemExit(f"handshake rejected: {head[:120]!r}")
        self.buf = tail

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise SystemExit("websocket closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, text):
        data = text.encode()
        hdr = bytearray([0x81])                    # FIN + text
        n = len(data)
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126)
            hdr += struct.pack(">H", n)
        else:
            hdr.append(0x80 | 127)
            hdr += struct.pack(">Q", n)
        mask = os.urandom(4)
        hdr += mask
        self.sock.sendall(bytes(hdr) + bytes(b ^ mask[i % 4]
                                             for i, b in enumerate(data)))

    def recv(self):
        """One message, reassembling fragments."""
        parts = []
        while True:
            b0, b1 = self._read(2)
            fin, opcode = b0 & 0x80, b0 & 0x0F
            ln = b1 & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", self._read(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", self._read(8))[0]
            if b1 & 0x80:                          # masked (should not happen)
                mask = self._read(4)
                payload = bytes(c ^ mask[i % 4]
                                for i, c in enumerate(self._read(ln)))
            else:
                payload = self._read(ln)
            if opcode == 0x8:
                raise SystemExit("websocket closed by peer")
            if opcode == 0x9:                      # ping -> pong
                self.sock.sendall(bytes([0x8A, 0x80]) + os.urandom(4))
                continue
            parts.append(payload)
            if fin:
                return b"".join(parts).decode()


class CDP:
    def __init__(self, ws_url):
        self.ws = WS(ws_url)
        self.id = 0

    def call(self, method, **params):
        self.id += 1
        mine = self.id
        self.ws.send(json.dumps({"id": mine, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mine:
                if "error" in msg:
                    raise SystemExit(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def eval(self, expr):
        # awaitPromise so a probe can hand back a Promise (e.g. "move for 2.5 s,
        # then report") -- for a plain value it resolves immediately anyway.
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True,
                      awaitPromise=True)
        # A throwing expression otherwise comes back as None, which reads
        # exactly like "the value is null" -- say so instead.
        if "exceptionDetails" in r:
            d = r["exceptionDetails"]
            msg = (d.get("exception") or {}).get("description") or d.get("text")
            return f"JS EXCEPTION: {msg}"
        return r.get("result", {}).get("value")


def wait_target(port, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/list", timeout=2) as f:
                for t in json.load(f):
                    if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                        return t["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(0.3)
    raise SystemExit("no debuggable page target appeared")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("out")
    ap.add_argument("--ready", default="document.readyState==='complete'",
                    help="JS expression that must become truthy")
    ap.add_argument("--timeout", type=float, default=60)
    ap.add_argument("--size", default="1280x800")
    ap.add_argument("--port", type=int, default=9333)
    ap.add_argument("--keep-open", action="store_true")
    a = ap.parse_args()

    w, h = (int(x) for x in a.size.lower().split("x"))
    proc = subprocess.Popen(
        [find_browser(), "--headless=new", f"--remote-debugging-port={a.port}",
         "--enable-unsafe-swiftshader", "--use-angle=swiftshader",
         "--no-first-run", "--no-default-browser-check",
         f"--window-size={w},{h}",
         "--user-data-dir=" + str(pathlib.Path(os.environ.get("TEMP", "/tmp"))
                                  / "cdp_shot_profile"),
         "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        cdp = CDP(wait_target(a.port))
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Emulation.setDeviceMetricsOverride", width=w, height=h,
                 deviceScaleFactor=1, mobile=False)
        cdp.call("Page.navigate", url=a.url)

        end = time.time() + a.timeout
        ready = False
        last = None
        while time.time() < end:
            try:
                last = cdp.eval(a.ready)
            except SystemExit:
                raise
            except Exception:
                last = None
            if last:
                ready = True
                break
            time.sleep(0.25)
        if not ready:
            print(f"warning: ready expression never became true (last={last!r})",
                  file=sys.stderr)

        # let a couple of real frames land after the ready signal
        time.sleep(0.6)
        shot = cdp.call("Page.captureScreenshot", format="png")
        data = base64.b64decode(shot["data"])
        pathlib.Path(a.out).write_bytes(data)
        print(f"{a.out}: {len(data):,} bytes"
              + ("" if ready else "  (NOT READY)"))
        return 0 if ready else 2
    finally:
        if not a.keep_open:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    sys.exit(main())
