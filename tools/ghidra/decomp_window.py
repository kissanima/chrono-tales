# Ghidra headless post-script (PyGhidra / Python 3).
#
# Decompile a targeted set of functions from the already-analyzed program:
#   1. every function whose entry point falls inside any given VA window,
#   2. every function that references a byte-pattern (e.g. a debug string).
#
# Usage (analyzeHeadless):
#   -postScript decomp_window.py OUTDIR WINDOWS STRINGS
#     WINDOWS : semicolon-separated VA windows "start-end" (hex ok), may be "-"
#     STRINGS : comma-separated ASCII substrings to search and xref, may be "-"
#
# Output: OUTDIR/func_<VA>.c  per function, plus OUTDIR/_index.txt listing
# (function name, entry VA, size, body length) sorted by VA.

import os
import re

from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor


def parse_windows(s):
    out = []
    if not s or s == "-":
        return out
    for part in s.split(";"):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.append((int(a, 0), int(b, 0)))
        else:
            v = int(part, 0)
            out.append((v, v))
    return out


def func_in_windows(func, wins):
    ea = func.getEntryPoint().getOffset()
    for lo, hi in wins:
        if lo <= ea <= hi:
            return True
    return False


def main():
    args = getScriptArgs()
    if len(args) < 2:
        print("usage: decomp_window.py OUTDIR WINDOWS STRINGS")
        return
    outdir = args[0]
    wins = parse_windows(args[1])
    strpats = [s for s in args[2].split(",") if s and s != "-"] if len(args) > 2 else []

    if not os.path.isdir(outdir):
        os.makedirs(outdir)

    fm = currentProgram.getFunctionManager()
    monitor = ConsoleTaskMonitor()
    ifc = DecompInterface()
    ifc.openProgram(currentProgram)

    targets = {}  # entry addr -> function

    # 1. window-based
    for f in fm.getFunctions(True):
        if func_in_windows(f, wins):
            targets[f.getEntryPoint()] = f

    # 2. string-pattern-based: find ascii bytes, then xrefs from code
    memory = currentProgram.getMemory()
    listing = currentProgram.getListing()
    refmgr = currentProgram.getReferenceManager()
    for pat in strpats:
        needle = pat.encode("ascii")
        addr = memory.getMinAddress()
        found = 0
        while found < 400:
            addr = memory.findBytes(addr, needle, None, True, monitor)
            if addr is None:
                break
            for ref in refmgr.getReferencesTo(addr):
                fn = fm.getFunctionContaining(ref.getFromAddress())
                if fn is not None:
                    targets[fn.getEntryPoint()] = fn
            found += 1
            addr = addr.add(1)
        print("pattern %r: %d hits scanned" % (pat, found))

    print("targets: %d functions" % len(targets))

    index = []
    for ea in sorted(targets):
        fn = targets[ea]
        try:
            res = ifc.decompileFunction(fn, 180, monitor)
            if res.decompileCompleted():
                text = res.getDecompiledFunction().getC()
            else:
                text = "// decompile FAILED: %s\n" % res.getErrorMessage()
        except Exception as e:
            text = "// decompile exception: %s\n" % e
        name = re.sub(r"[^A-Za-z0-9_.]", "_", fn.getName())
        fnfile = os.path.join(outdir, "func_%08x_%s.c" % (ea, name))
        with open(fnfile, "w") as fh:
            fh.write("// %s  entry=%#x  size=%d\n" % (fn.getName(), ea, fn.getBody().getNumAddresses()))
            fh.write(text)
        index.append((ea, fn.getName(), fn.getBody().getNumAddresses(), len(text)))

    with open(os.path.join(outdir, "_index.txt"), "w") as fh:
        fh.write("entry_va\tname\tsize_bytes\tdecomp_len\n")
        for ea, name, sz, ln in index:
            fh.write("%#x\t%s\t%d\t%d\n" % (ea, name, sz, ln))

    print("wrote %d decompiled functions to %s" % (len(index), outdir))


main()
