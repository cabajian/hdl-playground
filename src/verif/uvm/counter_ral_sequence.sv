
   class counter_ral_sequence extends uvm_sequence;
      `uvm_object_utils(counter_ral_sequence)
      counter_reg_block ral;

      function new(string name = "counter_ral_sequence");
         super.new(name);
      endfunction

      virtual task body();
         uvm_status_e   status;
         uvm_reg_data_t val;

         if (ral == null) `uvm_fatal("RAL_SEQ", "RAL handle is null")

         // Read count register using RAL
         `uvm_info("RAL_SEQ", "Reading count register via RAL...", UVM_LOW)
         ral.count.read(status, val);
         `uvm_info("RAL_SEQ", $sformatf("Read value: %0h", val), UVM_LOW)
      endtask
   endclass
