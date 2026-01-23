
  class counter_env_cfg extends uvm_object;
    `uvm_object_utils(counter_env_cfg)
    
    counter_agent_cfg m_agent_cfg;

    function new(string name = "counter_env_cfg");
      super.new(name);
      m_agent_cfg = counter_agent_cfg::type_id::create("m_agent_cfg");
    endfunction
  endclass
