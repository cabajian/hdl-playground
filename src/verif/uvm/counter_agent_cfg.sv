
  class counter_agent_cfg extends uvm_object;
    `uvm_object_utils(counter_agent_cfg)
    
    virtual counter_if vif;

    function new(string name = "counter_agent_cfg");
      super.new(name);
    endfunction
  endclass
