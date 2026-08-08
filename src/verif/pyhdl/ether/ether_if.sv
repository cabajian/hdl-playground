interface ether_if;
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

   clocking cb @(posedge clk);
      output rst, valid, data;
      input o_pl_valid, o_pl_data, o_pl_last, o_valid, o_dst_mac, o_src_mac, o_ethertype,
          o_payload_bytes, o_vlan_valid, o_vlan_tci, o_fcs_ok, o_err_runt, o_err_oversize;
   endclocking

   modport dut(
       input clk, rst, valid, data,
       output o_pl_valid, o_pl_data, o_pl_last, o_valid, o_dst_mac, o_src_mac, o_ethertype,
       o_payload_bytes, o_vlan_valid, o_vlan_tci, o_fcs_ok, o_err_runt, o_err_oversize
   );

   modport tb(clocking cb);
endinterface
