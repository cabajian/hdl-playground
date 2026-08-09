// tcp_py_seq: concrete specialization of pyhdl-if's shipped
// pyhdl_uvm_sequence_proxy for REQ=tcp_item.
//
// This is a workaround, not a redesign: Verilator 5.48 hits an internal error
// (V3Param "Couldn't find pin in clone list") elaborating the shipped proxy's
// self-parameterized helper declaration
//   class helper #(type REQ, RSP) extends imp_impl #(helper #(REQ,REQ))
// so the two classes are specialized here with the type parameters resolved.
// The Python-side contract is untouched: the sequence's pyclass names a
// Python class extending hdl_if.uvm.uvm_sequence_impl, whose body() calls
// create_req()/start_item()/finish_item() through the proxy handle.

typedef class tcp_py_seq_helper;

class tcp_py_seq extends uvm_sequence #(
    .REQ(tcp_item)
) implements pyhdl_uvm_sequence_proxy_if;
   `uvm_object_utils(tcp_py_seq)

   string pyclass = "";
   tcp_py_seq_helper m_helper;

   function new(string name = "tcp_py_seq");
      super.new(name);
   endfunction

   virtual function uvm_sequencer_base _get_sequencer();
      return m_sequencer;
   endfunction

   task body();
      string modname, clsname;
      PyObject mod, cls;
      int i;

      // Ensure that the task scheduler is running
      pyhdl_if_start();

      if (pyclass == "") begin
         `uvm_fatal(get_name(), "No value specified for 'pyclass'")
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

      mod = PyImport_ImportModule(modname);
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

      m_helper = new(pyclass, cls);
      m_helper.m_proxy = this;

      // Associate the Python object for the helper with the sequence object
      pyhdl_uvm_object_rgy::inst().register_object(this, m_helper.m_obj);

      m_helper.m_exp.body();
   endtask

endclass

class tcp_py_seq_helper extends uvm_sequence_proxy_imp_impl #(tcp_py_seq_helper)
   implements pyhdl_uvm_object_if;

   uvm_sequence_base m_proxy;
   uvm_sequence_proxy_exp_impl m_exp;

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

   virtual function PyObject create_req();
      tcp_item req = tcp_item::type_id::create();
      return pyhdl_uvm_object_rgy::inst().wrap(req);
   endfunction

   virtual function PyObject create_rsp();
      tcp_item rsp = tcp_item::type_id::create();
      return pyhdl_uvm_object_rgy::inst().wrap(rsp);
   endfunction

   virtual task start_item(PyObject item);
      uvm_object item_o;
      uvm_sequence_item uvm_item;

      item_o = pyhdl_uvm_object_rgy::inst().get_object(item);
      if ($cast(uvm_item, item_o)) begin
         m_proxy.start_item(uvm_item);
      end else begin
         `PYHDL_IF_FATAL(("can't cast back to a sequence item"))
      end
   endtask

   virtual task finish_item(PyObject item);
      uvm_object item_o;
      uvm_sequence_item uvm_item;

      item_o = pyhdl_uvm_object_rgy::inst().get_object(item);
      if ($cast(uvm_item, item_o)) begin
         m_proxy.finish_item(uvm_item);
      end else begin
         `PYHDL_IF_FATAL(("can't cast back to a sequence item"))
      end
   endtask

endclass
