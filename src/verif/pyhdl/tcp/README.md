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

## Files

| File | Role |
|---|---|
| `tcp_item.sv` | `uvm_sequence_item` mirroring `TcpSegment`; byte-queue `options`/`payload`; wire codec |
| `tcp_item_mirror.py` | Python mirror declaring queue element widths (8 bits) |
| `tcp_if.sv` | Byte-stream interface (`valid`/`data`/`last`) per direction |
| `tcp_driver.sv` / `tcp_monitor.sv` | Serialize / reassemble segments, one byte per clock |
| `tcp_scoreboard.sv` | Byte-exact in-order transport check, both directions |
| `tcp_agent.sv` / `tcp_env.sv` | Per-side agent; env with two agents + scoreboard |
| `tcp_py_seq.sv` | Sequence proxy specialized for `tcp_item` (see best practices §4.1) |
| `pyhdl_uvm_vlt.sv` | Entry point that patches one pyhdl-if macro (§4.2) |
| `tcp_time_service.sv` | `now_ns` / `wait_ns` exposed to Python |
| `tcp_py_relay.sv` | Hands monitored segment bytes to the Python runner |
| `tcp_test.sv` | Test library; `tcp_base_test` owns the Python bootstrap |
| `test_runner.py` | Call API, session sequences, `TimeMux`, engines, checks |

## Things worth knowing before editing

- **One pyhdl-if imp task at a time.** Every SV-blocking call takes `sv_lock()`
  in `test_runner.py`; overlapping two wedges Verilator. Best practices §1.
- **Side A pumps time**, in short quanta, so side B is not starved behind the
  lock. Side B is purely reactive.
- Both engines live in one interpreter, so side A's sequence makes the *app*
  calls for both peers. Only the resulting segments are protocol traffic, and
  those still cross the wire from their own side's sequencer.
- `uvm_default_packer.use_metadata = 1` is set in `build_phase` and is load
  bearing for queue fields (§5.2).
- Engines run at `snd_mss=256` with millisecond-scaled timers so runs finish in
  microseconds-to-milliseconds of simulation time rather than RFC seconds.
