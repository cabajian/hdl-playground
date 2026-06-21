// Pure-SV version of the ether testbench for performance comparison against
// the pyhdl-if variant. Generates NUM_PACKETS random packets, drives them
// through the DUT, monitors the outputs, and verifies that the reconstructed
// packet matches the stimulus.

module tb_ether_basic;

   localparam int NUM_PACKETS = 1000;
   localparam int HDR_LEN = 14;  // 6 DST + 6 SRC + 2 Ethertype
   localparam int MAX_PAYLOAD = 1500;

   logic                clk;
   logic                rst;
   logic                start;
   logic                valid;
   logic [        15:0] num_bytes;
   logic [         7:0] data;

   wire                 o_valid;
   wire  [        47:0] o_dst_mac;
   wire  [        47:0] o_src_mac;
   wire  [        15:0] o_ethertype;
   wire  [(1500*8)-1:0] o_payload;
   wire  [        15:0] o_payload_bytes;

   ether dut (
       .i_clk      (clk),
       .i_rst      (rst),
       .i_start    (start),
       .i_valid    (valid),
       .i_num_bytes(num_bytes),
       .i_data     (data),

       .o_valid        (o_valid),
       .o_dst_mac      (o_dst_mac),
       .o_src_mac      (o_src_mac),
       .o_ethertype    (o_ethertype),
       .o_payload      (o_payload),
       .o_payload_bytes(o_payload_bytes)
   );

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
      $dumpvars(0, tb_ether_basic);
   end
`endif

   // Shared state for per-packet comparison
   bit           [47:0] exp_dst_mac;
   bit           [47:0] exp_src_mac;
   bit           [15:0] exp_ethertype;
   byte unsigned        exp_payload                                              [$];
   int                  num_matches;
   int                  pending;  // driver sets, monitor clears when it consumes

   // -------------------------------------------------------------------------
   // Driver: serialize the current expected packet and drive it byte-by-byte
   // -------------------------------------------------------------------------
   task automatic drive_packet();
      byte unsigned bytes[$];
      int total;

      // Serialize header (MSB first)
      for (int i = 5; i >= 0; i--) bytes.push_back(exp_dst_mac[i*8+:8]);
      for (int i = 5; i >= 0; i--) bytes.push_back(exp_src_mac[i*8+:8]);
      for (int i = 1; i >= 0; i--) bytes.push_back(exp_ethertype[i*8+:8]);
      foreach (exp_payload[i]) bytes.push_back(exp_payload[i]);
      total = bytes.size();

      // Clear inputs and synchronize to clocking edge
      rst       <= 0;
      start     <= 0;
      valid     <= 0;
      num_bytes <= 0;
      data      <= 0;
      @(posedge clk);

      for (int i = 0; i < total; i++) begin
         valid     <= 1'b1;
         start     <= (i == 0) ? 1'b1 : 1'b0;
         num_bytes <= 16'(total);
         data      <= bytes[i];
         @(posedge clk);
      end

      valid <= 1'b0;
      start <= 1'b0;
      @(posedge clk);

      // Allow monitor time to latch o_valid and settle
      repeat (5) @(posedge clk);
   endtask

   // -------------------------------------------------------------------------
   // Monitor: on o_valid, compare DUT outputs against the expected frame
   // -------------------------------------------------------------------------
   task automatic monitor_loop();
      forever begin
         @(posedge clk);
         if (o_valid === 1'b1) begin
            int payload_len = int'(o_payload_bytes);
            bit ok = 1'b1;

            if (o_dst_mac !== exp_dst_mac) begin
               $display("[%0t] MISMATCH dst_mac: exp=%012h got=%012h", $time, exp_dst_mac,
                        o_dst_mac);
               ok = 1'b0;
            end
            if (o_src_mac !== exp_src_mac) begin
               $display("[%0t] MISMATCH src_mac: exp=%012h got=%012h", $time, exp_src_mac,
                        o_src_mac);
               ok = 1'b0;
            end
            if (o_ethertype !== exp_ethertype) begin
               $display("[%0t] MISMATCH ethertype: exp=%04h got=%04h", $time, exp_ethertype,
                        o_ethertype);
               ok = 1'b0;
            end
            if (payload_len !== exp_payload.size()) begin
               $display("[%0t] MISMATCH payload_len: exp=%0d got=%0d", $time, exp_payload.size(),
                        payload_len);
               ok = 1'b0;
            end else begin
               for (int i = 0; i < payload_len; i++) begin
                  byte unsigned got = o_payload[(1499-i)*8+:8];
                  if (got !== exp_payload[i]) begin
                     $display("[%0t] MISMATCH payload[%0d]: exp=%02h got=%02h", $time, i,
                              exp_payload[i], got);
                     ok = 1'b0;
                     break;
                  end
               end
            end

            if (ok) num_matches++;
            pending = 0;
         end
      end
   endtask

   // -------------------------------------------------------------------------
   // Test sequence
   // -------------------------------------------------------------------------
   initial begin
      int payload_len;

      $timeformat(-9, 0, "ns");
      $display("[%0t] Starting basic ether simulation...", $time);

      num_matches = 0;
      pending     = 0;

      // Global reset
      rst         = 1;
      start       = 0;
      valid       = 0;
      num_bytes   = 0;
      data        = 0;
      repeat (4) @(posedge clk);
      rst = 0;
      @(posedge clk);

      fork
         monitor_loop();
      join_none

      for (int p = 0; p < NUM_PACKETS; p++) begin
         // Randomize expected packet
         exp_dst_mac   = {$urandom, $urandom} & 48'hFFFF_FFFF_FFFF;
         exp_src_mac   = {$urandom, $urandom} & 48'hFFFF_FFFF_FFFF;
         exp_ethertype = 16'h9000;
         payload_len   = $urandom_range(0, MAX_PAYLOAD);
         exp_payload.delete();
         for (int i = 0; i < payload_len; i++) begin
            exp_payload.push_back(byte'($urandom));
         end

         pending = 1;
         $display("[%0t] Sending packet %0d (payload_len=%0d)...", $time, p, payload_len);
         drive_packet();
      end

      $display("[%0t] Matched %0d/%0d packets", $time, num_matches, NUM_PACKETS);
      $display("[%0t] Simulation finished in SV.", $time);
      $finish;
   end

endmodule
