
package dv_lib_pkg;
  import uvm_pkg::*;
  `include "uvm_macros.svh"

  // -------------------------------------------------------------------------
  // Base Object
  // -------------------------------------------------------------------------
  class dv_base_object extends uvm_object;
    `uvm_object_utils(dv_base_object)
    function new(string name = "dv_base_object");
      super.new(name);
    endfunction
  endclass

  // -------------------------------------------------------------------------
  // Base Register Adapter
  // -------------------------------------------------------------------------
  virtual class dv_base_reg_adapter extends uvm_reg_adapter;
    `uvm_object_utils(dv_base_reg_adapter)
    function new(string name = "dv_base_reg_adapter");
      super.new(name);
    endfunction

    virtual function uvm_sequence_item reg2bus(const ref uvm_reg_bus_op rw);
       `uvm_fatal("dv_base_reg_adapter", "reg2bus must be implemented in child class")
       return null;
    endfunction

    virtual function void bus2reg(uvm_sequence_item bus_item, ref uvm_reg_bus_op rw);
       `uvm_fatal("dv_base_reg_adapter", "bus2reg must be implemented in child class")
    endfunction
  endclass

  // -------------------------------------------------------------------------
  // Base Sequencer
  // -------------------------------------------------------------------------
  class dv_base_sequencer #(type REQ   = uvm_sequence_item, 
                            type RSP   = REQ,
                            type CFG_T = uvm_object) extends uvm_sequencer #(REQ, RSP);
    `uvm_component_param_utils(dv_base_sequencer #(REQ, RSP, CFG_T))
    CFG_T cfg;
    function new(string name = "dv_base_sequencer", uvm_component parent = null);
      super.new(name, parent);
    endfunction

    virtual function void build_phase(uvm_phase phase);
      super.build_phase(phase);
      if (!uvm_config_db#(CFG_T)::get(this, "", "cfg", cfg)) begin
        `uvm_fatal(get_full_name(), "Failed to get cfg from uvm_config_db")
      end
    endfunction
  endclass

  // -------------------------------------------------------------------------
  // Base Driver
  // -------------------------------------------------------------------------
  class dv_base_driver #(type REQ   = uvm_sequence_item,
                         type RSP   = REQ,
                         type CFG_T = uvm_object) extends uvm_driver #(REQ, RSP);
    `uvm_component_param_utils(dv_base_driver #(REQ, RSP, CFG_T))
    CFG_T cfg;
    function new(string name = "dv_base_driver", uvm_component parent = null);
      super.new(name, parent);
    endfunction
    
    virtual function void build_phase(uvm_phase phase);
      super.build_phase(phase);
      if (!uvm_config_db#(CFG_T)::get(this, "", "cfg", cfg)) begin
        `uvm_fatal(get_full_name(), "Failed to get cfg from uvm_config_db")
      end
    endfunction
  endclass

  class dv_base_monitor #(type CFG_T = uvm_object) extends uvm_monitor;
    `uvm_component_param_utils(dv_base_monitor #(CFG_T))
    CFG_T cfg;
    function new(string name = "dv_base_monitor", uvm_component parent = null);
      super.new(name, parent);
    endfunction
    
    virtual function void build_phase(uvm_phase phase);
      super.build_phase(phase);
      if (!uvm_config_db#(CFG_T)::get(this, "", "cfg", cfg)) begin
        `uvm_fatal(get_full_name(), "Failed to get cfg from uvm_config_db")
      end
    endfunction
  endclass

  // -------------------------------------------------------------------------
  // Base Agent
  // -------------------------------------------------------------------------
  class dv_base_agent #(type CFG_T = uvm_object) extends uvm_agent;
    `uvm_component_param_utils(dv_base_agent #(CFG_T))
    CFG_T cfg;
    function new(string name = "dv_base_agent", uvm_component parent = null);
      super.new(name, parent);
    endfunction
    
    virtual function void build_phase(uvm_phase phase);
      super.build_phase(phase);
      if (!uvm_config_db#(CFG_T)::get(this, "", "cfg", cfg)) begin
        `uvm_fatal(get_full_name(), "Failed to get cfg from uvm_config_db")
      end
    endfunction
  endclass

  // -------------------------------------------------------------------------
  // Base Scoreboard
  // -------------------------------------------------------------------------
  class dv_base_scoreboard extends uvm_scoreboard;
    `uvm_component_utils(dv_base_scoreboard)
    function new(string name = "dv_base_scoreboard", uvm_component parent = null);
      super.new(name, parent);
    endfunction
  endclass

  // -------------------------------------------------------------------------
  // Base Env
  // -------------------------------------------------------------------------
  class dv_base_env #(type CFG_T = uvm_object, 
                      type SCO_T = dv_base_scoreboard) extends uvm_env;
    `uvm_component_param_utils(dv_base_env #(CFG_T, SCO_T))
    CFG_T cfg;
    SCO_T scoreboard;
    
    function new(string name = "dv_base_env", uvm_component parent = null);
      super.new(name, parent);
    endfunction
    
    virtual function void build_phase(uvm_phase phase);
      super.build_phase(phase);
      if (!uvm_config_db#(CFG_T)::get(this, "", "cfg", cfg)) begin
        `uvm_fatal(get_full_name(), "Failed to get cfg from uvm_config_db")
      end
    endfunction
  endclass

  // -------------------------------------------------------------------------
  // Base Test
  // -------------------------------------------------------------------------
  class dv_base_test extends uvm_test;
    `uvm_component_utils(dv_base_test)
    function new(string name = "dv_base_test", uvm_component parent = null);
      super.new(name, parent);
    endfunction
  endclass

endpackage
