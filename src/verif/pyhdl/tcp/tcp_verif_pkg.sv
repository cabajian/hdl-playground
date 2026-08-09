`timescale 1ns / 1ps
`include "uvm_macros.svh"
`include "pyhdl_if_macros.svh"

package tcp_verif_pkg;
   import uvm_pkg::*;
   import pyhdl_if::*;
   import pyhdl_uvm::*;
   import tb_tcp_uvm_pyhdl_api_pkg::*;

   // Engine MSS for this TB (TcpEngine snd_mss). The item's payload is a byte
   // queue, so this no longer bounds a fixed lane -- it keeps each segment's
   // pack image well under UVM_MAX_STREAMBITS (4096) for pack_ints.
   localparam int unsigned TCP_TB_MSS = 256;

   typedef byte unsigned tcp_byte_q_t[$];

   `include "pyhdl_raw.sv"
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
