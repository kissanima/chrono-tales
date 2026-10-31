/* Ghidra headless post-script (Java).
 * Linear-sweep disassembly over an address range and print it.  Useful for
 * regions the auto analysis never reached, where we need to see where a
 * function actually starts before we can create it.
 *
 * Usage:
 *   -postScript dis_range.java 0x4ee400 0x4ee700
 */
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.Listing;
import ghidra.program.model.mem.Memory;

public class dis_range extends GhidraScript {

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 2) {
            println("usage: dis_range.java START END");
            return;
        }
        long start = Long.decode(args[0].trim());
        long end = Long.decode(args[1].trim());
        Listing listing = currentProgram.getListing();
        Memory mem = currentProgram.getMemory();
        Address a = toAddr(start);
        Address stop = toAddr(end);
        int n = 0;
        while (a.getOffset() < stop.getOffset() && n < 4000) {
            if (getMonitor().isCancelled()) {
                break;
            }
            // only disassemble where nothing is defined yet
            if (listing.getInstructionAt(a) == null) {
                try {
                    disassemble(a);
                }
                catch (Exception e) {
                    // ignore, just skip a byte
                }
            }
            Instruction ins = listing.getInstructionAt(a);
            if (ins == null) {
                a = a.add(1);
                continue;
            }
            String bytes = "";
            try {
                byte[] b = new byte[ins.getLength()];
                mem.getBytes(a, b);
                StringBuilder sb = new StringBuilder();
                for (byte x : b) {
                    sb.append(String.format("%02x ", x & 0xff));
                }
                bytes = sb.toString().trim();
            }
            catch (Exception e) {
                bytes = "??";
            }
            println(String.format("%08x  %-24s %s", a.getOffset(), bytes,
                    ins.toString()));
            a = a.add(ins.getLength());
            n++;
        }
        println("// " + n + " instructions");
    }
}
