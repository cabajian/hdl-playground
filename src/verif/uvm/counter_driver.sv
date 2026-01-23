   class counter_driver extends dv_base_driver#(counter_transaction, counter_transaction, counter_agent_cfg);
      `uvm_component_utils(counter_driver)
      virtual counter_if vif;

      function new(string name, uvm_component parent);
         super.new(name, parent);
      endfunction

      virtual function void build_phase(uvm_phase phase);
         super.build_phase(phase);
         vif = cfg.vif;
         if (vif == null) `uvm_fatal("DRV", "Virtual interface in cfg is null")
      endfunction

      virtual task run_phase(uvm_phase phase);
         forever begin
            seq_item_port.get_next_item(req);
            `uvm_info("DRV", $sformatf("Driving: %s", req.convert2string()), UVM_HIGH)
            
            // Sync to clock edge
            @(vif.cb);
            vif.cb.rst_n  <= req.rst_n;
            vif.cb.addr   <= req.addr;
            vif.cb.wr_en  <= req.wr_en;
            vif.cb.data_i <= req.data;
            
            // Wait for drive to happen and results to settle
            @(vif.cb);
            
            // For reads, capture the output data from the DUT
            if (!req.wr_en && req.rst_n) begin
               req.data = vif.cb.data_o;
            end
            
            seq_item_port.item_done();
         end
      endtask
   endclass
