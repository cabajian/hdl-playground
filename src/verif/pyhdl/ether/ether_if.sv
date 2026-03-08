interface ether_if;
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

   clocking cb @(posedge clk);
      output rst, start, valid, num_bytes, data;
      input o_valid, o_dst_mac, o_src_mac, o_ethertype, o_payload, o_payload_bytes;
   endclocking

   modport dut(
       input clk, rst, start, valid, num_bytes, data,
       output o_valid, o_dst_mac, o_src_mac, o_ethertype, o_payload, o_payload_bytes
   );

   modport tb(clocking cb);
endinterface
