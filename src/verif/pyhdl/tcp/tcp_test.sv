// Test library for the TCP co-simulation testbench.
//
// tcp_base_test owns the Python bootstrap: pyhdl-if startup, the time
// service, the runner API, and the monitor->Python relays. Concrete tests
// implement run_body().

class tcp_base_test extends uvm_test;
   `uvm_component_utils(tcp_base_test)

   tcp_env env;
   tcp_time_service ts;
   TcpRunnerAPI_exp_impl py_runner;
   tcp_py_relay relay_a;
   tcp_py_relay relay_b;

   // Sim-time watchdog: a stalled Python side otherwise hangs the run
   longint unsigned watchdog_ns = 500_000_000;

   function new(string name, uvm_component parent);
      super.new(name, parent);
   endfunction

   virtual function void build_phase(uvm_phase phase);
      super.build_phase(phase);

      // Queue fields only carry a 32-bit element count when the packer has
      // metadata enabled (uvm_pack_arrayN). pyhdl-if's Python model always
      // reads that count, so without this a queue unpacks into whatever size
      // it already had -- i.e. empty. pack_ints()/unpack_ints() go through
      // uvm_default_packer, so enabling it here is enough. Integral fields are
      // unaffected: uvm_pack_intN does not consult use_metadata.
      uvm_default_packer.use_metadata = 1;

      relay_a = new("relay_a");
      relay_a.side = 0;
      relay_b = new("relay_b");
      relay_b.side = 1;
      uvm_config_db#(tcp_py_relay)::set(this, "env.agent_a.monitor", "relay", relay_a);
      uvm_config_db#(tcp_py_relay)::set(this, "env.agent_b.monitor", "relay", relay_b);

      env = tcp_env::type_id::create("env", this);
   endfunction

   task run_phase(uvm_phase phase);
      phase.raise_objection(this);

      fork
         begin
            #(watchdog_ns * 1ns);
            `uvm_fatal(get_name(), "Watchdog expired: Python side stalled")
         end
      join_none

      // Defer Python startup past time 0 (async imp calls at t=0 can deadlock)
      #100ns;
      pyhdl_if_start();
      ts             = new();
      py_runner      = new();
      relay_a.runner = py_runner;
      relay_b.runner = py_runner;
      py_runner.init_ts(ts.api.m_obj);

      run_body();

      phase.drop_objection(this);
   endtask

   virtual task run_body();
      `uvm_fatal(get_name(), "run_body() not implemented")
   endtask

   // Ask Python for its final verdict; any nonzero count fails the test.
   task check_python_report();
      longint n_err;
      n_err = py_runner.report();
      if (n_err != 0) begin
         `uvm_error(get_name(), $sformatf("Python reported %0d error(s)", n_err))
      end
   endtask
endclass

// T0: time service + shipped proxy sequence + transport, no TCP engines.
// Python's SmokeSeq checks wait_ns advances sim time, then pushes one canned
// segment through create_req/pack/unpack/start_item/finish_item. The segment
// crosses the wire A->B; Python compares the monitored bytes against the
// tcp_model codec's build() image.
class tcp_smoke_test extends tcp_base_test;
   `uvm_component_utils(tcp_smoke_test)

   function new(string name, uvm_component parent);
      super.new(name, parent);
   endfunction

   virtual task run_body();
      tcp_py_seq seq;
      seq = tcp_py_seq::type_id::create("seq");
      seq.pyclass = "test_runner::SmokeSeq";
      seq.start(env.agent_a.sequencer);

      // Let the driver/monitor drain, then collect the Python verdict
      #2000ns;
      check_python_report();
      if (env.scoreboard.n_checked_ab != 1) begin
         `uvm_error(get_name(), $sformatf("Expected 1 A->B transport item, got %0d",
                                          env.scoreboard.n_checked_ab))
      end
   endtask
endclass
