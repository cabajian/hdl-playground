module tb_counter;

   logic       clk;
   logic       rst_n;
   logic [7:0] addr;
   logic       wr_en;
   wire  [7:0] data;
   logic [3:0] count;   // To monitor output port directly
   logic [7:0] wdata;   // To drive data bus during write

   // Bidirectional data bus control
   assign data = (wr_en) ? wdata : 8'bz;

   // Instantiate the counter
   counter dut (
       .clk  (clk),
       .rst_n(rst_n),
       .addr (addr),
       .wr_en(wr_en),
       .data (data),
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
      
      // Initialize inputs
      rst_n = 0;
      addr = 0;
      wr_en = 0;
      wdata = 0;
      
      #20;
      rst_n = 1;
      
      // 1. Verify simple counting (no register interaction)
      $display("Waiting for counter increment...");
      repeat (5) @(posedge clk);
      $display("Count is %d", count);

      // 2. Register Write Test: Set count to 4'hA (10)
      $display("Writing 0xA to counter...");
      @(negedge clk);
      addr = 8'h00;
      wr_en = 1;
      wdata = 8'h0A;
      @(negedge clk);
      wr_en = 0; // End write
      wdata = 8'h00; 

      @(posedge clk); // Allow update
      if (count !== 4'hA) $error("Write Failed! Expected 10, got %d", count);
      else $display("Write Success: Count set to %d", count);

      // 3. Register Read Test
      $display("Reading counter value...");
      @(negedge clk);
      addr = 8'h00;
      wr_en = 0;
      #1; // Wait for data to drive
      if (data !== {4'h0, count}) $error("Read Failed! Expected %d, got %d", count, data);
      else $display("Read Success: Data bus saw %d", data);

      // 4. Verify counter continues from new value
      repeat (2) @(posedge clk);
      $display("Count after 2 cycles: %d", count);
      
      $display("Simulation finished.");
      $finish;
   end

endmodule
