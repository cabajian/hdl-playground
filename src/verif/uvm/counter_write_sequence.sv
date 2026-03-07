
   class counter_write_sequence extends uvm_sequence#(counter_transaction);
      `uvm_object_utils(counter_write_sequence)

      function new(string name = "counter_write_sequence");
         super.new(name);
      endfunction

      virtual task body();
         counter_transaction tr;

         // 1. Write 0xA to counter
         `uvm_info("SEQ", "Writing 0xA to counter", UVM_LOW)
         tr = counter_transaction::type_id::create("tr");
         start_item(tr);
         tr.rst_n = 1; tr.wr_en = 1; tr.data = 4'hA;
         finish_item(tr);

         // 2. Letting counter free-run for a few cycles (should auto-increment from 0xA)
         `uvm_info("SEQ", "Letting counter auto-increment", UVM_LOW)
         repeat (5) begin
            tr = counter_transaction::type_id::create("tr");
            start_item(tr);
            tr.rst_n = 1; tr.wr_en = 0; tr.data = 0;
            finish_item(tr);
         end

         // 3. Write 0x0 to counter (reset via write)
         `uvm_info("SEQ", "Writing 0x0 to counter", UVM_LOW)
         tr = counter_transaction::type_id::create("tr");
         start_item(tr);
         tr.rst_n = 1; tr.wr_en = 1; tr.data = 4'h0;
         finish_item(tr);

         // 4. Let it increment again
         repeat (3) begin
            tr = counter_transaction::type_id::create("tr");
            start_item(tr);
            tr.rst_n = 1; tr.wr_en = 0; tr.data = 0;
            finish_item(tr);
         end

         `uvm_info("SEQ", "Write sequence finished", UVM_LOW)
      endtask
   endclass
