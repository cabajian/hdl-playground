module tb_counter;

   logic       clk;
   logic       rst_n;
   logic [3:0] count;

   // Instantiate the counter
   counter dut (
       .clk  (clk),
       .rst_n(rst_n),
       .count(count)
   );

   // Clock generation
   initial begin
      clk = 0;
      forever #5 clk = ~clk;
   end

   // Test sequence
   initial begin
      $display("Starting counter simulation...");
      rst_n = 0;
      #20;
      rst_n = 1;

      // Monitor the counter value
      repeat (20) begin
         @(posedge clk);
         $display("Time: %0t | Count: %d", $time, count);
      end

      $display("Simulation finished.");
      $finish;
   end

endmodule
