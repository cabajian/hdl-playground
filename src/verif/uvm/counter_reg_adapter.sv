
   class counter_reg_adapter extends dv_base_reg_adapter;
      `uvm_object_utils(counter_reg_adapter)

      function new(string name = "counter_reg_adapter");
         super.new(name);
      endfunction

      virtual function uvm_sequence_item reg2bus(const ref uvm_reg_bus_op rw);
         counter_transaction tr = counter_transaction::type_id::create("tr");
         tr.addr  = rw.addr[7:0];
         tr.wr_en = (rw.kind == UVM_WRITE);
         tr.data  = rw.data[7:0];
         tr.rst_n = 1; // Normal op
         return tr;
      endfunction

      virtual function void bus2reg(uvm_sequence_item bus_item, ref uvm_reg_bus_op rw);
         counter_transaction tr;
         if (!$cast(tr, bus_item)) begin
            `uvm_fatal("ADAPT", "Cast failed")
            return;
         end
         rw.kind = tr.wr_en ? UVM_WRITE : UVM_READ;
         rw.addr = tr.addr;
         rw.data = tr.data;
         rw.status = UVM_IS_OK;
      endfunction
   endclass
