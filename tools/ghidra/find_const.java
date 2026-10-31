/* Ghidra headless post-script (Java).
 * Find a byte pattern (e.g. a float constant pair) in memory, then decompile
 * every function that references it.
 *
 * Usage:
 *   -postScript find_const.java OUTDIR HEXPATTERN[,HEXPATTERN...]
 *     HEXPATTERN: lowercase hex bytes, no separators, e.g. 0000c8c200006144
 */
import java.io.File;
import java.io.PrintWriter;
import java.util.ArrayList;
import java.util.List;
import java.util.TreeMap;

import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionManager;
import ghidra.program.model.listing.Program;
import ghidra.program.model.symbol.Reference;
import ghidra.util.task.ConsoleTaskMonitor;

public class find_const extends GhidraScript {

    private static byte[] parseHex(String s) {
        int n = s.length() / 2;
        byte[] b = new byte[n];
        for (int i = 0; i < n; i++) {
            b[i] = (byte) Integer.parseInt(s.substring(i * 2, i * 2 + 2), 16);
        }
        return b;
    }

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 2) {
            println("usage: find_const.java OUTDIR HEXPATTERN[,HEXPATTERN...]");
            return;
        }
        String outdir = args[0];
        new File(outdir).mkdirs();

        Program prog = currentProgram;
        FunctionManager fm = prog.getFunctionManager();
        TreeMap<Long, Function> targets = new TreeMap<>();

        for (String spec : args[1].split(",")) {
            byte[] needle = parseHex(spec.trim());
            int found = 0;
            Address addr = prog.getMinAddress();
            while (found < 200 && !monitor.isCancelled()) {
                addr = prog.getMemory().findBytes(addr, needle, null, true, monitor);
                if (addr == null) break;
                println("hit " + addr + " for " + spec);
                for (Reference ref : prog.getReferenceManager().getReferencesTo(addr)) {
                    Function fn = fm.getFunctionContaining(ref.getFromAddress());
                    if (fn != null) {
                        targets.put(fn.getEntryPoint().getOffset(), fn);
                        println("   xref from " + ref.getFromAddress() + " in "
                                + fn.getName());
                    }
                }
                found++;
                addr = addr.add(1);
            }
            println("pattern [" + spec + "]: " + found + " hits");
        }

        println("targets: " + targets.size() + " functions");
        DecompInterface ifc = new DecompInterface();
        ifc.openProgram(prog);

        PrintWriter index = new PrintWriter(new File(outdir, "_index.txt"));
        index.println("entry_va\tname\tsize_bytes\tdecomp_len");
        for (long ea : targets.keySet()) {
            Function fn = targets.get(ea);
            DecompileResults res = ifc.decompileFunction(fn, 240, new ConsoleTaskMonitor());
            String text = res.decompileCompleted()
                    ? res.getDecompiledFunction().getC()
                    : "// decompile FAILED: " + res.getErrorMessage() + "\n";
            String safeName = fn.getName().replaceAll("[^A-Za-z0-9_.]", "_");
            File fnfile = new File(outdir, String.format("func_%08x_%s.c", ea, safeName));
            PrintWriter pw = new PrintWriter(fnfile);
            pw.println(String.format("// %s  entry=0x%x  size=%d", fn.getName(), ea,
                    fn.getBody().getNumAddresses()));
            pw.print(text);
            pw.close();
            index.println(String.format("0x%x\t%s\t%d\t%d", ea, fn.getName(),
                    fn.getBody().getNumAddresses(), text.length()));
        }
        index.close();
        println("wrote " + targets.size() + " decompiled functions to " + outdir);
    }
}
