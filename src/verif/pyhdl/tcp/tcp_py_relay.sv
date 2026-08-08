// tcp_py_relay: hands raw segment bytes observed by a monitor to the Python
// runner (TcpRunnerAPI.rx_segment). The monitor holds one of these when the
// test has a Python side; without one, monitoring is SV-only.
class tcp_py_relay extends uvm_object;

   TcpRunnerAPI_exp_if runner;
   int side;  // 0 = A, 1 = B

   function new(string name = "tcp_py_relay");
      super.new(name);
   endfunction

   function void relay(tcp_byte_q_t data);
      py_list  lst;
      PyObject py_long;

      lst = new();
      // PyList_Append increments the element's refcount, so drop our own
      // reference from PyLong_FromLong to avoid a slow leak.
      foreach (data[i]) begin
         py_long = PyLong_FromLong(longint'(data[i]));
         lst.append_obj(py_long);
         Py_DecRef(py_long);
      end

      // The generated wrapper's PyTuple_SetItem steals a reference; hand it an
      // owned one via steal() so our dispose() doesn't free early.
      runner.rx_segment(longint'(side), lst.steal());
      lst.dispose();
   endfunction

endclass
