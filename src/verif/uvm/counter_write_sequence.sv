
   class counter_write_sequence extends uvm_sequence#(counter_transaction);
      `uvm_object_utils(counter_write_sequence)

      function new(string name = "counter_write_sequence");
         super.new(name);
      endfunction

      virtual task body();
         counter_transaction tr;

         for (int seed = 0; seed < 16; seed++) begin
            // 1. Write starting count
            `uvm_info("SEQ", $sformatf("Writing seed %0d", seed), UVM_LOW)
            tr = counter_transaction::type_id::create("tr");
            start_item(tr);
            tr.rst_n = 1; tr.wr_en = 1; tr.data = 4'(seed);
            finish_item(tr);

            // 2. Let counter free-run for 16 cycles (verify counting)
            repeat (16) begin
               tr = counter_transaction::type_id::create("tr");
               start_item(tr);
               tr.rst_n = 1; tr.wr_en = 0; tr.data = 0;
               finish_item(tr);
            end

            // 3. Write 0
            tr = counter_transaction::type_id::create("tr");
            start_item(tr);
            tr.rst_n = 1; tr.wr_en = 1; tr.data = 4'h0;
            finish_item(tr);

            // 4. Read for 16 cycles (verify reading)
            repeat (16) begin
               tr = counter_transaction::type_id::create("tr");
               start_item(tr);
               tr.rst_n = 1; tr.wr_en = 0; tr.data = 0;
               finish_item(tr);
            end
         end

         `uvm_info("SEQ", "Write sequence finished", UVM_LOW)
      endtask
   endclass
