// tcp_item: UVM sequence item mirroring tcp_model's TcpSegment.
//
// The variable-length parts of a segment (options, payload) are byte queues.
// pyhdl-if's UVM wrapper supports queue fields, but it *infers* the element
// width -- on pack from the data itself (max_val.bit_length()), which for a
// byte queue would vary per transaction and collapse to 1 bit for an all-zero
// payload. The element width is therefore pinned to 8 from the Python side by
// the mirror class in tcp_item_mirror.py; see the note there.
//
// Queue element i is wire byte i. No separate length fields: size() is the
// length.
class tcp_item extends uvm_sequence_item;

   // Protocol fields, wire order (network byte order on the wire)
   rand bit [15:0] src_port;
   rand bit [15:0] dst_port;
   rand bit [31:0] seq_num;
   rand bit [31:0] ack_num;
   rand bit [7:0] flags;
   rand bit [15:0] window;
   rand bit [15:0] checksum;
   rand bit [15:0] urgent_ptr;

   // Option bytes exactly as they appear on the wire, already padded to a
   // multiple of 4 by the codec. Not rand: stimulus comes from the Python
   // engines, and Verilator's constrained randomization of queues is limited.
   byte unsigned options[$];
   byte unsigned payload[$];

   `uvm_object_utils_begin(tcp_item)
      `uvm_field_int(src_port, UVM_ALL_ON)
      `uvm_field_int(dst_port, UVM_ALL_ON)
      `uvm_field_int(seq_num, UVM_ALL_ON)
      `uvm_field_int(ack_num, UVM_ALL_ON)
      `uvm_field_int(flags, UVM_ALL_ON)
      `uvm_field_int(window, UVM_ALL_ON)
      `uvm_field_int(checksum, UVM_ALL_ON)
      `uvm_field_int(urgent_ptr, UVM_ALL_ON)
      `uvm_field_queue_int(options, UVM_ALL_ON)
      `uvm_field_queue_int(payload, UVM_ALL_ON)
   `uvm_object_utils_end

   function new(string name = "tcp_item");
      super.new(name);
   endfunction

   // Serialize to the wire image: 20 B header (network order) + options +
   // payload. Mirrors tcp_model/core/segment.py build().
   function tcp_byte_q_t pack_bytes();
      tcp_byte_q_t b;
      bit [3:0] off_words;
      off_words = 4'((32'd20 + 32'(options.size())) / 4);

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
      foreach (options[i]) b.push_back(options[i]);
      foreach (payload[i]) b.push_back(payload[i]);
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

      if (hdr_len < 20 || hdr_len > b.size()) return 0;

      options.delete();
      payload.delete();
      for (int i = 20; i < hdr_len; i++) options.push_back(b[i]);
      for (int i = hdr_len; i < b.size(); i++) payload.push_back(b[i]);
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
          options.size(),
          payload.size()
      );
   endfunction

endclass

// Raw-bytes codec for tcp_item: the entire per-type cost of the raw transport
// path (pyhdl_raw.sv). Python sends a wire image, this turns it back into a
// real tcp_item using the same unpack_bytes() the monitor path already relies
// on -- so the raw path cannot silently disagree with the field path.
class tcp_item_codec extends pyhdl_raw_codec;
   `uvm_object_utils(tcp_item_codec)

   function new(string name = "tcp_item_codec");
      super.new(name);
   endfunction

   virtual function uvm_sequence_item decode(pyhdl_raw_byte_q_t raw);
      tcp_item it = tcp_item::type_id::create("raw_req");
      if (!it.unpack_bytes(raw)) return null;
      return it;
   endfunction

endclass
