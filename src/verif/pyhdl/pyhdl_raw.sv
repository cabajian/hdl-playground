// Raw-bytes transport for pyhdl-if UVM sequence items.
//
// The normal path has Python set every field of a sequence item through
// pyhdl-if's packer: create_req() returns a wrapped item, Python pack()s it
// into a mirror object, assigns each field, and unpack()s it back. That works
// well until the item's shape is awkward for the packer or the printer --
// nested objects, unions, fields whose width the mirror cannot describe.
//
// This is the escape hatch. Python serializes the transaction itself and sends
// one byte queue; SystemVerilog reconstructs the real item from those bytes.
// The packer then only ever sees a single well-understood field.
//
// Nothing here is per-item-type. An item opts in by extending
// seq_item_serializable, and the sequence names its type with a factory string,
// so one compiled sequence serves every testbench:
//
//   class my_item extends seq_item_serializable;
//      ...
//      virtual function byte_q_t to_bytes();          ... endfunction
//      virtual function bit      from_bytes(byte_q_t b); ... endfunction
//   endclass
//
//   pyhdl_raw_seq seq = pyhdl_raw_seq::type_id::create("seq");
//   seq.pyclass   = "my_runner::MySeq";
//   seq.item_type = "my_item";
//   seq.start(any_sequencer);
//
// Note what this does and does not buy. It removes the packer's dependence on
// the item's *shape*, not on its *size*: the same total bytes still cross in
// one pack_ints() call, so UVM_MAX_STREAMBITS still applies.

typedef byte unsigned byte_q_t[$];

// A sequence item that can state its own wire image, in both directions.
//
// This is the contract the raw path needs, but it is not specific to it: a
// driver serializing with to_bytes(), a monitor reassembling with from_bytes(),
// and a scoreboard comparing to_bytes() images are all the same codec, so it
// belongs on the item rather than in any one component.
//
// An `interface class` would be tidier -- an item could keep whatever base it
// already had -- and Verilator *compiles* one happily. It does not work at run
// time: `$cast` to an interface-class handle returns 0 even for an object whose
// class declares `implements`, so every decode failed with "does not
// implement". A virtual base class casts reliably, and for a uvm_sequence_item
// the constraint costs nothing in practice.
virtual class seq_item_serializable extends uvm_sequence_item;

   function new(string name = "seq_item_serializable");
      super.new(name);
   endfunction

   // Serialize to the wire image.
   pure virtual function byte_q_t to_bytes();

   // Populate from a wire image. Return 0 if the image is malformed.
   pure virtual function bit from_bytes(byte_q_t b);

endclass

// The only item Python ever populates on the raw path: a single byte queue,
// element width declared from the mirror (see raw_mirror.py).
//
// It is a seq_item_serializable too, whose codec is the identity -- the bytes
// it carries *are* its wire image. That makes it usable anywhere a serializable
// item is expected, e.g. driven directly by a byte-level driver.
class bytes_item extends seq_item_serializable;

   byte unsigned raw[$];

   `uvm_object_utils_begin(bytes_item)
      `uvm_field_queue_int(raw, UVM_ALL_ON)
   `uvm_object_utils_end

   function new(string name = "bytes_item");
      super.new(name);
   endfunction

   virtual function byte_q_t to_bytes();
      return raw;
   endfunction

   virtual function bit from_bytes(byte_q_t b);
      raw = b;
      return 1;
   endfunction

   virtual function string convert2string();
      return $sformatf("raw[%0d]", raw.size());
   endfunction

endclass

typedef class pyhdl_raw_seq_helper;

// Generic Python-bodied proxy sequence for the raw path.
//
// Deliberately *not* parameterized, and that is what makes it generic rather
// than what limits it:
//
//   * A `#(type REQ)` proxy would need the self-referential helper declaration
//     `helper #(REQ) extends imp_impl #(helper #(REQ))`, which is the shape
//     that makes Verilator's V3Param abort -- the bug that forced
//     tcp_py_seq.sv to be hand-specialized in the first place.
//   * It is not needed anyway. uvm_sequence's REQ defaults to
//     uvm_sequence_item, start() takes a uvm_sequencer_base, and
//     uvm_sequencer_param_base::send_request() $casts the item at run time.
//     So this sequence drives a uvm_sequencer #(my_item) perfectly well, as
//     long as the object it sends really is a my_item -- which the factory
//     guarantees.
class pyhdl_raw_seq extends uvm_sequence implements pyhdl_uvm_sequence_proxy_if;
   `uvm_object_utils(pyhdl_raw_seq)

   // Python class to run as the body, "module::Class"
   string pyclass = "";
   // UVM factory type name of the item to reconstruct, e.g. "tcp_item"
   string item_type = "";

   pyhdl_raw_seq_helper m_helper;

   function new(string name = "pyhdl_raw_seq");
      super.new(name);
   endfunction

   virtual function uvm_sequencer_base _get_sequencer();
      return m_sequencer;
   endfunction

   task body();
      string modname, clsname;
      PyObject mod, cls;
      int i;

      pyhdl_if_start();

      // The carrier's only field is a queue, and UVM emits a queue's element
      // count only when the packer has metadata enabled -- while pyhdl-if's
      // Python model always reads it. Without this every image would arrive
      // empty. Enforced here rather than left to the test, so this sequence
      // works in a testbench that has never needed it (best practices 5.2).
      uvm_default_packer.use_metadata = 1;

      if (pyclass == "") begin
         `uvm_fatal(get_name(), "No value specified for 'pyclass'")
      end
      if (item_type == "") begin
         `uvm_fatal(get_name(), "No value specified for 'item_type'")
      end

      for (i = pyclass.len() - 1; i >= 0; i--) begin
         if (pyclass[i] == ":") begin
            clsname = pyclass.substr(i + 1, pyclass.len() - 1);
            break;
         end
      end

      if (clsname == "") begin
         `uvm_fatal(get_name(), $sformatf("Failed to find '::' in pyclass %0s", pyclass))
      end

      while (i >= 0) begin
         if (pyclass[i] != ":") break;
         i--;
      end

      modname = pyclass.substr(0, i);

      mod     = PyImport_ImportModule(modname);
      if (mod == null) begin
         PyErr_Print();
         `uvm_fatal(get_name(), $sformatf("Failed to load Python module %0s", modname))
         return;
      end

      cls = PyObject_GetAttrString(mod, clsname);
      if (cls == null) begin
         PyErr_Print();
         `uvm_fatal(get_name(), $sformatf("Failed to find class %0s in Python module %0s", clsname,
                                          modname))
         return;
      end

      m_helper           = new(pyclass, cls);
      m_helper.m_proxy   = this;
      m_helper.item_type = item_type;

      pyhdl_uvm_object_rgy::inst().register_object(this, m_helper.m_obj);

      m_helper.m_exp.body();
   endtask

endclass

class pyhdl_raw_seq_helper extends uvm_sequence_proxy_imp_impl #(pyhdl_raw_seq_helper)
   implements pyhdl_uvm_object_if;

   uvm_sequence_base m_proxy;
   uvm_sequence_proxy_exp_impl m_exp;

   // Factory type name of the item to build, and the item start_item built.
   // The decoded handle is cached because UVM needs start_item and finish_item
   // to name the same object, and Python only ever holds the raw one.
   string item_type;
   uvm_sequence_item m_decoded = null;

   function new(string clsname, PyObject cls);
      PyObject impl_o, args;
      super.new(this);

      m_exp = new(m_obj);

      args  = PyTuple_New(1);
      void'(PyTuple_SetItem(args, 0, m_obj));

      impl_o = PyObject_Call(cls, args, null);
      if (impl_o == null) begin
         PyErr_Print();
         `PYHDL_IF_FATAL(("Failed to construct user class %0s", clsname))
         $finish;
      end

      if (PyObject_SetAttrString(m_obj, "_impl", impl_o) != 0) begin
         PyErr_Print();
         `PYHDL_IF_FATAL(("Failed to set _impl on proxy wrapper"))
         $finish;
      end
   endfunction

   virtual function uvm_object get_object();
      return m_proxy;
   endfunction

   virtual function PyObject get_pyobject();
      return m_obj;
   endfunction

   virtual function string get_name();
      return m_proxy.get_name();
   endfunction

   virtual function void reseed();
      m_proxy.reseed();
   endfunction

   virtual function void set_name(string name);
      m_proxy.set_name(name);
   endfunction

   virtual function int get_inst_id();
      return m_proxy.get_inst_id();
   endfunction

   virtual function int get_inst_count();
      return m_proxy.get_inst_count();
   endfunction

   virtual function string get_type_name();
      return m_proxy.get_type_name();
   endfunction

   virtual function PyObject create();
      return pyhdl_uvm_object_rgy::inst().wrap(m_proxy.create());
   endfunction

   virtual function PyObject clone();
      return pyhdl_uvm_object_rgy::inst().wrap(m_proxy.clone());
   endfunction

   virtual function void print();
      m_proxy.print();
   endfunction

   virtual function string convert2string();
      return m_proxy.convert2string();
   endfunction

   virtual function void record();
      m_proxy.record();
   endfunction

   virtual function void copy(PyObject rhs);
      m_proxy.copy(pyhdl_uvm_object_rgy::inst().get_object(rhs));
   endfunction

   virtual function bit compare(PyObject rhs);
      return m_proxy.compare(pyhdl_uvm_object_rgy::inst().get_object(rhs));
   endfunction

   virtual function void set_int_local(string name, int value);
      m_proxy.set_int_local(name, value);
   endfunction

   virtual function void set_string_local(string name, string value);
      m_proxy.set_string_local(name, value);
   endfunction

   virtual function void set_object_local(string name, PyObject value);
      m_proxy.set_object_local(name, pyhdl_uvm_object_rgy::inst().get_object(value));
   endfunction

   virtual function PyObject _get_sequencer();
      pyhdl_uvm_sequence_proxy_if proxy;
      $cast(proxy, m_proxy);
      return pyhdl_uvm_object_rgy::inst().wrap(proxy._get_sequencer());
   endfunction

   virtual function PyObject get_userdata();
      return None;
   endfunction

   // Python always gets the raw carrier, whatever the real item type is.
   virtual function PyObject create_req();
      bytes_item req = bytes_item::type_id::create();
      return pyhdl_uvm_object_rgy::inst().wrap(req);
   endfunction

   virtual function PyObject create_rsp();
      bytes_item rsp = bytes_item::type_id::create();
      return pyhdl_uvm_object_rgy::inst().wrap(rsp);
   endfunction

   // Build `item_type` from the factory and let the item deserialize itself.
   // This is the whole of the type-specific work, and none of it is written
   // per type: the name is a string and the codec is the item's own method.
   virtual function uvm_sequence_item m_decode(PyObject item);
      uvm_object item_o;
      bytes_item raw;
      uvm_object built;
      uvm_sequence_item seq_item;
      seq_item_serializable target;

      // Drop any previously decoded handle first. PYHDL_IF_FATAL calls $finish,
      // which Verilator defers to the end of the timestep rather than aborting
      // this call, so Python still gets its start_item() return and still calls
      // finish_item(). Without this, that call would re-drive the *previous*
      // item on the way out.
      m_decoded = null;

      item_o = pyhdl_uvm_object_rgy::inst().get_object(item);
      if (!$cast(raw, item_o)) begin
         `PYHDL_IF_FATAL(("raw path: Python did not hand back a bytes_item"))
         return null;
      end

      // Scope comes from the proxied sequence: this helper is a pyhdl-if imp
      // impl, not a uvm_object, so it has no get_full_name() of its own.
      built =
          uvm_factory::get().create_object_by_name(item_type, m_proxy.get_full_name(), "raw_req");
      if (built == null) begin
         `PYHDL_IF_FATAL(("raw path: factory has no type '%0s'", item_type))
         return null;
      end

      if (!$cast(target, built)) begin
         // Report what the factory actually built: a type override is the
         // likely cause when the requested name looks right.
         string built_t = built.get_type_name();
         `PYHDL_IF_FATAL(
             ("raw path: '%0s' built '%0s', not a seq_item_serializable", item_type, built_t))
         return null;
      end

      if (!target.from_bytes(raw.raw)) begin
         `PYHDL_IF_FATAL(("raw path: '%0s' rejected a %0d-byte image", item_type, raw.raw.size()))
         return null;
      end

      if (!$cast(seq_item, built)) begin
         `PYHDL_IF_FATAL(("raw path: '%0s' is not a uvm_sequence_item", item_type))
         return null;
      end

      m_decoded = seq_item;
      return m_decoded;
   endfunction

   virtual task start_item(PyObject item);
      uvm_sequence_item it = m_decode(item);
      if (it != null) m_proxy.start_item(it);
   endtask

   virtual task finish_item(PyObject item);
      // Reuse what start_item decoded: decoding again would hand UVM a
      // different object than the one it arbitrated for. Null means that decode
      // failed and already reported; stay quiet rather than drive anything.
      if (m_decoded != null) m_proxy.finish_item(m_decoded);
   endtask

endclass
