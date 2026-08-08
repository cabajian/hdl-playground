`timescale 1ns / 1ps
`include "uvm_macros.svh"

// Top for the TCP co-simulation testbench: two byte-stream interfaces
// cross-wired so side A's TX is side B's RX and vice versa. No RTL DUT -
// the SystemVerilog is pure transport between two Python TCP engines.
module tb_tcp_uvm_pyhdl;
   import uvm_pkg::*;
   import tcp_verif_pkg::*;

   logic clk;

   tcp_if a_if (clk);
   tcp_if b_if (clk);

   // Cross-wire the two sides
   assign a_if.rx_valid = b_if.tx_valid;
   assign a_if.rx_data  = b_if.tx_data;
   assign a_if.rx_last  = b_if.tx_last;
   assign b_if.rx_valid = a_if.tx_valid;
   assign b_if.rx_data  = a_if.tx_data;
   assign b_if.rx_last  = a_if.tx_last;

   // Clock generation (10ns period)
   initial begin
      clk = 0;
      forever #5 clk = ~clk;
   end

`ifdef WAVES
   string _waves_vcd;
   initial begin
      if (!$value$plusargs("waves_vcd=%s", _waves_vcd)) _waves_vcd = "waves.vcd";
      $dumpfile(_waves_vcd);
      $dumpvars(0, tb_tcp_uvm_pyhdl);
   end
`endif

   initial begin
      $timeformat(-9, 0, "ns");
      uvm_config_db#(virtual tcp_if)::set(null, "uvm_test_top.env.agent_a.*", "vif", a_if);
      uvm_config_db#(virtual tcp_if)::set(null, "uvm_test_top.env.agent_b.*", "vif", b_if);
      // Override with +UVM_TESTNAME=<test>
      run_test("tcp_smoke_test");
   end

endmodule
