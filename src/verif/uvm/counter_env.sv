
   class counter_env extends dv_base_env#(counter_env_cfg, counter_scoreboard);
      `uvm_component_utils(counter_env)
      counter_agent       agent;
      counter_reg_block   ral;
      counter_reg_adapter adapter;

      function new(string name, uvm_component parent);
         super.new(name, parent);
      endfunction

      virtual function void build_phase(uvm_phase phase);
         super.build_phase(phase);
         
         // Propagate agent config
         uvm_config_db#(counter_agent_cfg)::set(this, "agent*", "cfg", cfg.m_agent_cfg);
         
         agent = counter_agent::type_id::create("agent", this);
         scoreboard = counter_scoreboard::type_id::create("scoreboard", this);
         
         // Build RAL
         ral = counter_reg_block::type_id::create("ral", this);
         ral.build(.base_addr(32'h0));
         ral.lock_model();
         
         adapter = counter_reg_adapter::type_id::create("adapter", this);
      endfunction

      virtual function void connect_phase(uvm_phase phase);
         agent.monitor.ap.connect(scoreboard.item_collected_export);
         ral.default_map.set_sequencer(agent.sequencer, adapter);
      endfunction
   endclass
