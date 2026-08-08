// tcp_driver: serializes each tcp_item onto the TX byte stream, one byte per
// clock, tx_last high on the final byte. Publishes every driven item on its
// analysis port for the transport scoreboard.
class tcp_driver extends uvm_driver #(tcp_item);
   `uvm_component_utils(tcp_driver)

   virtual tcp_if vif;
   uvm_analysis_port #(tcp_item) ap;

   // Idle cycles inserted between segments (0 = back-to-back)
   int unsigned inter_seg_gap = 1;

   function new(string name, uvm_component parent);
      super.new(name, parent);
   endfunction

   virtual function void build_phase(uvm_phase phase);
      super.build_phase(phase);
      ap = new("ap", this);
      if (!uvm_config_db#(virtual tcp_if)::get(this, "", "vif", vif)) begin
         `uvm_fatal(get_name(), "No virtual interface configured")
      end
   endfunction

   task run_phase(uvm_phase phase);
      tcp_item req_c;
      tcp_byte_q_t bytes;

      // Driven with plain non-blocking assignments on the clock edge, and
      // sampled the same way by the monitor. Driving through the clocking
      // block instead races the monitor's sample across the cross-wired
      // interfaces and drops the first byte of every segment.
      vif.tx_valid <= 1'b0;
      vif.tx_data  <= '0;
      vif.tx_last  <= 1'b0;

      forever begin
         seq_item_port.get_next_item(req);

         bytes = req.pack_bytes();
         `uvm_info(get_name(), $sformatf("Driving %0d bytes: %s", bytes.size(), req.convert2string()
                   ), UVM_HIGH)

         foreach (bytes[i]) begin
            @(posedge vif.clk);
            vif.tx_valid <= 1'b1;
            vif.tx_data  <= bytes[i];
            vif.tx_last  <= (i == bytes.size() - 1);
         end
         @(posedge vif.clk);
         vif.tx_valid <= 1'b0;
         vif.tx_data  <= '0;
         vif.tx_last  <= 1'b0;
         repeat (inter_seg_gap) @(posedge vif.clk);

         $cast(req_c, req.clone());
         ap.write(req_c);
         seq_item_port.item_done();
      end
   endtask
endclass
