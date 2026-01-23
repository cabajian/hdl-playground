
   class counter_agent extends dv_base_agent#(counter_agent_cfg);
      `uvm_component_utils(counter_agent)
      counter_driver    driver;
      counter_monitor   monitor;
      counter_sequencer sequencer;

      function new(string name, uvm_component parent);
         super.new(name, parent);
      endfunction

      virtual function void build_phase(uvm_phase phase);
         super.build_phase(phase);
         monitor = counter_monitor::type_id::create("monitor", this);
         if (get_is_active() == UVM_ACTIVE) begin
            driver    = counter_driver::type_id::create("driver", this);
            sequencer = counter_sequencer::type_id::create("sequencer", this);
         end
      endfunction

      virtual function void connect_phase(uvm_phase phase);
         if (get_is_active() == UVM_ACTIVE) begin
            driver.seq_item_port.connect(sequencer.seq_item_export);
         end
      endfunction
   endclass
