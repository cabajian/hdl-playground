module tb_counter;

   logic       clk;
   logic       rst_n;
   logic       wr_en;
   logic [3:0] data_i;
   logic [3:0] data_o;
   logic [3:0] count;

   // Instantiate the counter
   counter dut (
       .clk   (clk),
       .rst_n (rst_n),
       .wr_en (wr_en),
       .data_i(data_i),
       .data_o(data_o),
       .count (count)
   );

   // Clock generation
   initial begin
      clk = 0;
      forever #5 clk = ~clk;
   end

`ifdef WAVES
   initial begin
      $dumpfile(`VCD_FILE);
      $dumpvars(0, tb_counter);
   end
`endif

   // Helper tasks (mirrors pyhdl counter_test_pkg)
   task write_count(bit [3:0] val);
      @(negedge clk);
      wr_en  = 1;
      data_i = val;
      @(negedge clk);
      wr_en = 0;
   endtask

   task read_count(output bit [3:0] val);
      wr_en = 0;
      val   = data_o;
   endtask

   task check_count(bit [3:0] exp, bit [3:0] act);
      if (act !== exp) $error("check failed! Expected %h, got %h", exp, act);
   endtask

   // Test sequence (matches pyhdl run_test)
   initial begin
      $display("Starting counter simulation...");

      for (int seed = 0; seed < 16; seed++) begin
         // Reset
         rst_n  = 0;
         wr_en  = 0;
         data_i = 0;
         repeat (2) @(negedge clk);
         rst_n = 1;

         // Seed initial count
         write_count(4'(seed));

         // 1. Verify simple counting
         $display("[%0t] Testing simple counting (seed=%0d)...", $time, seed);
         for (int i = 0; i < 16; i++) begin
            check_count(4'(seed + i), count);
            @(negedge clk);
         end

         // 2. Register Read Test
         write_count(0);
         $display("[%0t] Testing counter reading (seed=%0d)...", $time, seed);
         for (int i = 0; i < 16; i++) begin
            bit [3:0] exp, act;
            exp = 4'(i);
            read_count(act);
            check_count(exp, act);
            @(negedge clk);
         end

         $display("[%0t] Test %0d finished.", $time, seed);
      end

      $display("Simulation finished.");
      $finish;
   end

endmodule
