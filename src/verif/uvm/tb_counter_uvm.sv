`timescale 1ns/1ps
`include "uvm_macros.svh"

// Interface
interface counter_if (input logic clk);
   logic rst_n;
   logic [3:0] count;
   logic [7:0] addr;
   logic       wr_en;
   logic [7:0] data_i;
   logic [7:0] data_o;
   
   clocking cb @(posedge clk);
      output rst_n;
      output addr;
      output wr_en;
      output data_i;
      
      input  mon_rst_n = rst_n;  // Add input skew for monitor
      input  mon_addr  = addr;
      input  mon_wr_en = wr_en;
      input  mon_data_i= data_i;
      
      input  data_o;
      input  count;
   endclocking
endinterface

// Top module
module tb_counter_uvm;
   import uvm_pkg::*;
   import counter_ral_pkg::*;
   import counter_verif_pkg::*;

   // HW signals
   logic clk;

   counter_if cif(clk);

   counter dut (
      .clk   (cif.clk),
      .rst_n (cif.rst_n),
      .addr  (cif.addr),
      .wr_en (cif.wr_en),
      .data_i(cif.data_i),
      .data_o(cif.data_o),
      .count (cif.count)
   );

   initial begin
      clk = 0;
      forever #5 clk = ~clk;
   end

   initial begin
      // Interface setup
      uvm_config_db#(virtual counter_if)::set(null, "*", "vif", cif);
      run_test("counter_test");
   end

endmodule
