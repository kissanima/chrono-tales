/* Ghidra headless post-script (Java).
 * Decompile the function containing (or immediately preceding) each address.
 *
 * Usage:
 *   -postScript decomp_at.java OUTDIR 0x4ee403[,0x4ee4fb...]
 */
import java.io.File;
import java.io.PrintWriter;
import java.util.TreeMap;

import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;
import ghidra.program.model.listing.FunctionManager;
import ghidra.program.model.listing.Program;
import ghidra.util.task.ConsoleTaskMonitor;

public class decomp_at extends GhidraScript {

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 2) {
            println("usage: decomp_at.java OUTDIR 0xADDR[,0xADDR...]");
            return;
        }
        String outdir = args[0];
        new File(outdir).mkdirs();

        Program prog = currentProgram;
        FunctionManager fm = prog.getFunctionManager();
        TreeMap<Long, Function> targets = new TreeMap<>();

        for (String spec : args[1].split(",")) {
            long va = Long.decode(spec.trim());
            Address a = toAddr(va);
            Function fn = fm.getFunctionContaining(a);
            if (fn == null) {
                // no function body covers the address: take the nearest
                // preceding entry point (Ghidra left this region undefined)
                long bestEa = -1;
                FunctionIterator it = fm.getFunctions(true);
                while (it.hasNext()) {
                    Function f = it.next();
                    long ea = f.getEntryPoint().getOffset();
                    if (ea <= va && ea > bestEa) {
                        bestEa = ea;
                        fn = f;
                    }
                }
            }
            if (fn == null) {
                println("no function for " + spec);
                continue;
            }
            println(spec + " -> " + fn.getName() + " @ " + fn.getEntryPoint()
                    + " (size " + fn.getBody().getNumAddresses() + ")");
            targets.put(fn.getEntryPoint().getOffset(), fn);
        }

        DecompInterface ifc = new DecompInterface();
        ifc.openProgram(prog);
        for (long ea : targets.keySet()) {
            Function fn = targets.get(ea);
            DecompileResults res = ifc.decompileFunction(fn, 240, new ConsoleTaskMonitor());
            String text = res.decompileCompleted()
                    ? res.getDecompiledFunction().getC()
                    : "// decompile FAILED: " + res.getErrorMessage() + "\n";
            String safe = fn.getName().replaceAll("[^A-Za-z0-9_.]", "_");
            PrintWriter pw = new PrintWriter(
                    new File(outdir, String.format("func_%08x_%s.c", ea, safe)));
            pw.println(String.format("// %s  entry=0x%x  size=%d", fn.getName(), ea,
                    fn.getBody().getNumAddresses()));
            pw.print(text);
            pw.close();
        }
        println("wrote " + targets.size() + " functions");
    }
}
