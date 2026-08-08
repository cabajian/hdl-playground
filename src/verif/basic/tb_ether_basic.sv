`timescale 1ns / 1ps
// Pure-SV version of the ether testbench for performance comparison against
// the pyhdl-if variant. Builds NUM_PACKETS random Ethernet frames, wraps each
// one in a preamble/SFD and a trailing FCS, sprinkles non-frame garbage bytes
// between them, streams the whole thing at the DUT as a flat byte stream, and
// checks that every frame comes back out intact.
//
// Frame flavours are mixed so the parser's header handling is exercised:
// Ethernet II (type >= 0x0600), IEEE 802.3 (length field, optionally padded),
// and 802.1Q VLAN-tagged.

module tb_ether_basic;

   localparam int NUM_PACKETS = 1000;
   localparam int MAX_PAYLOAD = 1500;
   localparam int MIN_FRAME_BYTES = 64;  // DST..FCS, for padding decisions

   logic        clk;
   logic        rst;
   logic        valid;
   logic [ 7:0] data;

   wire         o_pl_valid;
   wire  [ 7:0] o_pl_data;
   wire         o_pl_last;
   wire         o_valid;
   wire  [47:0] o_dst_mac;
   wire  [47:0] o_src_mac;
   wire  [15:0] o_ethertype;
   wire  [15:0] o_payload_bytes;
   wire         o_vlan_valid;
   wire  [15:0] o_vlan_tci;
   wire         o_fcs_ok;
   wire         o_err_runt;
   wire         o_err_oversize;

   ether dut (
       .i_clk  (clk),
       .i_rst  (rst),
       .i_valid(valid),
       .i_data (data),

       .o_pl_valid(o_pl_valid),
       .o_pl_data (o_pl_data),
       .o_pl_last (o_pl_last),

       .o_valid        (o_valid),
       .o_dst_mac      (o_dst_mac),
       .o_src_mac      (o_src_mac),
       .o_ethertype    (o_ethertype),
       .o_payload_bytes(o_payload_bytes),
       .o_vlan_valid   (o_vlan_valid),
       .o_vlan_tci     (o_vlan_tci),
       .o_fcs_ok       (o_fcs_ok),
       .o_err_runt     (o_err_runt),
       .o_err_oversize (o_err_oversize)
   );

   // Clock generation (10ns period)
   initial begin
      clk = 0;
      forever #5 clk = ~clk;
   end

   // Watchdog: a frame the DUT never retires would otherwise hang the run
   initial begin
      #500_000_000;
      $fatal(1, "ether: timeout waiting for frames (matched %0d/%0d)", num_matches, NUM_PACKETS);
   end

`ifdef WAVES
   string _waves_vcd;
   initial begin
      if (!$value$plusargs("waves_vcd=%s", _waves_vcd)) _waves_vcd = "waves.vcd";
      $dumpfile(_waves_vcd);
      $dumpvars(0, tb_ether_basic);
   end
`endif

   // -------------------------------------------------------------------------
   // Expected frame (one in flight at a time) and monitor bookkeeping
   // -------------------------------------------------------------------------
   bit           [47:0] exp_dst_mac;
   bit           [47:0] exp_src_mac;
   bit           [15:0] exp_ethertype;
   bit                  exp_vlan;
   bit           [15:0] exp_vlan_tci;
   byte unsigned        exp_payload   [$];

   byte unsigned        got_payload   [$];
   byte unsigned        stream        [$];
   int                  num_matches;
   int                  pending;

   // -------------------------------------------------------------------------
   // CRC-32 (IEEE 802.3): reflected, poly 0x04C11DB7, init/final 0xFFFFFFFF
   // -------------------------------------------------------------------------
   function automatic bit [31:0] crc32_of(input byte unsigned bytes[$]);
      bit [31:0] c = 32'hFFFF_FFFF;
      foreach (bytes[i]) begin
         c ^= {24'h0, bytes[i]};
         for (int b = 0; b < 8; b++) begin
            c = c[0] ? ((c >> 1) ^ 32'hEDB8_8320) : (c >> 1);
         end
      end
      return ~c;
   endfunction

   // -------------------------------------------------------------------------
   // Build one framed packet -- garbage, preamble, SFD, header, payload,
   // optional pad, FCS -- and append it to `stream`. Also records what the DUT
   // is expected to report for it.
   // -------------------------------------------------------------------------
   task automatic build_frame(input int flavour);
      byte unsigned body[$];  // DST..pad, i.e. everything the FCS covers
      bit [31:0] fcs;
      int payload_len;
      int garbage_len;
      byte unsigned g;
      int pad_len;

      // Inter-frame filler. Never 0x55, so it can never look like a preamble.
      // A length of zero puts this frame back-to-back with the previous one.
      garbage_len = $urandom_range(0, 8);
      for (int i = 0; i < garbage_len; i++) begin
         g = byte'($urandom);
         if (g == 8'h55) g = 8'hA5;
         stream.push_back(g);
      end

      exp_dst_mac  = {$urandom, $urandom} & 48'hFFFF_FFFF_FFFF;
      exp_src_mac  = {$urandom, $urandom} & 48'hFFFF_FFFF_FFFF;
      exp_vlan     = 1'b0;
      exp_vlan_tci = 16'h0000;
      payload_len  = $urandom_range(0, MAX_PAYLOAD);

      exp_payload.delete();
      for (int i = 0; i < payload_len; i++) exp_payload.push_back(byte'($urandom));

      // Header: DST + SRC, then a type/length field that depends on the flavour
      for (int i = 5; i >= 0; i--) body.push_back(exp_dst_mac[i*8+:8]);
      for (int i = 5; i >= 0; i--) body.push_back(exp_src_mac[i*8+:8]);

      case (flavour)
         0: begin  // Ethernet II
            case ($urandom_range(
                0, 3
            ))
               0: exp_ethertype = 16'h0800;  // IPv4
               1: exp_ethertype = 16'h86DD;  // IPv6
               2: exp_ethertype = 16'h0806;  // ARP
               default: exp_ethertype = 16'h9000;  // loopback
            endcase
         end
         1: begin  // IEEE 802.3: the field carries the payload length
            exp_ethertype = 16'(payload_len);
         end
         default: begin  // 802.1Q VLAN-tagged
            exp_vlan      = 1'b1;
            exp_vlan_tci  = 16'($urandom);
            exp_ethertype = 16'h0800;
            body.push_back(8'h81);
            body.push_back(8'h00);
            body.push_back(exp_vlan_tci[15:8]);
            body.push_back(exp_vlan_tci[7:0]);
         end
      endcase

      body.push_back(exp_ethertype[15:8]);
      body.push_back(exp_ethertype[7:0]);
      foreach (exp_payload[i]) body.push_back(exp_payload[i]);

      // 802.3 frames may be padded out to the 64-byte minimum. The pad sits
      // outside the length field, so the DUT must consume it for the CRC but
      // leave it off the payload stream.
      if (flavour == 1) begin
         pad_len = MIN_FRAME_BYTES - 4 - body.size();
         if (pad_len > 0) begin
            for (int i = 0; i < pad_len; i++) body.push_back(byte'($urandom));
         end
      end

      fcs = crc32_of(body);

      // Preamble (7x 0x55) + SFD
      for (int i = 0; i < 7; i++) stream.push_back(8'h55);
      stream.push_back(8'hD5);
      foreach (body[i]) stream.push_back(body[i]);
      // FCS is appended little-endian
      for (int i = 0; i < 4; i++) stream.push_back(fcs[i*8+:8]);
   endtask

   // -------------------------------------------------------------------------
   // Driver: push `stream` at the DUT one byte per clock, with occasional
   // stalls to prove that gaps do not break a frame in progress.
   // -------------------------------------------------------------------------
   task automatic drive_stream();
      foreach (stream[i]) begin
         valid <= 1'b1;
         data  <= stream[i];
         @(posedge clk);
         if ($urandom_range(0, 63) == 0) begin
            valid <= 1'b0;
            data  <= 8'h00;
            repeat ($urandom_range(1, 3)) @(posedge clk);
         end
      end
      valid <= 1'b0;
      data  <= 8'h00;
      @(posedge clk);
      stream.delete();
   endtask

   // -------------------------------------------------------------------------
   // Monitor: reassemble the payload stream and check each extracted frame
   // -------------------------------------------------------------------------
   task automatic monitor_loop();
      forever begin
         @(posedge clk);

         if (o_pl_valid === 1'b1) got_payload.push_back(o_pl_data);

         if (o_valid === 1'b1) begin
            bit ok = 1'b1;

            if (!o_fcs_ok || o_err_oversize) begin
               $display("[%0t] BAD FRAME: fcs_ok=%0b oversize=%0b", $time, o_fcs_ok,
                        o_err_oversize);
               ok = 1'b0;
            end
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
            if (o_vlan_valid !== exp_vlan) begin
               $display("[%0t] MISMATCH vlan_valid: exp=%0b got=%0b", $time, exp_vlan,
                        o_vlan_valid);
               ok = 1'b0;
            end
            if (exp_vlan && (o_vlan_tci !== exp_vlan_tci)) begin
               $display("[%0t] MISMATCH vlan_tci: exp=%04h got=%04h", $time, exp_vlan_tci,
                        o_vlan_tci);
               ok = 1'b0;
            end
            if (int'(o_payload_bytes) !== exp_payload.size()) begin
               $display("[%0t] MISMATCH payload_bytes: exp=%0d got=%0d", $time, exp_payload.size(),
                        o_payload_bytes);
               ok = 1'b0;
            end
            if (got_payload.size() !== exp_payload.size()) begin
               $display("[%0t] MISMATCH streamed payload length: exp=%0d got=%0d", $time,
                        exp_payload.size(), got_payload.size());
               ok = 1'b0;
            end else begin
               foreach (exp_payload[i]) begin
                  if (got_payload[i] !== exp_payload[i]) begin
                     $display("[%0t] MISMATCH payload[%0d]: exp=%02h got=%02h", $time, i,
                              exp_payload[i], got_payload[i]);
                     ok = 1'b0;
                     break;
                  end
               end
            end

            if (ok) num_matches++;
            got_payload.delete();
            pending = 0;
         end
      end
   endtask

   // -------------------------------------------------------------------------
   // Test sequence
   // -------------------------------------------------------------------------
   initial begin
      $timeformat(-9, 0, "ns");
      $display("[%0t] Starting basic ether simulation...", $time);

      num_matches = 0;
      pending     = 0;

      rst         = 1;
      valid       = 0;
      data        = 0;
      repeat (4) @(posedge clk);
      rst = 0;
      @(posedge clk);

      fork
         monitor_loop();
      join_none

      for (int p = 0; p < NUM_PACKETS; p++) begin
         build_frame(p % 3);
         pending = 1;
         $display("[%0t] Sending packet %0d (payload_len=%0d, flavour=%0d)...", $time, p,
                  exp_payload.size(), p % 3);
         drive_stream();

         // Let the monitor retire the frame before the next one is built
         while (pending != 0) @(posedge clk);
      end

      $display("[%0t] Matched %0d/%0d packets", $time, num_matches, NUM_PACKETS);
      if (num_matches != NUM_PACKETS) begin
         $fatal(1, "ether: only %0d/%0d packets matched", num_matches, NUM_PACKETS);
      end
      $display("[%0t] Simulation finished in SV.", $time);
      $finish;
   end

endmodule
