// tcp_env: two agents (side A / side B) cross-checked by the transport
// scoreboard.
class tcp_env extends uvm_env;
   `uvm_component_utils(tcp_env)

   tcp_agent agent_a;
   tcp_agent agent_b;
   tcp_scoreboard scoreboard;

   function new(string name, uvm_component parent);
      super.new(name, parent);
   endfunction

   virtual function void build_phase(uvm_phase phase);
      super.build_phase(phase);
      agent_a    = tcp_agent::type_id::create("agent_a", this);
      agent_b    = tcp_agent::type_id::create("agent_b", this);
      scoreboard = tcp_scoreboard::type_id::create("scoreboard", this);
   endfunction

   virtual function void connect_phase(uvm_phase phase);
      super.connect_phase(phase);
      agent_a.driver.ap.connect(scoreboard.drv_a_fifo.analysis_export);
      agent_b.monitor.ap.connect(scoreboard.mon_b_fifo.analysis_export);
      agent_b.driver.ap.connect(scoreboard.drv_b_fifo.analysis_export);
      agent_a.monitor.ap.connect(scoreboard.mon_a_fifo.analysis_export);
   endfunction
endclass
