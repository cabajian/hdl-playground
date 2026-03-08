`timescale 1ns / 1ps
module counter #(
    parameter COUNT_WIDTH = 4
) (
    input  logic                   clk,
    input  logic                   rst_n,
    input  logic                   wr_en,
    input  logic [COUNT_WIDTH-1:0] data_i,
    output logic [COUNT_WIDTH-1:0] data_o,
    output logic [COUNT_WIDTH-1:0] count
);

   // Drive data_o with count when reading (wr_en low)
   assign data_o = !wr_en ? count : '0;

   always_ff @(posedge clk or negedge rst_n) begin
      if (!rst_n) begin
         count <= '0;
      end else begin
         if (wr_en) begin
            // Write operation
            count <= data_i;
         end else begin
            // Increment operation
            count <= count + 1'b1;
         end
      end
   end

endmodule
