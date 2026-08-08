# tcp_model — standalone RFC 9293-oriented TCP engine

A synchronous, runtime-agnostic TCP protocol model in pure Python. This is the
**standalone** half of the TCP-model project (Plan B, milestones B0–B5): the core
engine plus a deterministic test harness. The pyhdl-if simulation adapters and the
Linux/TUN interop harness are deliberately **not** included here — they are separate
work streams that plug into the same engine through its ports.

## Why it's built this way

The engine core never blocks, never awaits, and never reads a wall clock. It reaches
the outside world only through three injected ports, so the identical code runs under
a SystemVerilog simulator and under plain pytest:

| Port | Direction | In simulation | Standalone (here) |
|------|-----------|---------------|-------------------|
| **App**       | user → engine | UVM component proxy | direct method calls |
| **Net**       | both | `tcp_seq_item.pack()/unpack()` over pyhdl-if | `tx` callable + `on_segment` |
| **Scheduler** | engine → time | SV `wait_ns`/`now_ns` | `ManualScheduler` (virtual time) |

Because the engine is synchronous, every entry point (`send`, `on_segment`, a timer
firing) runs to completion atomically — no interleaving to reason about.

The engine **does not compute or validate the TCP checksum**: that needs the IP
pseudo-header, which the engine never sees. The net adapter owns it (the SV IP layer
in simulation, the IP/TUN adapter standalone).

## What's implemented

- Connection FSM: all 11 states (`tcp_model.ConnState`).
- Handshake: active open, passive open, simultaneous open, MSS option exchange,
  SEND-in-LISTEN passive→active conversion.
- **State machine cross-checked** against the EFSM/SDL model of Zaghal & Khan
  (Kent State TR2005-07-22): every paper transition is a cited test, and every
  RFC-9293-vs-paper disagreement is catalogued in docs section 9.
- **Three interchangeable clocks**: `ManualScheduler` (virtual),
  `WallClockScheduler` (real time), and `SimScheduler`+`SpoofClock` — an
  external-clock harness shaped exactly like the eventual pyhdl-if time
  service (`now_ns`/`wait_ns`).
- Data path: MSS segmentation, PSH, cumulative ACKs, send-window flow control,
  out-of-order reassembly, in-order delivery, duplicate-data suppression,
  urgent data (SND.UP/RCV.UP, URG + pointer).
- Retransmission: RFC 6298 RTO estimator (SRTT/RTTVAR), exponential backoff,
  Karn's algorithm, give-up after `max_retransmits`, enforced user timeout
  (RFC 9293 3.8.3).
- Zero-window persist probing; keepalive probes (RFC 9293 3.8.4, default off);
  Nagle (RFC 9293 3.7.4, configurable, default off for stimulus determinism);
  receiver SWS avoidance (RFC 9293 3.8.6.2.2, default on).
- Teardown: active / passive / simultaneous close, FIN handling, TIME-WAIT (2·MSL),
  RST generation and handling (incl. RFC 5961-style challenge ACKs).
- `seed()` / `seed_established()` to drop the model into any state mid-stream —
  the migration path from manual seq/ack tracking.

The full RFC 9293 compliance matrix (including the deliberate deviations and
their rationale) is in `docs/DOCUMENTATION.md`, alongside the complete API
reference.

## Layout

```
tcp_model/
  core/
    seqnum.py     mod-2**32 arithmetic + acceptability test
    segment.py    TcpSegment + wire codec (single source of the wire format)
    tcb.py        ConnState, TCB, TcbSeed, seed_established
    timers.py     TimerConfig (all periods configurable)
    rtt.py        RFC 6298 RTO estimator
    engine.py     the TcpEngine (FSM + SEGMENT-ARRIVES + send/recv + timers)
  ports/
    scheduler.py  Scheduler protocol + ManualScheduler + WallClockScheduler
    net_port.py   CaptureNet (record/inject) + Wire (two engines, loss/reorder)
    sim_clock.py  ExternalTimeService + SimScheduler + SpoofClock (pyhdl-if seam)
  tests/unit/     pytest suite (207 tests: FSM matrix, EFSM-paper transitions,
                  wall-clock timers, spoofed-sim-clock harness)
  docs/           documentation + API reference + compliance matrix +
                  ARCHITECTURE.md (Mermaid diagram set)
```

## User interface — RFC 9293 §3.9.1

The public API mirrors the six TCP service calls exactly:

| RFC call | Method | Notes |
|----------|--------|-------|
| **OPEN**    | `open(active=True, *, remote_port=None, local_port=None, timeout=None)` | returns the local connection name; `active=False` ⇒ passive/LISTEN |
| **SEND**    | `send(data, push=True, urgent=False, timeout=None)` | `push` ⇒ PSH on the last segment; `urgent` ⇒ URG + urgent pointer |
| **RECEIVE** | `receive(max_len=65535) -> ReceiveResult(data, push, urgent)` | RFC return is byte count + PUSH + URGENT |
| **CLOSE**   | `close()` | |
| **ABORT**   | `abort()` | sends RST |
| **STATUS**  | `status() -> dict` | the RFC status-data block (state, sockets, windows, buffers awaiting ack / pending receipt, urgent state, transmission timeout) |

`active_open()` / `passive_open()` remain as thin wrappers over `open()`, and
`recv()` is a convenience wrapper over `receive()` returning just the bytes.
`timeout`, Diffserv, and security/compartment OPEN/SEND parameters are accepted
for interface parity but not enforced (the latter two are IP-layer concerns the
engine does not own).

Non-user-call surface: `on_segment(seg_bytes)` is the lower-layer "SEGMENT
ARRIVES" event (not a user call), and `seed()` / `seed_established()` are this
model's extension for starting mid-stream.

## Quick start

```python
from tcp_model import TcpEngine, ManualScheduler, CaptureNet

sched = ManualScheduler()
net = CaptureNet()
eng = TcpEngine(local_port=40000, remote_port=80, scheduler=sched,
                tx=net.tx, iss=1000)
net.bind(eng)

name = eng.open(active=True)   # OPEN -> emits SYN, returns connection name
# ... feed a SYN-ACK via eng.on_segment(...) ...
eng.send(b"hello", push=True)  # SEND
result = eng.receive()         # RECEIVE -> ReceiveResult(data, push, urgent)
info = eng.status()            # STATUS -> status data block
eng.close()                    # CLOSE
```

Seeding mid-stream (migration from manual tracking):

```python
from tcp_model import seed_established
eng.seed(seed_established(snd_nxt=my_seq, rcv_nxt=my_ack))
```

## Running the tests

From the directory that contains the `tcp_model` package:

```
python -m pytest tcp_model/tests/unit
```

The `ManualScheduler` makes timer-driven behavior (RTO, persist, TIME-WAIT) instant
and deterministic, so the whole suite runs in well under a second. The strongest
end-to-end checks wire two engine instances back-to-back through a controllable
channel (`test_two_engines.py`) covering handshake, bulk transfer, bidirectional
flow, packet loss recovery, sequence wraparound, and full close.
