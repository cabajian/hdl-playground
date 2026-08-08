// tcp_monitor: reassembles the RX byte stream into segments (delimited by
// rx_last), publishes each as a tcp_item, and forwards the raw bytes to the
// Python runner when a relay is configured.
class tcp_monitor extends uvm_monitor;
   `uvm_component_utils(tcp_monitor)

   virtual tcp_if vif;
   uvm_analysis_port #(tcp_item) ap;
   tcp_py_relay relay;  // optional; set via config_db

   function new(string name, uvm_component parent);
      super.new(name, parent);
   endfunction

   virtual function void build_phase(uvm_phase phase);
      super.build_phase(phase);
      ap = new("ap", this);
      if (!uvm_config_db#(virtual tcp_if)::get(this, "", "vif", vif)) begin
         `uvm_fatal(get_name(), "No virtual interface configured")
      end
      void'(uvm_config_db#(tcp_py_relay)::get(this, "", "relay", relay));
   endfunction

   task run_phase(uvm_phase phase);
      tcp_byte_q_t bytes;
      tcp_item item;
      bit in_seg = 0;

      // Sampled on the raw signals rather than through the clocking block:
      // rx_* are continuous assignments from the far side's clocking-block
      // outputs, and sampling that through a second clocking block races with
      // the driver's update, dropping the first byte of every segment.
      forever begin
         @(posedge vif.clk);
         if (vif.rx_valid === 1'b1) begin
            bytes.push_back(vif.rx_data);
            in_seg = 1;
            if (vif.rx_last === 1'b1) begin
               if (bytes.size() < 20) begin
                  `uvm_error(get_name(), $sformatf("Segment of %0d bytes at rx_last (min 20)",
                                                   bytes.size()))
               end else begin
                  item = tcp_item::type_id::create("mon_item");
                  if (!item.unpack_bytes(bytes)) begin
                     `uvm_error(get_name(), $sformatf("Malformed segment of %0d bytes",
                                                      bytes.size()))
                  end else begin
                     `uvm_info(get_name(), $sformatf("Observed: %s", item.convert2string()),
                               UVM_HIGH)
                     ap.write(item);
                     if (relay != null) relay.relay(bytes);
                  end
               end
               bytes.delete();
               in_seg = 0;
            end
         end else if (vif.rx_last === 1'b1 && !in_seg) begin
            `uvm_error(get_name(), "rx_last asserted without rx_valid")
         end
      end
   endtask
endclass
