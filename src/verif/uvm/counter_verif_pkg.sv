
package counter_verif_pkg;
  import uvm_pkg::*;
  import counter_ral_pkg::*;
  import dv_lib_pkg::*;
  `include "uvm_macros.svh"

  `include "counter_transaction.sv"
  `include "counter_agent_cfg.sv"
  `include "counter_env_cfg.sv"
  `include "counter_reg_adapter.sv"
  `include "counter_sequencer.sv"
  `include "counter_driver.sv"
  `include "counter_monitor.sv"
  `include "counter_agent.sv"
  `include "counter_scoreboard.sv"
  `include "counter_env.sv"
  `include "counter_reset_sequence.sv"
  `include "counter_ral_sequence.sv"
  `include "counter_test.sv"

endpackage
