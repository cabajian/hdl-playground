// Raw-bytes transport for pyhdl-if UVM sequence items.
//
// The normal path has Python set every field of a sequence item through
// pyhdl-if's packer: create_req() returns a wrapped item, Python pack()s it
// into a mirror object, assigns each field, and unpack()s it back. That works
// well until the item's shape is awkward for the packer or the printer --
// nested objects, unions, fields whose width the mirror cannot describe.
//
// This is the escape hatch. Python serializes the transaction itself and sends
// one byte queue; SystemVerilog reconstructs the real item from those bytes
// using whatever codec the item already has. The packer then only ever sees a
// single well-understood field.
//
// Note what this does and does not buy. It removes the packer's dependence on
// the item's *shape*, not on its *size*: the same total bytes still cross in
// one pack_ints() call, so UVM_MAX_STREAMBITS still applies.

typedef byte unsigned pyhdl_raw_byte_q_t[$];

// The one item Python ever populates on the raw path: a single byte queue,
// element width declared from the mirror (see raw_mirror.py). Item-type
// agnostic -- reusable by any testbench.
class pyhdl_raw_item extends uvm_sequence_item;

   byte unsigned raw[$];

   `uvm_object_utils_begin(pyhdl_raw_item)
      `uvm_field_queue_int(raw, UVM_ALL_ON)
   `uvm_object_utils_end

   function new(string name = "pyhdl_raw_item");
      super.new(name);
   endfunction

   virtual function string convert2string();
      return $sformatf("raw[%0d]", raw.size());
   endfunction

endclass

// Turns a wire image into a concrete sequence item. One small subclass per
// item type is all a testbench has to write; everything else here is generic.
//
// This is deliberately runtime polymorphism rather than a `#(type REQ)` type
// parameter. A parameterized proxy needs a self-referential helper
// declaration -- `helper #(REQ) extends imp_impl #(helper #(REQ))` -- which is
// the shape that makes Verilator's V3Param abort; tcp_py_seq.sv exists because
// of it. A virtual method sidesteps the whole area, compiles to one copy, and
// can be swapped per sequence instance.
virtual class pyhdl_raw_codec extends uvm_object;

   function new(string name = "pyhdl_raw_codec");
      super.new(name);
   endfunction

   // Return null if `raw` is not a well-formed image; the caller reports it.
   pure virtual function uvm_sequence_item decode(pyhdl_raw_byte_q_t raw);

endclass
