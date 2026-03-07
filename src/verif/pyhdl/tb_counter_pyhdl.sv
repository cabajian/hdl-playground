module tb_counter_pyhdl;
   import pyhdl_if::*;
   import tb_counter_pyhdl_api_pkg::*;
   import counter_test_pkg::*;

   logic clk;
   counter_if vif ();
   assign vif.clk = clk;

   // Instantiate the counter and connect via interface
   counter dut (
       .clk   (clk),
       .rst_n (vif.rst_n),
       .wr_en (vif.wr_en),
       .data_i(vif.data_i),
       .data_o(vif.data_o),
       .count (vif.count)
   );

   // Clock generation
   initial begin
      clk = 0;
      forever #5 clk = ~clk;
   end

`ifdef WAVES
   initial begin
      $dumpfile(`VCD_FILE);
      $dumpvars(0, tb_counter_pyhdl);
   end
`endif

   // Test sequence
   initial begin
      automatic TestRunnerAPI_exp_impl py_runner;
      automatic counter_test test;

      $display("[%0t] Starting counter simulation with pyhdl-if...", $time);

      // Start PyHDL-IF
      pyhdl_if_start();

      // Wait for signals to settle
      #1;

      // Instantiate and call the Python API
      py_runner = new();

      // Set up the implementation for Python to call back
      test = new(vif.tb);

      $display("[%0t] Calling start_test from Python...", $time);
      py_runner.start_test(test.api.m_obj);

      $display("[%0t] Simulation finished in SV.", $time);
      $finish;
   end

endmodule
