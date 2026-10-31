/* Ghidra headless post-script (Java).
 * Decompile functions whose entry points fall inside given VA windows,
 * plus functions that reference ASCII byte patterns (debug strings).
 *
 * Usage:
 *   -postScript decomp_window.java OUTDIR WINDOWS STRINGS
 *     WINDOWS : semicolon-separated VA windows "start-end" (hex ok), or "-"
 *     STRINGS : comma-separated ASCII substrings, or "-"
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
import ghidra.program.model.listing.FunctionIterator;
import ghidra.program.model.listing.FunctionManager;
import ghidra.program.model.listing.Program;
import ghidra.program.model.symbol.Reference;
import ghidra.util.task.ConsoleTaskMonitor;

public class decomp_window extends GhidraScript {

    private List<long[]> parseWindows(String s) {
        List<long[]> out = new ArrayList<>();
        if (s == null || s.isEmpty() || s.equals("-")) return out;
        for (String part : s.split(";")) {
            part = part.trim();
            if (part.isEmpty()) continue;
            if (part.contains("-")) {
                String[] ab = part.split("-", 2);
                out.add(new long[] { Long.decode(ab[0].trim()), Long.decode(ab[1].trim()) });
            } else {
                long v = Long.decode(part.trim());
                out.add(new long[] { v, v });
            }
        }
        return out;
    }

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 3) {
            println("usage: decomp_window.java OUTDIR WINDOWS STRINGS");
            return;
        }
        String outdir = args[0];
        List<long[]> wins = parseWindows(args[1]);
        List<String> pats = new ArrayList<>();
        if (!args[2].equals("-")) {
            for (String p : args[2].split(",")) {
                if (!p.isEmpty()) pats.add(p);
            }
        }
        new File(outdir).mkdirs();

        Program prog = currentProgram;
        TreeMap<Long, Function> targets = new TreeMap<>();
        FunctionManager fm = prog.getFunctionManager();

        FunctionIterator it = fm.getFunctions(true);
        while (it.hasNext()) {
            Function f = it.next();
            long ea = f.getEntryPoint().getOffset();
            for (long[] w : wins) {
                if (ea >= w[0] && ea <= w[1]) {
                    targets.put(ea, f);
                    break;
                }
            }
        }

        byte[][] needles = new byte[pats.size()][];
        for (int i = 0; i < pats.size(); i++) needles[i] = pats.get(i).getBytes("US-ASCII");

        for (int pi = 0; pi < needles.length; pi++) {
            int found = 0;
            Address addr = prog.getMinAddress();
            while (found < 500 && !monitor.isCancelled()) {
                addr = prog.getMemory().findBytes(addr, needles[pi], null, true, monitor);
                if (addr == null) break;
                for (Reference ref : prog.getReferenceManager().getReferencesTo(addr)) {
                    Function fn = fm.getFunctionContaining(ref.getFromAddress());
                    if (fn != null) targets.put(fn.getEntryPoint().getOffset(), fn);
                }
                found++;
                addr = addr.add(1);
            }
            println("pattern [" + pats.get(pi) + "]: " + found + " hits");
        }

        println("targets: " + targets.size() + " functions");
        DecompInterface ifc = new DecompInterface();
        ifc.openProgram(prog);

        PrintWriter index = new PrintWriter(new File(outdir, "_index.txt"));
        index.println("entry_va\tname\tsize_bytes\tdecomp_len");
        for (long ea : targets.keySet()) {
            Function fn = targets.get(ea);
            String text;
            DecompileResults res = ifc.decompileFunction(fn, 240, new ConsoleTaskMonitor());
            if (res.decompileCompleted()) {
                text = res.getDecompiledFunction().getC();
            } else {
                text = "// decompile FAILED: " + res.getErrorMessage() + "\n";
            }
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
