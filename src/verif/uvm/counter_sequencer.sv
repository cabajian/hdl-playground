
   class counter_sequencer extends dv_base_sequencer#(counter_transaction, counter_transaction, counter_agent_cfg);
      `uvm_component_utils(counter_sequencer)

      function new(string name, uvm_component parent);
         super.new(name, parent);
      endfunction
   endclass
