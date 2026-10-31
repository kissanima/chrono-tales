#!/usr/bin/env python3
"""cdp_eval.py - evaluate JS in a page once it is ready, print the value.

The companion to cdp_shot.py: that one proves what the client *draws*, this
one asks the client what it *thinks*.  Same stdlib WebSocket client, so no
dependencies.

    python tools/cdp_eval.py URL --ready "window.__ct" --js "1+1"
    python tools/cdp_eval.py URL --ready "window.__ct" --file work/probe.js

`--js` may be given several times; each is evaluated in order in the same
page and printed with its own label, so a multi-step probe can build on
what the previous step computed.
"""
import argparse
import json
import sys
import time

from cdp_shot import CDP, find_browser, wait_target
import subprocess


def launch(url, port, size):
    w, h = (size.lower().split("x") + ["800"])[:2]
    prof = f"/tmp/cdp-eval-{port}"
    cmd = [find_browser(), "--headless=new", f"--remote-debugging-port={port}",
           f"--user-data-dir={prof}", f"--window-size={w},{h}",
           "--no-first-run", "--no-default-browser-check",
           "--use-gl=angle", "--use-angle=swiftshader",
           "--enable-unsafe-swiftshader", "--hide-scrollbars",
           "--allow-file-access-from-files", url]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--js", action="append", default=[])
    ap.add_argument("--file", action="append", default=[])
    ap.add_argument("--ready", default="document.readyState==='complete'")
    ap.add_argument("--timeout", type=float, default=90)
    ap.add_argument("--size", default="1100x700")
    ap.add_argument("--port", type=int, default=9345)
    a = ap.parse_args()

    exprs = list(a.js)
    for f in a.file:
        exprs.append(open(f, encoding="utf-8").read())

    proc = launch(a.url, a.port, a.size)
    try:
        cdp = CDP(wait_target(a.port))
        end = time.time() + a.timeout
        ready = False
        while time.time() < end:
            try:
                v = cdp.eval(a.ready)
                # a thrown expression comes back as a truthy "JS EXCEPTION:"
                # string -- never mistake that for "ready"
                if v and not (isinstance(v, str) and v.startswith("JS EXCEPTION")):
                    ready = True
                    break
            except SystemExit:
                pass
            time.sleep(0.25)
        if not ready:
            print(f"warning: ready expression never became true ({a.ready})",
                  file=sys.stderr)
        for i, e in enumerate(exprs, 1):
            v = cdp.eval(e)
            print(f"--- [{i}] ---")
            if isinstance(v, (dict, list)):
                print(json.dumps(v, indent=1))
            else:
                print(v)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    return 0 if ready else 2


if __name__ == "__main__":
    sys.exit(main())
