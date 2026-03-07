
class counter_reset_sequence extends uvm_sequence #(counter_transaction);
   `uvm_object_utils(counter_reset_sequence)

   function new(string name = "counter_reset_sequence");
      super.new(name);
   endfunction

   virtual task body();
      counter_transaction tr;

      // Initial Reset
      `uvm_info("SEQ", "Starting reset sequence", UVM_LOW)
      tr = counter_transaction::type_id::create("tr");
      start_item(tr);
      tr.rst_n = 0;
      tr.wr_en = 0;
      tr.data  = 0;
      finish_item(tr);

      #20ns;  // Hold reset

      start_item(tr);
      tr.rst_n = 1;
      finish_item(tr);
      `uvm_info("SEQ", "Reset sequence finished", UVM_LOW)
   endtask
endclass
