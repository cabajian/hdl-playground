`timescale 1ns/1ps
`include "uvm_macros.svh"

// Interface
interface counter_if (input logic clk, input logic rst_n);
   logic [3:0] count;
endinterface

// Top module
module tb_counter_uvm;
   import uvm_pkg::*;

   // Transaction
   class counter_transaction extends uvm_sequence_item;
      `uvm_object_utils(counter_transaction)

      rand logic rst_n;
      logic [3:0] count;

      function new(string name = "counter_transaction");
         super.new(name);
      endfunction

      virtual function string convert2string();
         return $sformatf("rst_n=%0b, count=%0h", rst_n, count);
      endfunction
   endclass

   // Sequence
   class counter_reset_sequence extends uvm_sequence#(counter_transaction);
      `uvm_object_utils(counter_reset_sequence)

      function new(string name = "counter_reset_sequence");
         super.new(name);
      endfunction

      virtual task body();
         counter_transaction tr;
         
         // Initial Reset
         `uvm_info("SEQ", "Starting reset sequence", UVM_LOW)
         tr = counter_transaction::type_id::create("tr");
         start_item(tr);
         tr.rst_n = 0;
         finish_item(tr);
         
         #20ns; // Hold reset
         
         start_item(tr);
         tr.rst_n = 1;
         finish_item(tr);
         `uvm_info("SEQ", "Reset sequence finished", UVM_LOW)
      endtask
   endclass

   // Driver
   class counter_driver extends uvm_driver#(counter_transaction);
      `uvm_component_utils(counter_driver)
      virtual counter_if vif;

      function new(string name, uvm_component parent);
         super.new(name, parent);
      endfunction

      virtual function void build_phase(uvm_phase phase);
         super.build_phase(phase);
         if (!uvm_config_db#(virtual counter_if)::get(this, "", "vif", vif))
            `uvm_fatal("DRV", "Virtual interface not found")
      endfunction

      virtual task run_phase(uvm_phase phase);
         forever begin
            seq_item_port.get_next_item(req);
            `uvm_info("DRV", $sformatf("Driving: %s", req.convert2string()), UVM_HIGH)
            vif.rst_n <= req.rst_n;
            @(posedge vif.clk);
            seq_item_port.item_done();
         end
      endtask
   endclass

   // Monitor
   class counter_monitor extends uvm_monitor;
      `uvm_component_utils(counter_monitor)
      virtual counter_if vif;
      uvm_analysis_port#(counter_transaction) ap;

      function new(string name, uvm_component parent);
         super.new(name, parent);
         ap = new("ap", this);
      endfunction

      virtual function void build_phase(uvm_phase phase);
         super.build_phase(phase);
         if (!uvm_config_db#(virtual counter_if)::get(this, "", "vif", vif))
            `uvm_fatal("MON", "Virtual interface not found")
      endfunction

      virtual task run_phase(uvm_phase phase);
         forever begin
            counter_transaction tr;
            @(posedge vif.clk);
            tr = counter_transaction::type_id::create("tr");
            tr.rst_n = vif.rst_n;
            tr.count = vif.count;
            ap.write(tr);
         end
      endtask
   endclass

   // Sequencer
   typedef uvm_sequencer#(counter_transaction) counter_sequencer;

   // Agent
   class counter_agent extends uvm_agent;
      `uvm_component_utils(counter_agent)
      counter_driver    driver;
      counter_monitor   monitor;
      counter_sequencer sequencer;

      function new(string name, uvm_component parent);
         super.new(name, parent);
      endfunction

      virtual function void build_phase(uvm_phase phase);
         super.build_phase(phase);
         monitor = counter_monitor::type_id::create("monitor", this);
         if (get_is_active() == UVM_ACTIVE) begin
            driver    = counter_driver::type_id::create("driver", this);
            sequencer = counter_sequencer::type_id::create("sequencer", this);
         end
      endfunction

      virtual function void connect_phase(uvm_phase phase);
         if (get_is_active() == UVM_ACTIVE) begin
            driver.seq_item_port.connect(sequencer.seq_item_export);
         end
      endfunction
   endclass

   // Scoreboard
   class counter_scoreboard extends uvm_scoreboard;
      `uvm_component_utils(counter_scoreboard)
      uvm_analysis_imp#(counter_transaction, counter_scoreboard) item_collected_export;
      
      logic [3:0] expected_count = 0;
      bit first_active = 0;

      function new(string name, uvm_component parent);
         super.new(name, parent);
         item_collected_export = new("item_collected_export", this);
      endfunction

      virtual function void write(counter_transaction tr);
         if (!tr.rst_n) begin
            expected_count = 0;
            //`uvm_info("SB", "Reset seen", UVM_LOW)
         end else if (first_active) begin
            expected_count = (expected_count + 1) & 4'hF;
         end
         
         if (tr.rst_n) first_active = 1;

         if (tr.count !== expected_count) begin
            `uvm_error("SB", $sformatf("Mismatch! Seen: %0h, Expected: %0h", tr.count, expected_count))
         end else begin
            `uvm_info("SB", $sformatf("Match: %0h", tr.count), UVM_LOW)
         end
      endfunction
   endclass

   // Env
   class counter_env extends uvm_env;
      `uvm_component_utils(counter_env)
      counter_agent      agent;
      counter_scoreboard scoreboard;

      function new(string name, uvm_component parent);
         super.new(name, parent);
      endfunction

      virtual function void build_phase(uvm_phase phase);
         super.build_phase(phase);
         agent = counter_agent::type_id::create("agent", this);
         scoreboard = counter_scoreboard::type_id::create("scoreboard", this);
      endfunction

      virtual function void connect_phase(uvm_phase phase);
         agent.monitor.ap.connect(scoreboard.item_collected_export);
      endfunction
   endclass

   // Test
   class counter_test extends uvm_test;
      `uvm_component_utils(counter_test)
      counter_env env;

      function new(string name, uvm_component parent);
         super.new(name, parent);
      endfunction

      virtual function void build_phase(uvm_phase phase);
         super.build_phase(phase);
         env = counter_env::type_id::create("env", this);
      endfunction

      task run_phase(uvm_phase phase);
         counter_reset_sequence seq;
         phase.raise_objection(this);
         seq = counter_reset_sequence::type_id::create("seq");
         `uvm_info("TEST", "Starting sequence", UVM_LOW)
         seq.start(env.agent.sequencer);
         #200ns;
         `uvm_info("TEST", "Finished sequence", UVM_LOW)
         phase.drop_objection(this);
      endtask
   endclass

   // HW signals
   logic clk;
   logic rst_n;

   counter_if cif(clk, rst_n);

   counter dut (
      .clk   (cif.clk),
      .rst_n (cif.rst_n),
      .count (cif.count)
   );

   initial begin
      clk = 0;
      forever #5 clk = ~clk;
   end

   initial begin
      // Interface setup
      uvm_config_db#(virtual counter_if)::set(null, "uvm_test_top.*", "vif", cif);
      run_test("counter_test");
   end

endmodule
