module tb_ether_pyhdl;
   import pyhdl_if::*;
   import tb_ether_pyhdl_api_pkg::*;
   import ether_test_pkg::*;

   logic clk;
   ether_if vif ();
   assign vif.clk = clk;

   // Instantiate the ether module and connect via interface
   ether dut (
       .i_clk      (clk),
       .i_rst      (vif.rst),
       .i_start    (vif.start),
       .i_valid    (vif.valid),
       .i_num_bytes(vif.num_bytes),
       .i_data     (vif.data),

       .o_valid        (vif.o_valid),
       .o_dst_mac      (vif.o_dst_mac),
       .o_src_mac      (vif.o_src_mac),
       .o_ethertype    (vif.o_ethertype),
       .o_payload      (vif.o_payload),
       .o_payload_bytes(vif.o_payload_bytes)
   );

   // Clock generation
   initial begin
      clk = 0;
      forever #5 clk = ~clk;
   end

`ifdef WAVES
   initial begin
      $dumpfile(`VCD_FILE);
      $dumpvars(0, tb_ether_pyhdl);
   end
`endif

   // Test sequence
   initial begin
      automatic TestRunnerAPI_exp_impl py_runner;
      automatic pyhdl_ether_test test;

      $timeformat(-9, 0, "ns");

      $display("[%0t] Starting ether simulation with pyhdl-if...", $time);

      // Start PyHDL-IF
      pyhdl_if_start();

      // Instantiate and call the Python API
      py_runner = new();

      // Set up the implementation for Python to call back
      // The pyhdl_ether_test acts as the host for SV execution logic
      test = pyhdl_ether_test::mk(vif.tb, py_runner);

      $display("[%0t] Calling start_test from Python...", $time);
      py_runner.start_test(test.api.m_obj);

      $display("[%0t] Simulation finished in SV.", $time);
      $finish;
   end

endmodule
