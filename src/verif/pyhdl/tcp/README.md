# TCP Model Co-Simulation Testbench

Two Python [`tcp_model`](../tcp_model/) `TcpEngine` instances establish a
connection and exchange data **through a SystemVerilog transport**, with UVM
providing the transaction plumbing and pyhdl-if bridging the two languages.

There is no RTL DUT. What is under test is the integration itself: the TCP model
running on simulator time, the pyhdl-if ↔ UVM pattern, and the byte-level SV
transport between the two sides.

See [`TEST_PLAN.md`](TEST_PLAN.md) for the full plan, findings and risk log, and
[`../best_practices.md`](../best_practices.md) for the general pyhdl-if
guidance this testbench produced.

## Topology

```
              Python - one interpreter, one asyncio loop
   TcpEngine A  ──tx──►  session seq A          session seq B  ◄──tx── TcpEngine B
        ▲                     │                        │                    ▲
        │ on_segment          │ start_item             │ start_item         │
        └────────── relay ◄───┼────────────┐  ┌────────┼──► relay ──────────┘
  ══════════════════════════  │  ══════════│══│════════│═══════════════════════
                          driver A      monitor A   driver B    monitor B   SV
                              │              ▲          │           ▲
                            tcp_if A ────────┼──────────┘           │
                                             └──────── tcp_if B ────┘
                                    (cross-wired: A's TX is B's RX)
```

Every segment — SYN, ACK, data, FIN, retransmission — crosses the wire. The
engines never talk to each other directly.

## Tests

All share one compiled binary; select with `+UVM_TESTNAME`.

| UVM test | Plan ID | What it proves |
|---|---|---|
| `tcp_smoke_test` | T0 | Time service advances sim time; field transport round-trips; queue element widths are pinned; one segment crosses A→B byte-exact |
| `tcp_transport_test` | P2 | Canned segments both directions; scoreboard checks each stream; sequential `wait_ns` accumulates exactly |
| `tcp_handshake_test` | T1 | Three-way handshake over the wire; both engines `ESTABLISHED`; sequence spaces agree |
| `tcp_data_test` | T2 | `+num_msgs` application messages A→B, byte-exact |
| `tcp_bidir_test` | T3 | `+num_msgs` messages in **each** direction, interleaved |
| `tcp_teardown_test` | T4 | Both sides close and reach `CLOSED`, one via TIME-WAIT expiry |
| `tcp_loss_test` | T5 | Data segments dropped at fixed indices; retransmission recovers every byte |
| `tcp_raw_test` | T6 | scapy builds 16 segments; Python sends each as one `bytes_item` and SV rebuilds the `tcp_item` via `from_bytes()` — no field named on the Python side |
| `tcp_raw_error_test` | T7 | Malformed images are rejected by `from_bytes()`, never reach the wire, and the proxy recovers: 3 rejected, then 2 good segments delivered |

## Running

```bash
pytest tests/test_tcp.py                    # all of the above
pytest tests/test_tcp.py -k bidir           # one
pytest tests/test_tcp.py --waves            # + VCD

# directly, for iteration (build first via pytest or fusesoc)
./build/tb_tcp_uvm_pyhdl/Vtb_tcp_uvm_pyhdl +UVM_TESTNAME=tcp_bidir_test \
    +num_msgs=100 +seed=7 +UVM_VERBOSITY=UVM_HIGH
```

| Plusarg | Default | Meaning |
|---|---|---|
| `+UVM_TESTNAME` | `tcp_smoke_test` | Which test to run |
| `+num_msgs` | 1000 | Application messages per direction |
| `+seed` | 1 | Stimulus RNG seed |
| `+max_msg` | 512 | Largest message in bytes |

At the default `+num_msgs=1000`, `tcp_bidir_test` moves ~250 kB per direction
across ~3000 segments each way in about 46 s wall clock.

## Reading the log

Each run writes `build/tb_tcp_uvm_pyhdl/sim.log` containing **both** languages'
output, in the order it was produced and stamped with simulation time:

```
UVM_INFO ... @ 0ns: reporter [RNTST] Running test tcp_handshake_test...
PY_INFO @ 100ns: [tcp] open: A=SYN_SENT B=LISTEN
PY_INFO @ 200835ns: [tcp] after handshake: A=ESTABLISHED B=ESTABLISHED
UVM_INFO tcp_test.sv(198) @ 202835ns: uvm_test_top [uvm_test_top] handshake segments: 2 A->B, 1 B->A
```

`PY_` lines come from Python, `UVM_` from SystemVerilog; sort by the `@ <n>ns`
stamp to follow a segment across the boundary. Getting the two streams to
interleave at all takes some care — see best practices §6.1 if you are adding a
runner of your own.

## Files

| File | Role |
|---|---|
| `tcp_item.sv` | `seq_item_serializable` mirroring `TcpSegment`; byte-queue `options`/`payload`; `to_bytes()`/`from_bytes()` wire codec |
| `tcp_item_mirror.py` | Python mirror declaring queue element widths (8 bits) |
| `tcp_if.sv` | Byte-stream interface (`valid`/`data`/`last`) per direction |
| `tcp_driver.sv` / `tcp_monitor.sv` | Serialize (`to_bytes`) / reassemble (`from_bytes`) segments, one byte per clock |
| `tcp_scoreboard.sv` | Byte-exact in-order transport check, both directions |
| `tcp_agent.sv` / `tcp_env.sv` | Per-side agent; env with two agents + scoreboard |
| `tcp_py_seq.sv` | Field-path sequence proxy, specialized for `tcp_item` (see best practices §4.1) |
| [`../pyhdl_raw.sv`](../pyhdl_raw.sv) | `seq_item_serializable` base, `bytes_item` carrier, generic `pyhdl_raw_seq` (§5.5) |
| [`../raw_mirror.py`](../raw_mirror.py) | Python side of the raw path: `bytes_item` mirror, `send_raw()` |
| `pyhdl_uvm_vlt.sv` | Entry point that patches one pyhdl-if macro (§4.2) |
| `tcp_time_service.sv` | `now_ns` / `wait_ns` exposed to Python |
| `tcp_py_relay.sv` | Hands monitored segment bytes to the Python runner |
| `tcp_test.sv` | Test library; `tcp_base_test` owns the Python bootstrap |
| `test_runner.py` | Call API, session sequences, `TimeMux`, engines, checks |
| [`../sim_logging.py`](../sim_logging.py) | Sim-time-stamped logging that interleaves with SV output |

## Things worth knowing before editing

- **One pyhdl-if imp task at a time.** Every SV-blocking call takes `sv_lock()`
  in `test_runner.py`; overlapping two wedges Verilator. Best practices §1.
- **Side A pumps time**, in short quanta, so side B is not starved behind the
  lock. Side B is purely reactive.
- Both engines live in one interpreter, so side A's sequence makes the *app*
  calls for both peers. Only the resulting segments are protocol traffic, and
  those still cross the wire from their own side's sequencer.
- `uvm_default_packer.use_metadata = 1` is load bearing for queue fields (§5.2).
  `tcp_base_test` sets it in `build_phase`; `pyhdl_raw_seq` also sets it itself
  so the raw path works in a testbench that never needed it. It is global state
  on `uvm_default_packer` — the sequence announces it when it changes anything.
- Engines run at `snd_mss=256` with millisecond-scaled timers so runs finish in
  microseconds-to-milliseconds of simulation time rather than RFC seconds.
