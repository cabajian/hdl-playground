`timescale 1ns / 1ps
`include "uvm_macros.svh"
`include "pyhdl_if_macros.svh"

package tcp_verif_pkg;
   import uvm_pkg::*;
   import pyhdl_if::*;
   import pyhdl_uvm::*;
   import tb_tcp_uvm_pyhdl_api_pkg::*;

   // Engine MSS for this TB (TcpEngine snd_mss); bounds the item payload lane.
   // Note: the item's total registered width must stay under UVM_MAX_STREAMBITS
   // (4096) for pack_ints - at 256 B payload + 40 B options it is ~2.5 kbit.
   localparam int unsigned TCP_TB_MSS = 256;
   // TCP header options maximum (header length field tops out at 60 bytes)
   localparam int unsigned TCP_OPT_MAX = 40;

   typedef byte unsigned tcp_byte_q_t[$];

   `include "tcp_item.sv"
   `include "tcp_py_seq.sv"
   `include "tcp_driver.sv"
   `include "tcp_py_relay.sv"
   `include "tcp_monitor.sv"
   `include "tcp_agent.sv"
   `include "tcp_scoreboard.sv"
   `include "tcp_env.sv"
   `include "tcp_time_service.sv"
   `include "tcp_test.sv"
endpackage
