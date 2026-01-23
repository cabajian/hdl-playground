
   class counter_test extends dv_base_test;
      `uvm_component_utils(counter_test)
      counter_env env;
      counter_env_cfg cfg;

      function new(string name, uvm_component parent);
         super.new(name, parent);
      endfunction

      virtual function void build_phase(uvm_phase phase);
         super.build_phase(phase);
         cfg = counter_env_cfg::type_id::create("cfg");
         if (!uvm_config_db#(virtual counter_if)::get(this, "", "vif", cfg.m_agent_cfg.vif))
            `uvm_fatal("TEST", "Virtual interface not found in config_db")
         
         uvm_config_db#(counter_env_cfg)::set(this, "env", "cfg", cfg);
         env = counter_env::type_id::create("env", this);
      endfunction

      task run_phase(uvm_phase phase);
         counter_reset_sequence seq;
         counter_ral_sequence ral_seq;
         
         phase.raise_objection(this);
         
         // 1. Reset
         seq = counter_reset_sequence::type_id::create("seq");
         `uvm_info("TEST", "Starting reset sequence", UVM_LOW)
         seq.start(env.agent.sequencer);
         
         // 2. RAL Access
         #100ns;
         ral_seq = counter_ral_sequence::type_id::create("ral_seq");
         ral_seq.ral = env.ral;
         `uvm_info("TEST", "Starting RAL sequence", UVM_LOW)
         ral_seq.start(env.agent.sequencer);
         
         #200ns;
         `uvm_info("TEST", "Finished test", UVM_LOW)
         phase.drop_objection(this);
      endtask
   endclass
