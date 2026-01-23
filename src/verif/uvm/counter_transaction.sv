
   class counter_transaction extends uvm_sequence_item;
      `uvm_object_utils(counter_transaction)

      rand logic [7:0] addr;
      rand logic       wr_en;
      rand logic [7:0] data;
      rand logic       rst_n;
      logic [3:0]      count;

      function new(string name = "counter_transaction");
         super.new(name);
      endfunction

      virtual function string convert2string();
         return $sformatf("rst_n=%0b, addr=%0h, wr=%0b, data=%0h, count=%0h", 
                          rst_n, addr, wr_en, data, count);
      endfunction
   endclass
