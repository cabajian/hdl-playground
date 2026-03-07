class counter_monitor extends uvm_monitor;
   `uvm_component_utils(counter_monitor)

   virtual counter_if vif;
   uvm_analysis_port #(counter_transaction) ap;

   function new(string name, uvm_component parent);
      super.new(name, parent);
      ap = new("ap", this);
   endfunction

   virtual function void build_phase(uvm_phase phase);
      super.build_phase(phase);
      if (!uvm_config_db#(virtual counter_if)::get(this, "", "counter_vif", vif))
         `uvm_fatal("MON", "Virtual interface not found in config_db")
   endfunction

   virtual task run_phase(uvm_phase phase);
      forever begin
         counter_transaction tr;
         @(posedge vif.clk);
         #1;  // Delay to sample post-clocking-block update

         tr = counter_transaction::type_id::create("tr");
         tr.rst_n = vif.cb.mon_rst_n;
         tr.wr_en = vif.cb.mon_wr_en;
         tr.data = vif.cb.mon_wr_en ? vif.cb.mon_data_i : vif.cb.data_o;
         tr.count = vif.cb.count;
         ap.write(tr);
      end
   endtask
endclass
