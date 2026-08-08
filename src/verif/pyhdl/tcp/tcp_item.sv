// tcp_item: UVM sequence item mirroring tcp_model's TcpSegment.
//
// The variable-length parts of a segment (options, payload) are carried in
// fixed-width lanes with explicit length fields. Fixed widths are mandatory:
// the pyhdl-if UVM wrapper discovers this type's field layout once by parsing
// sprint() output and then slices pack_ints() bitstreams by those cached
// widths, so dynamic fields cannot ride the field-transport path.
//
// Lane byte convention: index i is the i-th wire byte, so options[0] /
// payload[0] occupy the LSB byte lane. The Python side fills lanes with
// int.from_bytes(data, "little").
//
// Unused lane bytes are always zero (unpack_bytes() clears the lanes first;
// Python producers zero the snapshot before writing) so field-automation
// compare works without masking.
class tcp_item extends uvm_sequence_item;

   // Protocol fields, wire order (network byte order on the wire)
   rand bit [           15:0]      src_port;
   rand bit [           15:0]      dst_port;
   rand bit [           31:0]      seq_num;
   rand bit [           31:0]      ack_num;
   rand bit [            7:0]      flags;
   rand bit [           15:0]      window;
   rand bit [           15:0]      checksum;
   rand bit [           15:0]      urgent_ptr;

   // Option bytes exactly as they appear on the wire (already padded to a
   // multiple of 4 by the Python codec); 40 B is the TCP header maximum.
   rand bit [            7:0]      options_len;
   rand bit [TCP_OPT_MAX-1:0][7:0] options;

   // Payload lane, bounded by the TB's engine MSS (TCP_TB_MSS)
   rand bit [           15:0]      payload_len;
   rand bit [ TCP_TB_MSS-1:0][7:0] payload;

   constraint lane_len_c {
      options_len <= TCP_OPT_MAX;
      options_len % 4 == 0;
      payload_len <= TCP_TB_MSS;
   }

   `uvm_object_utils_begin(tcp_item)
      `uvm_field_int(src_port, UVM_ALL_ON)
      `uvm_field_int(dst_port, UVM_ALL_ON)
      `uvm_field_int(seq_num, UVM_ALL_ON)
      `uvm_field_int(ack_num, UVM_ALL_ON)
      `uvm_field_int(flags, UVM_ALL_ON)
      `uvm_field_int(window, UVM_ALL_ON)
      `uvm_field_int(checksum, UVM_ALL_ON)
      `uvm_field_int(urgent_ptr, UVM_ALL_ON)
      `uvm_field_int(options_len, UVM_ALL_ON)
      `uvm_field_int(options, UVM_ALL_ON)
      `uvm_field_int(payload_len, UVM_ALL_ON)
      `uvm_field_int(payload, UVM_ALL_ON)
   `uvm_object_utils_end

   function new(string name = "tcp_item");
      super.new(name);
   endfunction

   // Serialize to the wire image: 20 B header (network order) + options +
   // payload. Mirrors tcp_model/core/segment.py build().
   function tcp_byte_q_t pack_bytes();
      tcp_byte_q_t b;
      bit [3:0] off_words;
      off_words = 4'((32'd20 + 32'(options_len)) / 4);

      b.push_back(src_port[15:8]);
      b.push_back(src_port[7:0]);
      b.push_back(dst_port[15:8]);
      b.push_back(dst_port[7:0]);
      for (int i = 3; i >= 0; i--) b.push_back(seq_num[i*8+:8]);
      for (int i = 3; i >= 0; i--) b.push_back(ack_num[i*8+:8]);
      b.push_back({off_words, 4'b0000});
      b.push_back(flags);
      b.push_back(window[15:8]);
      b.push_back(window[7:0]);
      b.push_back(checksum[15:8]);
      b.push_back(checksum[7:0]);
      b.push_back(urgent_ptr[15:8]);
      b.push_back(urgent_ptr[7:0]);
      for (int i = 0; i < int'(options_len); i++) b.push_back(options[i]);
      for (int i = 0; i < int'(payload_len); i++) b.push_back(payload[i]);
      return b;
   endfunction

   // Populate from a wire image. Returns 0 on a malformed header.
   function bit unpack_bytes(tcp_byte_q_t b);
      int hdr_len;

      if (b.size() < 20) return 0;

      src_port   = {b[0], b[1]};
      dst_port   = {b[2], b[3]};
      seq_num    = {b[4], b[5], b[6], b[7]};
      ack_num    = {b[8], b[9], b[10], b[11]};
      hdr_len    = int'(b[12][7:4]) * 4;
      flags      = b[13];
      window     = {b[14], b[15]};
      checksum   = {b[16], b[17]};
      urgent_ptr = {b[18], b[19]};

      if (hdr_len < 20 || hdr_len > b.size() || (hdr_len - 20) > TCP_OPT_MAX) return 0;
      if ((b.size() - hdr_len) > TCP_TB_MSS) return 0;

      options     = '0;
      payload     = '0;
      options_len = 8'(hdr_len - 20);
      for (int i = 0; i < int'(options_len); i++) options[i] = b[20+i];
      payload_len = 16'(b.size() - hdr_len);
      for (int i = 0; i < int'(payload_len); i++) payload[i] = b[hdr_len+i];
      return 1;
   endfunction

   virtual function string convert2string();
      string fs = "";
      if (flags[1]) fs = {fs, "S"};
      if (flags[0]) fs = {fs, "F"};
      if (flags[2]) fs = {fs, "R"};
      if (flags[3]) fs = {fs, "P"};
      if (flags[4]) fs = {fs, "A"};
      if (flags[5]) fs = {fs, "U"};
      return $sformatf(
          "%0d->%0d [%s] seq=%0d ack=%0d win=%0d opts=%0d pl=%0d",
          src_port,
          dst_port,
          fs,
          seq_num,
          ack_num,
          window,
          options_len,
          payload_len
      );
   endfunction

endclass
