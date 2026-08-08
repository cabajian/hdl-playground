`include "sim_clock.svh"

module tb_ether_pyhdl;
   import pyhdl_if::*;
   import tb_ether_pyhdl_api_pkg::*;
   import ether_test_pkg::*;

   logic clk;
   ether_if vif ();
   assign vif.clk = clk;

   // Instantiate the ether module and connect via interface
   ether dut (
       .i_clk  (clk),
       .i_rst  (vif.rst),
       .i_valid(vif.valid),
       .i_data (vif.data),

       .o_pl_valid(vif.o_pl_valid),
       .o_pl_data (vif.o_pl_data),
       .o_pl_last (vif.o_pl_last),

       .o_valid        (vif.o_valid),
       .o_dst_mac      (vif.o_dst_mac),
       .o_src_mac      (vif.o_src_mac),
       .o_ethertype    (vif.o_ethertype),
       .o_payload_bytes(vif.o_payload_bytes),
       .o_vlan_valid   (vif.o_vlan_valid),
       .o_vlan_tci     (vif.o_vlan_tci),
       .o_fcs_ok       (vif.o_fcs_ok),
       .o_err_runt     (vif.o_err_runt),
       .o_err_oversize (vif.o_err_oversize)
   );

   // Clock generation
   initial begin
      clk = 0;
      forever #5 clk = ~clk;
   end

`ifdef WAVES
   string _waves_vcd;
   initial begin
      if (!$value$plusargs("waves_vcd=%s", _waves_vcd)) _waves_vcd = "waves.vcd";
      $dumpfile(_waves_vcd);
      $dumpvars(0, tb_ether_pyhdl);
   end
`endif

   // Test sequence
   initial begin
      automatic TestRunnerAPI_exp_impl py_runner;
      automatic SimClockAPI_exp_impl py_clock;
      automatic pyhdl_ether_test test;

      $timeformat(-9, 0, "ns");

      $display("[%0t] Starting ether simulation with pyhdl-if...", $time);

      // Start PyHDL-IF
      pyhdl_if_start();

      // Instantiate Python-facing APIs
      py_clock  = new();
      py_runner = new();

      // Start the background polling thread to keep the simpy clock in sync.
      // Override at runtime via +sim_clock.poll_ns=N.
      `SIM_CLOCK_START(py_clock, 1000)

      // Set up the implementation for Python to call back
      // The pyhdl_ether_test acts as the host for SV execution logic
      test = pyhdl_ether_test::mk(vif.tb, py_runner, py_clock);

      $display("[%0t] Calling start_test from Python...", $time);
      py_runner.start_test(test.api.m_obj);

      $display("[%0t] Simulation finished in SV.", $time);
      $finish;
   end

endmodule
