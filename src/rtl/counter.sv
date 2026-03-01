`timescale 1ns/1ps
module counter (
    input  logic       clk,
    input  logic       rst_n,
    input  logic       wr_en,
    input  logic [7:0] data_i,
    output logic [7:0] data_o,
    output logic [3:0] count
);

   // Drive data_o with zero-padded count when reading (wr_en low)
   assign data_o = !wr_en ? {4'h0, count} : 8'h00;

   always_ff @(posedge clk or negedge rst_n) begin
      if (!rst_n) begin
         count <= 4'h0;
      end else begin
         if (wr_en) begin
            // Write operation
            count <= data_i[3:0];
         end else begin
            // Increment operation
            count <= count + 4'h1;
         end
      end
   end

endmodule
