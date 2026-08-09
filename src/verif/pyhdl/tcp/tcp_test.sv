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

   // Stimulus knobs, overridable from the command line
   int unsigned num_msgs = 1000;
   int unsigned seed = 1;
   int unsigned max_msg = 512;

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

      void'($value$plusargs("num_msgs=%d", num_msgs));
      void'($value$plusargs("seed=%d", seed));
      void'($value$plusargs("max_msg=%d", max_msg));
      py_runner.configure(longint'(num_msgs), longint'(seed), longint'(max_msg));

      run_body();

      phase.drop_objection(this);
   endtask

   virtual task run_body();
      `uvm_fatal(get_name(), "run_body() not implemented")
   endtask

   // Start a Python-bodied sequence on `seqr`. Blocks until its body returns.
   task start_py_seq(uvm_sequencer#(tcp_item) seqr, string pyclass, string name = "py_seq");
      tcp_py_seq seq;
      seq = tcp_py_seq::type_id::create(name);
      seq.pyclass = pyclass;
      seq.start(seqr);
   endtask

   // Same, on the raw-bytes path: Python sends a wire image and the item
   // deserializes itself (pyhdl_raw.sv). There is no tcp-specific sequence
   // class here -- pyhdl_raw_seq is generic and the item is a factory name.
   task start_raw_py_seq(uvm_sequencer_base seqr, string pyclass, string item_type,
                         string name = "raw_py_seq");
      pyhdl_raw_seq seq;
      seq = pyhdl_raw_seq::type_id::create(name);
      seq.pyclass = pyclass;
      seq.item_type = item_type;
      seq.start(seqr);
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
      start_py_seq(env.agent_a.sequencer, "test_runner::SmokeSeq");

      // Let the driver/monitor drain, then collect the Python verdict
      #2000ns;
      check_python_report();
      if (env.scoreboard.n_checked_ab != 1) begin
         `uvm_error(get_name(), $sformatf("Expected 1 A->B transport item, got %0d",
                                          env.scoreboard.n_checked_ab))
      end
   endtask
endclass

// P2: both directions driven concurrently with canned segments, so the
// scoreboard's A->B and B->A streams are both exercised. Also runs the
// time-service probe that fixes how P3 must drive simulation time.
class tcp_transport_test extends tcp_base_test;
   `uvm_component_utils(tcp_transport_test)

   localparam int unsigned XPORT_SEGMENTS = 4;

   function new(string name, uvm_component parent);
      super.new(name, parent);
   endfunction

   virtual task run_body();
      start_py_seq(env.agent_a.sequencer, "test_runner::TimeServiceProbe", "probe");

      fork
         start_py_seq(env.agent_a.sequencer, "test_runner::XportSeqA", "seq_a");
         start_py_seq(env.agent_b.sequencer, "test_runner::XportSeqB", "seq_b");
      join

      #5000ns;
      check_python_report();

      if (env.scoreboard.n_checked_ab != XPORT_SEGMENTS) begin
         `uvm_error(get_name(), $sformatf("Expected %0d A->B transport items, got %0d",
                                          XPORT_SEGMENTS, env.scoreboard.n_checked_ab))
      end
      if (env.scoreboard.n_checked_ba != XPORT_SEGMENTS) begin
         `uvm_error(get_name(), $sformatf("Expected %0d B->A transport items, got %0d",
                                          XPORT_SEGMENTS, env.scoreboard.n_checked_ba))
      end
      if (env.scoreboard.n_errors != 0) begin
         `uvm_error(get_name(), $sformatf("Scoreboard reported %0d error(s)",
                                          env.scoreboard.n_errors))
      end
   endtask
endclass

// T6: the raw-bytes transport path. Python builds segments with scapy and
// sends each one as a single byte queue; the generic pyhdl_raw_seq rebuilds the
// item on this side via the item's own from_bytes(). Nothing on the Python
// side names a TCP field, and nothing in SV is written for tcp_item's sake --
// the type is just a factory name.
//
// The check is still field-level even though no field is named anywhere: the
// driver re-serializes the reconstructed item with to_bytes(), so a field
// that came back wrong changes the bytes the far-side monitor sees, and both
// the scoreboard and Python's report() catch it.
class tcp_raw_test extends tcp_base_test;
   `uvm_component_utils(tcp_raw_test)

   localparam int unsigned RAW_SEGMENTS = 16;

   function new(string name, uvm_component parent);
      super.new(name, parent);
   endfunction

   virtual task run_body();
      start_raw_py_seq(env.agent_a.sequencer, "test_runner::RawScapySeq", "tcp_item", "raw_seq");

      #5000ns;
      check_python_report();

      if (env.scoreboard.n_checked_ab != RAW_SEGMENTS) begin
         `uvm_error(get_name(), $sformatf("Expected %0d A->B transport items, got %0d",
                                          RAW_SEGMENTS, env.scoreboard.n_checked_ab))
      end
      if (env.scoreboard.n_errors != 0) begin
         `uvm_error(get_name(), $sformatf("Scoreboard reported %0d error(s)",
                                          env.scoreboard.n_errors))
      end
   endtask
endclass

// T7: the raw path's *failure* behaviour, which the happy-path test cannot show.
//
// Python sends three images that are legal as transport (byte queues of legal
// length) but malformed to tcp_item::from_bytes(), then two good segments. The
// test asserts all three were rejected, that nothing reached the wire for them,
// and that the good segments still went through -- i.e. the proxy recovered
// rather than wedging or re-driving the last successfully decoded item.
//
// The rejections are *expected* errors, so they are counted and then cleared
// from the report server. Anything left in the count afterwards is a real
// failure, which keeps the pytest "UVM_ERROR : 0" contract intact.
class tcp_raw_error_test extends tcp_base_test;
   `uvm_component_utils(tcp_raw_error_test)

   localparam int unsigned CORRUPT_IMAGES = 3;
   localparam int unsigned RECOVERY_SEGMENTS = 2;

   function new(string name, uvm_component parent);
      super.new(name, parent);
   endfunction

   virtual task run_body();
      uvm_report_server rs;
      int n_decode_err;
      int n_wire;

      start_raw_py_seq(env.agent_a.sequencer, "test_runner::RawCorruptSeq", "tcp_item", "raw_seq");

      #5000ns;

      // Snapshot and clear before reporting anything of our own, so a failure
      // raised below is not wiped along with the expected rejections.
      rs           = uvm_report_server::get_server();
      n_decode_err = rs.get_severity_count(UVM_ERROR);
      rs.set_severity_count(UVM_ERROR, 0);

      n_wire = env.scoreboard.n_checked_ab;

      if (n_decode_err != CORRUPT_IMAGES) begin
         `uvm_error(get_name(), $sformatf("Expected %0d rejected images, saw %0d UVM_ERROR(s)",
                                          CORRUPT_IMAGES, n_decode_err))
      end
      if (n_wire != RECOVERY_SEGMENTS) begin
         `uvm_error(get_name(),
                    $sformatf(
                        {"Expected %0d segments on the wire (malformed images must not reach it, ",
                         "good ones must); scoreboard checked %0d"}, RECOVERY_SEGMENTS, n_wire))
      end
      if (env.scoreboard.n_errors != 0) begin
         `uvm_error(get_name(), $sformatf("Scoreboard reported %0d error(s)",
                                          env.scoreboard.n_errors))
      end

      check_python_report();

      `uvm_info(
          get_name(),
          $sformatf(
              "raw error test: %0d/%0d malformed images rejected, %0d/%0d good segments delivered",
              n_decode_err, CORRUPT_IMAGES, n_wire, RECOVERY_SEGMENTS), UVM_NONE)
   endtask
endclass

// T1 (P3): two Python TcpEngine instances complete a three-way handshake with
// every segment carried across the SystemVerilog wire. Side A opens the
// connection and pumps simulation time; side B reacts. Both sequences run
// concurrently on their own sequencers.
class tcp_handshake_test extends tcp_base_test;
   `uvm_component_utils(tcp_handshake_test)

   function new(string name, uvm_component parent);
      super.new(name, parent);
   endfunction

   virtual task run_body();
      fork
         start_py_seq(env.agent_a.sequencer, "test_runner::HandshakeSeqA", "seq_a");
         start_py_seq(env.agent_b.sequencer, "test_runner::EngineSeqB", "seq_b");
      join

      #2000ns;
      check_python_report();

      // SYN (A->B), SYN-ACK (B->A), ACK (A->B)
      if (env.scoreboard.n_checked_ab < 2) begin
         `uvm_error(get_name(), $sformatf("Expected >=2 A->B segments (SYN, ACK), got %0d",
                                          env.scoreboard.n_checked_ab))
      end
      if (env.scoreboard.n_checked_ba < 1) begin
         `uvm_error(get_name(), $sformatf("Expected >=1 B->A segment (SYN-ACK), got %0d",
                                          env.scoreboard.n_checked_ba))
      end
      if (env.scoreboard.n_errors != 0) begin
         `uvm_error(get_name(), $sformatf("Scoreboard reported %0d error(s)",
                                          env.scoreboard.n_errors))
      end
      `uvm_info(get_name(), $sformatf("handshake segments: %0d A->B, %0d B->A",
                                      env.scoreboard.n_checked_ab, env.scoreboard.n_checked_ba),
                UVM_LOW)
   endtask
endclass

// T2/T3 (P4): application data across the established connection. Side A's
// sequence orchestrates both peers' app-level send() calls and pumps time;
// every resulting segment still crosses the wire from its own side's
// sequencer. Message count via +num_msgs, RNG via +seed, size cap via
// +max_msg.
class tcp_data_test extends tcp_base_test;
   `uvm_component_utils(tcp_data_test)

   // Python class implementing side A's session; overridden by the bidir test
   string seq_a_pyclass = "test_runner::DataSeqA";

   function new(string name, uvm_component parent);
      super.new(name, parent);
   endfunction

   virtual task run_body();
      fork
         start_py_seq(env.agent_a.sequencer, seq_a_pyclass, "seq_a");
         start_py_seq(env.agent_b.sequencer, "test_runner::EngineSeqB", "seq_b");
      join

      #2000ns;
      check_python_report();

      if (env.scoreboard.n_errors != 0) begin
         `uvm_error(get_name(), $sformatf("Scoreboard reported %0d error(s)",
                                          env.scoreboard.n_errors))
      end
      `uvm_info(get_name(), $sformatf("segments on the wire: %0d A->B, %0d B->A",
                                      env.scoreboard.n_checked_ab, env.scoreboard.n_checked_ba),
                UVM_LOW)
   endtask
endclass

class tcp_bidir_test extends tcp_data_test;
   `uvm_component_utils(tcp_bidir_test)

   function new(string name, uvm_component parent);
      super.new(name, parent);
      seq_a_pyclass = "test_runner::BidirSeqA";
   endfunction
endclass

// T4 (P5): graceful teardown. Some data, then both sides close; the
// connection must reach CLOSED, one side through TIME-WAIT expiry.
class tcp_teardown_test extends tcp_data_test;
   `uvm_component_utils(tcp_teardown_test)

   function new(string name, uvm_component parent);
      super.new(name, parent);
      seq_a_pyclass = "test_runner::TeardownSeqA";
   endfunction
endclass

// T5 (P5): segment loss. Side A's sequence drops whole data-bearing segments
// at fixed indices, so retransmission has to recover them. Those segments
// never reach the wire, so the A->B scoreboard stream is expected to be
// short by exactly the number dropped -- Python owns the data check here.
class tcp_loss_test extends tcp_data_test;
   `uvm_component_utils(tcp_loss_test)

   function new(string name, uvm_component parent);
      super.new(name, parent);
      seq_a_pyclass = "test_runner::LossSeqA";
   endfunction

   virtual task run_body();
      fork
         start_py_seq(env.agent_a.sequencer, seq_a_pyclass, "seq_a");
         start_py_seq(env.agent_b.sequencer, "test_runner::EngineSeqB", "seq_b");
      join

      #2000ns;
      check_python_report();

      // Dropped segments are never driven, so driver and far monitor still
      // agree byte-for-byte on everything that *was* driven.
      if (env.scoreboard.n_errors != 0) begin
         `uvm_error(get_name(), $sformatf("Scoreboard reported %0d error(s)",
                                          env.scoreboard.n_errors))
      end
      `uvm_info(get_name(), $sformatf("segments on the wire: %0d A->B, %0d B->A",
                                      env.scoreboard.n_checked_ab, env.scoreboard.n_checked_ba),
                UVM_LOW)
   endtask
endclass
