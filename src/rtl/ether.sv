`timescale 1ns / 1ps
//
// ether: Ethernet frame extractor (MAC receive path).
//
// Consumes an arbitrary byte stream and pulls Ethernet frames out of it. The
// module hunts for the preamble/SFD, parses the MAC header (including an
// optional 802.1Q VLAN tag), streams the payload out byte-by-byte, and
// validates the frame check sequence. Bytes that are not part of a frame --
// inter-frame gap, idle, or arbitrary garbage -- are skipped.
//
// Frames are self-delimiting: the running CRC-32 register reaches a fixed
// residue exactly when the four bytes just consumed form a valid FCS over
// everything since the SFD. No externally supplied frame length is needed, and
// frames may be back-to-back with no gap between them.
//
// i_valid gates the stream. Deasserting it stalls the parser rather than
// terminating the frame in progress, so a bursty producer is fine.
//
module ether #(
    // Largest DST..FCS byte count accepted before a frame is abandoned.
    // 1518 = 14B header + 1500B payload + 4B FCS; 1522 leaves room for a VLAN tag.
    parameter int MAX_FRAME_BYTES  = 1522,
    // Smallest DST..FCS byte count considered a legal frame (IEEE 802.3).
    parameter int MIN_FRAME_BYTES  = 64,
    // Consecutive 0x55 bytes required before an SFD is honoured. The standard
    // preamble is seven; allowing fewer tolerates preamble shrinkage, allowing
    // more reduces false locks on random data.
    parameter int MIN_PREAMBLE_LEN = 4
) (
    input logic i_clk,
    input logic i_rst,  // synchronous, active high

    // Byte stream in
    input logic       i_valid,
    input logic [7:0] i_data,

    // Payload stream out. One byte per beat; o_pl_last marks the final payload
    // byte of a frame. 802.3 padding beyond the length field is not emitted.
    output logic       o_pl_valid,
    output logic [7:0] o_pl_data,
    output logic       o_pl_last,

    // Frame result. o_valid is a one-cycle pulse marking the end of an
    // extraction attempt; every other output below is held stable until the
    // next one completes.
    output logic        o_valid,
    output logic [47:0] o_dst_mac,
    output logic [47:0] o_src_mac,
    output logic [15:0] o_ethertype,      // resolved inner type/length, past any VLAN tag
    output logic [15:0] o_payload_bytes,
    output logic        o_vlan_valid,
    output logic [15:0] o_vlan_tci,       // PCP/DEI/VID, valid when o_vlan_valid
    output logic        o_fcs_ok,         // FCS verified; frame is good
    output logic        o_err_runt,       // frame shorter than MIN_FRAME_BYTES
    output logic        o_err_oversize    // abandoned: no valid FCS within MAX_FRAME_BYTES
);

   // IEEE 802.3 framing constants
   localparam logic [7:0] PREAMBLE_BYTE = 8'h55;
   localparam logic [7:0] SFD_BYTE = 8'hD5;
   localparam logic [15:0] VLAN_TPID = 16'h8100;
   localparam logic [15:0] MAX_LEN_FIELD = 16'd1500;  // type/len <= this means 802.3 length
   localparam int FCS_BYTES = 4;

   // Running (pre-final-XOR) CRC-32 value reached after clocking a message
   // followed by its own little-endian FCS. Reaching it means "the last four
   // bytes were a valid FCS", which is what closes a frame.
   localparam logic [31:0] CRC32_INIT = 32'hFFFF_FFFF;
   localparam logic [31:0] CRC32_RESIDUE = 32'hDEBB_20E3;
   localparam logic [31:0] CRC32_POLY = 32'hEDB8_8320;  // 0x04C11DB7 reflected

   typedef enum logic [2:0] {
      S_HUNT,    // searching for preamble/SFD
      S_DST,     // destination MAC (6B)
      S_SRC,     // source MAC (6B)
      S_TYPE,    // EtherType/length (2B); re-entered for the inner type after a VLAN tag
      S_VLAN,    // 802.1Q TCI (2B)
      S_PAYLOAD  // payload, padding and FCS
   } state_e;

   state_e        state;

   logic   [31:0] crc;
   logic   [31:0] crc_nxt;
   logic          crc_hit;  // residue reached by consuming this byte

   logic   [ 2:0] pre_cnt;  // consecutive preamble bytes seen (saturating)
   logic   [ 3:0] hdr_cnt;  // byte index within the current header field
   logic   [15:0] frame_bytes;  // bytes consumed since the SFD, including FCS
   logic   [15:0] pl_cnt;  // payload bytes emitted so far

   // Trailing-byte holdback. Payload and FCS bytes look identical on the wire,
   // so the last four bytes are always held back: whatever falls out the far
   // end is known to be payload, and whatever remains at frame end is the FCS.
   logic   [31:0] dly;  // dly[31:24] oldest .. dly[7:0] newest
   logic   [ 2:0] dly_cnt;

   logic          len_mode;  // type field was an 802.3 length
   logic   [15:0] len_field;
   logic          vlan_seen;

   // Header shift registers, filled MSB-first as bytes arrive
   logic   [47:0] dst_sr;
   logic   [47:0] src_sr;
   logic   [15:0] type_sr;
   logic   [15:0] tci_sr;

   logic   [15:0] type_nxt;  // type/length completing on this byte
   logic          pl_emit;  // a payload byte leaves the holdback this cycle
   logic          pl_last_w;  // ...and it is the frame's final payload byte
   logic          oversize_w;
   logic   [15:0] frame_bytes_nxt;
   logic   [15:0] pl_cnt_nxt;

   // ---------------------------------------------------------------------
   // CRC-32 (IEEE 802.3): reflected, poly 0x04C11DB7, init/final 0xFFFFFFFF.
   // ---------------------------------------------------------------------
   function automatic logic [31:0] crc32_next(input logic [31:0] crc_in, input logic [7:0] data);
      logic [31:0] c;
      c = crc_in ^ {24'h0, data};
      for (int i = 0; i < 8; i++) begin
         c = c[0] ? ((c >> 1) ^ CRC32_POLY) : (c >> 1);
      end
      return c;
   endfunction

   assign crc_nxt = crc32_next(crc, i_data);
   assign crc_hit = (state == S_PAYLOAD) && (crc_nxt == CRC32_RESIDUE);

   assign type_nxt = {type_sr[7:0], i_data};

   assign frame_bytes_nxt = frame_bytes + 16'd1;

   // Once the holdback is full every new byte pushes a known-payload byte out.
   // In 802.3 length mode the payload stops at the length field, so any padding
   // that follows is consumed for the CRC but not emitted.
   assign pl_emit         = (state == S_PAYLOAD) && (dly_cnt == 3'(FCS_BYTES))
                            && !(len_mode && (pl_cnt >= len_field));
   assign pl_cnt_nxt = pl_cnt + (pl_emit ? 16'd1 : 16'd0);
   assign pl_last_w = crc_hit || (len_mode && (pl_cnt_nxt == len_field));

   // Strictly greater, so a frame that is exactly MAX_FRAME_BYTES long still
   // gets the chance to close on its FCS.
   assign oversize_w = (state != S_HUNT) && (frame_bytes_nxt > 16'(MAX_FRAME_BYTES));

   always_ff @(posedge i_clk) begin
      // Single-cycle pulses default low
      o_valid    <= 1'b0;
      o_pl_valid <= 1'b0;
      o_pl_last  <= 1'b0;

      if (i_rst) begin
         state           <= S_HUNT;
         pre_cnt         <= '0;
         hdr_cnt         <= '0;
         frame_bytes     <= '0;
         pl_cnt          <= '0;
         dly             <= '0;
         dly_cnt         <= '0;
         crc             <= CRC32_INIT;
         len_mode        <= 1'b0;
         len_field       <= '0;
         vlan_seen       <= 1'b0;
         dst_sr          <= '0;
         src_sr          <= '0;
         type_sr         <= '0;
         tci_sr          <= '0;
         o_pl_data       <= '0;
         o_dst_mac       <= '0;
         o_src_mac       <= '0;
         o_ethertype     <= '0;
         o_payload_bytes <= '0;
         o_vlan_valid    <= 1'b0;
         o_vlan_tci      <= '0;
         o_fcs_ok        <= 1'b0;
         o_err_runt      <= 1'b0;
         o_err_oversize  <= 1'b0;
      end else if (i_valid) begin
         // Every in-frame byte advances the CRC and the frame length counter.
         if (state != S_HUNT) begin
            crc         <= crc_nxt;
            frame_bytes <= frame_bytes_nxt;
         end

         unique case (state)
            // -----------------------------------------------------------
            S_HUNT: begin
               if ((i_data == SFD_BYTE) && (pre_cnt >= 3'(MIN_PREAMBLE_LEN))) begin
                  // SFD consumed; the frame body starts with the next byte.
                  state       <= S_DST;
                  hdr_cnt     <= '0;
                  frame_bytes <= '0;
                  pl_cnt      <= '0;
                  dly_cnt     <= '0;
                  crc         <= CRC32_INIT;
                  vlan_seen   <= 1'b0;
                  len_mode    <= 1'b0;
                  pre_cnt     <= '0;
               end else if (i_data == PREAMBLE_BYTE) begin
                  if (pre_cnt != 3'd7) pre_cnt <= pre_cnt + 3'd1;
               end else begin
                  pre_cnt <= '0;
               end
            end

            // -----------------------------------------------------------
            S_DST: begin
               dst_sr <= {dst_sr[39:0], i_data};
               if (hdr_cnt == 4'd5) begin
                  hdr_cnt <= '0;
                  state   <= S_SRC;
               end else begin
                  hdr_cnt <= hdr_cnt + 4'd1;
               end
            end

            // -----------------------------------------------------------
            S_SRC: begin
               src_sr <= {src_sr[39:0], i_data};
               if (hdr_cnt == 4'd5) begin
                  hdr_cnt <= '0;
                  state   <= S_TYPE;
               end else begin
                  hdr_cnt <= hdr_cnt + 4'd1;
               end
            end

            // -----------------------------------------------------------
            S_TYPE: begin
               type_sr <= type_nxt;
               if (hdr_cnt == 4'd1) begin
                  hdr_cnt <= '0;
                  if (!vlan_seen && (type_nxt == VLAN_TPID)) begin
                     // 802.1Q tag: capture the TCI, then read the inner type.
                     vlan_seen <= 1'b1;
                     state     <= S_VLAN;
                  end else begin
                     state     <= S_PAYLOAD;
                     len_mode  <= (type_nxt <= MAX_LEN_FIELD);
                     len_field <= type_nxt;
                  end
               end else begin
                  hdr_cnt <= hdr_cnt + 4'd1;
               end
            end

            // -----------------------------------------------------------
            S_VLAN: begin
               tci_sr <= {tci_sr[7:0], i_data};
               if (hdr_cnt == 4'd1) begin
                  hdr_cnt <= '0;
                  state   <= S_TYPE;
               end else begin
                  hdr_cnt <= hdr_cnt + 4'd1;
               end
            end

            // -----------------------------------------------------------
            S_PAYLOAD: begin
               dly <= {dly[23:0], i_data};
               if (dly_cnt != 3'(FCS_BYTES)) dly_cnt <= dly_cnt + 3'd1;

               if (pl_emit) begin
                  o_pl_valid <= 1'b1;
                  o_pl_data  <= dly[31:24];
                  o_pl_last  <= pl_last_w;
                  pl_cnt     <= pl_cnt_nxt;
               end

               if (crc_hit || oversize_w) begin
                  // Publish the extraction result and go back to hunting.
                  o_valid         <= 1'b1;
                  o_dst_mac       <= dst_sr;
                  o_src_mac       <= src_sr;
                  o_ethertype     <= type_sr;
                  o_vlan_valid    <= vlan_seen;
                  o_vlan_tci      <= tci_sr;
                  o_payload_bytes <= pl_cnt_nxt;
                  o_fcs_ok        <= crc_hit;
                  o_err_oversize  <= !crc_hit;
                  o_err_runt      <= crc_hit && (frame_bytes_nxt < 16'(MIN_FRAME_BYTES));
                  state           <= S_HUNT;
                  pre_cnt         <= '0;
               end
            end

            default: state <= S_HUNT;
         endcase
      end
   end

endmodule
