typedef uvm_sequencer#(tcp_item) tcp_sequencer;

// tcp_agent: driver + sequencer + monitor for one side of the link.
class tcp_agent extends uvm_agent;
   `uvm_component_utils(tcp_agent)

   tcp_driver driver;
   tcp_sequencer sequencer;
   tcp_monitor monitor;

   function new(string name, uvm_component parent);
      super.new(name, parent);
   endfunction

   virtual function void build_phase(uvm_phase phase);
      super.build_phase(phase);
      driver    = tcp_driver::type_id::create("driver", this);
      sequencer = tcp_sequencer::type_id::create("sequencer", this);
      monitor   = tcp_monitor::type_id::create("monitor", this);
   endfunction

   virtual function void connect_phase(uvm_phase phase);
      super.connect_phase(phase);
      driver.seq_item_port.connect(sequencer.seq_item_export);
   endfunction
endclass
