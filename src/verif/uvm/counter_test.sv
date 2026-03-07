
class counter_test extends uvm_test;
   `uvm_component_utils(counter_test)

   counter_env env;

   function new(string name, uvm_component parent);
      super.new(name, parent);
   endfunction

   virtual function void build_phase(uvm_phase phase);
      super.build_phase(phase);
      env = counter_env::type_id::create("env", this);
   endfunction

   task run_phase(uvm_phase phase);
      counter_reset_sequence rst_seq;
      counter_write_sequence wr_seq;

      phase.raise_objection(this);

      // 1. Reset
      rst_seq = counter_reset_sequence::type_id::create("rst_seq");
      `uvm_info("TEST", "Starting reset sequence", UVM_LOW)
      rst_seq.start(env.agent.sequencer);

      // 2. Write & read-back
      wr_seq = counter_write_sequence::type_id::create("wr_seq");
      `uvm_info("TEST", "Starting write sequence", UVM_LOW)
      wr_seq.start(env.agent.sequencer);

      #200ns;
      `uvm_info("TEST", "Finished test", UVM_LOW)
      phase.drop_objection(this);
   endtask
endclass
