`timescale 1ns/1ps
`include "uvm_macros.svh"

// Interface
interface counter_if (input logic clk);
   logic rst_n;
   logic [3:0] count;
   logic       wr_en;
   logic [7:0] data_i;
   logic [7:0] data_o;
   
   clocking cb @(posedge clk);
      output rst_n;
      output wr_en;
      output data_i;
      
      input  mon_rst_n = rst_n;  // Add input skew for monitor
      input  mon_wr_en = wr_en;
      input  mon_data_i= data_i;
      
      input  data_o;
      input  count;
   endclocking
endinterface

// Top module
module tb_counter_uvm;
   import uvm_pkg::*;
   import counter_verif_pkg::*;

   // HW signals
   logic clk;

   counter_if vif(clk);

   counter dut (
      .clk   (vif.clk),
      .rst_n (vif.rst_n),
      .wr_en (vif.wr_en),
      .data_i(vif.data_i),
      .data_o(vif.data_o),
      .count (vif.count)
   );

   initial begin
      clk = 0;
      forever #5 clk = ~clk;
   end

   initial begin
      // Interface setup
      uvm_config_db#(virtual counter_if)::set(null, "*", "counter_vif", vif);
      run_test("counter_test");
   end

endmodule
