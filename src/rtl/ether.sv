module ether (
    input logic        i_clk,
    input logic        i_rst,
    input logic        i_start,
    input logic        i_valid,
    input logic [15:0] i_num_bytes,
    input logic [ 7:0] i_data,

    output logic                o_valid,
    output logic [        47:0] o_dst_mac,
    output logic [        47:0] o_src_mac,
    output logic [        15:0] o_ethertype,
    output logic [(1500*8)-1:0] o_payload,
    output logic [        15:0] o_payload_bytes
);

   bit [15:0] hdr_len = 16'(($bits(o_dst_mac) + $bits(o_src_mac) + $bits(o_ethertype))) / 8;
   assign o_payload_bytes = (num_bytes_actual >= hdr_len) ? (num_bytes_actual - hdr_len) : 16'd0;

   // Internal memory array to easily write payload bytes
   logic [7:0] payload_mem[0:1499];

   // Instantiate the parameterized counter
   // The counter expects a negative reset (rst_n) while ether uses positive reset (i_rst)
   logic rst_n;
   assign rst_n = ~i_rst;

   logic [15:0] byte_count;
   logic        counter_wr_en;
   logic [15:0] counter_data_i;

   logic [15:0] num_bytes_reg;
   logic [15:0] num_bytes_actual;
   assign num_bytes_actual = (i_start && i_valid) ? i_num_bytes : num_bytes_reg;

   counter #(
       .COUNT_WIDTH(16)
   ) u_byte_counter (
       .clk   (i_clk),
       .rst_n (rst_n),
       .wr_en (counter_wr_en),
       .data_i(counter_data_i),
       .data_o(),                // Unused
       .count (byte_count)
   );

   always_comb begin
      counter_wr_en  = 1'b0;
      counter_data_i = '0;

      if (i_valid) begin
         if (i_start) begin
            counter_wr_en  = 1'b1;
            counter_data_i = 16'd1;
         end else if (byte_count == (num_bytes_actual - 16'd1)) begin
            counter_wr_en  = 1'b1;
            counter_data_i = '0;
         end
      end else begin
         // If not valid, hold the counter value (by writing its own value)
         // since the counter only increments when wr_en is 0
         counter_wr_en  = 1'b1;
         counter_data_i = byte_count;
      end
   end

   always_ff @(posedge i_clk) begin
      if (i_rst) begin
         num_bytes_reg <= '0;
         o_valid       <= 1'b0;
         o_dst_mac     <= '0;
         o_src_mac     <= '0;
         o_ethertype   <= '0;
         for (int i = 0; i < 1500; i++) begin
            payload_mem[i] <= 8'h00;
         end
      end else begin
         if (i_start && i_valid) begin
            num_bytes_reg <= i_num_bytes;
            // If start is asserted abruptly, clear old state immediately to catch this new byte as MAC
            o_dst_mac <= {40'b0, i_data};
            o_src_mac <= '0;
            o_ethertype <= '0;
         end

         // o_valid pulses high for one cycle when the complete frame is received
         o_valid <= 1'b0;

         if (i_valid) begin
            if (byte_count < ($bits(o_dst_mac) / 8) && !i_start) begin
               // Shift in destination MAC
               o_dst_mac <= {o_dst_mac[39:0], i_data};
            end else if (byte_count < (($bits(o_dst_mac) + $bits(o_src_mac)) / 8)) begin
               // Shift in source MAC
               o_src_mac <= {o_src_mac[39:0], i_data};
            end else if (byte_count < hdr_len) begin
               // Shift in ethertype
               o_ethertype <= {o_ethertype[7:0], i_data};
            end else if (byte_count < num_bytes_actual) begin
               // Write payload byte if within 1500 bytes statically allocated size
               if ((byte_count - hdr_len) < 16'd1500) begin
                  payload_mem[byte_count-hdr_len] <= i_data;
               end
            end
            // Reset byte count at the end of the frame and pulse output valid
            if (byte_count == (num_bytes_actual - 16'd1)) begin
               o_valid <= 1'b1;
            end
         end
      end
   end

   // Continuous assignment to map payload_mem to flat output payload vector.
   generate
      for (genvar i = 0; i < 1500; i++) begin : gen_payload_assign
         assign o_payload[(1499-i)*8+:8] = payload_mem[i];
      end
   endgenerate

endmodule
