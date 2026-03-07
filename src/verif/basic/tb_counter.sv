module tb_counter;

   logic       clk;
   logic       rst_n;
   logic       wr_en;
   logic [7:0] data_i;
   logic [7:0] data_o;
   logic [3:0] count;   // To monitor output port directly

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

   // Test sequence
   initial begin
      $display("Starting counter simulation...");
      
      // Initialize inputs
      rst_n  = 0;
      wr_en  = 0;
      data_i = 0;
      
      #20;
      rst_n = 1;
      
      // 1. Verify simple counting (no register interaction)
      $display("Waiting for counter increment...");
      repeat (5) @(posedge clk);
      $display("Count is %d", count);

      // 2. Register Write Test: Set count to 4'hA (10)
      $display("Writing 0xA to counter...");
      @(negedge clk);
      wr_en  = 1;
      data_i = 8'h0A;
      @(negedge clk);
      wr_en  = 0; // End write
      data_i = 8'h00; 

      @(posedge clk); // Allow update
      if (count !== 4'hA) $error("Write Failed! Expected 10, got %d", count);
      else $display("Write Success: Count set to %d", count);

      // 3. Register Read Test
      $display("Reading counter value...");
      @(negedge clk);
      wr_en = 0;
      #1; // Wait for data to drive
      if (data_o !== {4'h0, count}) $error("Read Failed! Expected %d, got %d", count, data_o);
      else $display("Read Success: data_o saw %d", data_o);

      // 4. Verify counter continues from new value
      repeat (2) @(posedge clk);
      $display("Count after 2 cycles: %d", count);
      
      $display("Simulation finished.");
      $finish;
   end

endmodule
