
package counter_verif_pkg;
   import uvm_pkg::*;
   `include "uvm_macros.svh"

   `include "counter_transaction.sv"
   `include "counter_sequencer.sv"
   `include "counter_driver.sv"
   `include "counter_monitor.sv"
   `include "counter_agent.sv"
   `include "counter_scoreboard.sv"
   `include "counter_env.sv"
   `include "counter_reset_sequence.sv"
   `include "counter_write_sequence.sv"
   `include "counter_test.sv"

endpackage
