# tcp_model — Documentation

A synchronous, runtime-agnostic, RFC 9293-oriented TCP protocol engine in pure
Python, built for hardware-verification testbenches. The same engine core runs
under a SystemVerilog/UVM simulation (behind a pyhdl-if adapter layer) and
standalone under pytest — with zero code differences in the core.

This document covers the model boundary only. The UVM/pyhdl-if adapter layer is a
separate deliverable with its own plan (see the implementation plan document);
nothing in this package imports or references pyhdl-if, UVM, or any simulator.

---

## 1. Design principles

1. **Synchronous core.** Every public entry point (`send`, `on_segment`, a timer
   callback) runs to completion with no `await`, no threads, no reentrancy from
   the engine itself. All sequencing pressure lives in the environment. This is
   what makes the engine trivially portable between an asyncio-backed simulator
   loop and a plain unit test.
2. **Ports, not dependencies.** The engine reaches the outside world through
   three injected ports (App, Net, Scheduler). It never reads a wall clock, never
   sleeps, never opens a socket.
3. **One wire codec.** `core/segment.py` is the single definition of the TCP wire
   format. In simulation the same bytes cross the pyhdl-if boundary
   (`tcp_seq_item.pack()`/`unpack()` are wire-format); standalone the same bytes
   go over a test channel or TUN device. There is no second parser to drift.
4. **No checksum in the engine.** The TCP checksum requires the IP pseudo-header,
   which the engine never sees. The checksum field is carried verbatim (0 on
   build); the net adapter owns compute/verify (SV IP layer in simulation,
   TUN/IP adapter standalone).
5. **RFC 9293 user interface, exactly.** The public API is the six service calls
   of §3.9.1 — OPEN, SEND, RECEIVE, CLOSE, ABORT, STATUS — with their RFC
   parameters and results. Convenience wrappers exist but never replace the
   primitives.
6. **Determinism first.** ISS is configurable, all timer periods are
   configurable, delayed ACK defaults to immediate, and Nagle defaults off. With
   fixed configuration the engine's output byte stream is a pure function of its
   inputs — the property that makes A/B comparison against legacy testbench
   tracking possible.

## 2. Architecture

> Full diagram set (ports view, FSM, SEGMENT-ARRIVES pipeline, send/receive
> paths, clock stack, pyhdl-if rendezvous): **`docs/ARCHITECTURE.md`**.

```
        App port (RFC 9293 user calls)          Scheduler port
   open/send/receive/close/abort/status       now/call_later/cancel
                   │                                  ▲
                   ▼                                  │
             ┌──────────────────────────────────────────────┐
             │                TcpEngine (sync)              │
             │  FSM · SEGMENT-ARRIVES · send/recv paths ·   │
             │  RFC6298 RTO · persist · keepalive · SWS     │
             └──────────────────────────────────────────────┘
                   │                                  ▲
                   ▼                                  │
              tx(bytes)  ── Net port ──  on_segment(bytes)
```

| Port | Direction | Simulation binding | Standalone binding |
|---|---|---|---|
| App | user → engine | UVM component proxy (pyhdl-if) | direct Python calls |
| Net TX | engine → wire | proxy sequence → `tcp_seq_item.unpack(bytes)` → sequencer | `CaptureNet.tx` / `Wire` / TUN write |
| Net RX | wire → engine | monitor subscriber → `on_segment(item.pack())` | `CaptureNet.inject` / `Wire` / TUN read |
| Scheduler | engine → time | asyncio loop + SV `wait_ns` service | `ManualScheduler` (virtual time) |

### Module layout

```
tcp_model/
  __init__.py          public re-exports
  core/
    seqnum.py          mod-2**32 arithmetic + RFC 9293 3.10.7.4 acceptability
    segment.py         TcpSegment dataclass + wire codec (build/parse, options)
    tcb.py             ConnState, TCB, TcbSeed, seed_established
    timers.py          TimerConfig (all periods + behavior knobs)
    rtt.py             RFC 6298 SRTT/RTTVAR/RTO estimator
    engine.py          TcpEngine: FSM, SEGMENT ARRIVES, send/recv, timers
  ports/
    scheduler.py       Scheduler protocol, TimerHandle, ManualScheduler,
                       WallClockScheduler
    net_port.py        CaptureNet (record/inject), Wire (two-engine channel)
    sim_clock.py       ExternalTimeService, SimScheduler, SpoofClock
  tests/unit/          207-test pytest suite
  docs/                this document + ARCHITECTURE.md (diagrams)
```

## 3. Lifecycle and threading model

The engine is **single-threaded by contract**: all calls into one engine instance
must come from one execution context (in simulation, the pyhdl-if asyncio loop;
in tests, the test function). Timer callbacks fire synchronously inside
`scheduler.advance()`/the loop, never concurrently.

Callbacks (`on_data`, `on_state_change`, `on_error`) are invoked synchronously
from inside engine entry points. Keep them light. Calling back into the engine
from a callback is supported for reads; if you must mutate (e.g. `send` from
`on_data`), prefer deferring via `scheduler.call_later(0, ...)` to keep event
ordering obvious.

A connection's life:

```
CLOSED ──open(active=True)──> SYN_SENT ──SYN-ACK──> ESTABLISHED
CLOSED ──open(active=False)─> LISTEN ──SYN──> SYN_RECEIVED ──ACK──> ESTABLISHED
LISTEN ──send()────────────-> SYN_SENT            (passive→active conversion)
ESTABLISHED ──close()──> FIN_WAIT_1 → FIN_WAIT_2 → TIME_WAIT ──2·MSL──> CLOSED
ESTABLISHED ──peer FIN──> CLOSE_WAIT ──close()──> LAST_ACK ──ACK──> CLOSED
```

EOF signaling: when the peer's FIN is consumed, `on_data(b"")` is delivered once
as an end-of-stream marker (streaming sinks should treat empty bytes as EOF).

## 4. Using the model

### 4.1 Directed / scripted (CaptureNet)

```python
from tcp_model import TcpEngine, ManualScheduler, CaptureNet, TcpSegment, Flags

sched = ManualScheduler()
net = CaptureNet()
eng = TcpEngine(local_port=40000, remote_port=80, scheduler=sched,
                tx=net.tx, iss=1000)
net.bind(eng)

eng.open(active=True)                    # OPEN: emits SYN -> net.last
synack = TcpSegment(src_port=80, dst_port=40000, seq=5000, ack=1001,
                    flags=Flags.SYN | Flags.ACK, window=65535)
eng.on_segment(synack.build())           # SEGMENT ARRIVES
eng.send(b"hello", push=True)            # SEND
r = eng.receive()                        # RECEIVE -> (data, push, urgent)
print(eng.status()["connection_state"])  # STATUS
eng.close()                              # CLOSE
```

### 4.2 Two engines end-to-end (Wire)

```python
from tcp_model import TcpEngine, ManualScheduler, Wire

sched = ManualScheduler()
wire = Wire(sched, delay=0.001)          # optional loss/reorder filters
a = TcpEngine(local_port=1, remote_port=2, scheduler=sched, tx=wire.tx_from_a, iss=100)
b = TcpEngine(local_port=2, remote_port=1, scheduler=sched, tx=wire.tx_from_b, iss=200,
              on_data=print)
wire.connect(a, b)
b.open(active=False); a.open(active=True)
sched.run_until_idle()                   # handshake completes in virtual time
a.send(b"payload")
sched.run_until_idle()
```

### 4.3 Seeding mid-stream (migration from manual tracking)

Replace hand-tracked sequence numbers by dropping the engine into ESTABLISHED at
the current stream position:

```python
from tcp_model import seed_established
eng.seed(seed_established(snd_nxt=current_seq, rcv_nxt=current_ack))
```

`TcbSeed` allows any state/variable combination for corner-case setups (e.g.
start in CLOSE_WAIT to test the tail of a teardown).

### 4.4 Determinism checklist

For byte-identical replays (and A/B comparison against a legacy implementation):
fix `iss`, keep `delayed_ack=None`, keep `nagle=False`, avoid `keepalive`, and
drive time only through the scheduler. Given identical inputs and timer firings,
the engine's outputs are then fully reproducible.

## 5. Timers

All periods are configurable (`TimerConfig` = policy); the mechanism is the
injected `Scheduler` port, with three interchangeable implementations:

- **`ManualScheduler`** — virtual time, advanced explicitly (`advance`,
  `run_until_idle`); protocol-logic tests run instantly.
- **`WallClockScheduler`** — real elapsed time; `run_for`/`run_until_idle`
  sleep to each deadline and fire callbacks on the caller's thread (no
  callback thread, atomic-entry contract preserved). `test_wallclock.py` runs
  every timer behavior this way with ms-scale periods.
- **`SimScheduler`** over an **`ExternalTimeService`** (`now_ns`/`wait_ns`) —
  the pyhdl-if seam. `SpoofClock` is the packaged Python stand-in for the
  SV side; swap in the real imp object and nothing else changes (section 8.6).

Under `ManualScheduler`/`SpoofClock`, wall-clock magnitudes are irrelevant —
time advances only when driven.

| Timer | Fires when | Action |
|---|---|---|
| Retransmission (RFC 6298) | RTO after oldest unacked segment | back off RTO ×2 (capped), invalidate RTT timing (Karn), retransmit head of queue; abort after `max_retransmits`; enforce `user_timeout` |
| Persist | send window is zero with data/FIN pending | 1-byte (or FIN) zero-window probe, exponential backoff between `persist_min`/`persist_max` |
| Keepalive (off by default) | connection idle `keepalive_idle` | probe with SEG.SEQ = SND.UNA−1, len 0 (unacceptable to the peer, elicits an ACK); after `keepalive_count` unanswered probes, abort |
| Delayed ACK (off by default) | data received, `delayed_ack` set | coalesce ACKs (every 2nd segment or on timeout) |
| TIME-WAIT | entering TIME_WAIT | close after 2·`msl`; restart on retransmitted FIN |

## 6. RFC 9293 compliance matrix

Status legend: **✔** implemented · **◐** implemented, configurable/partial ·
**✘** deliberate deviation (rationale given) · **n/a** outside the model's layer.

| RFC 9293 area | § | Status | Notes |
|---|---|---|---|
| Header format, flags, codec | 3.1 | ✔ | `TcpSegment.build/parse`; reserved bits zeroed; ECE/CWR carried but not acted on (no ECN) |
| Checksum | 3.1 | n/a | Needs the IP pseudo-header; owned by the net adapter by design |
| MSS option | 3.2/3.7.1 | ✔ | Sent on SYN, honored (min of local config and peer offer) |
| Window Scale / other options | 3.2 | ◐ | WScale parsed, **not applied** (no >64 KiB windows); unknown options preserved in `raw_options` |
| Sequence arithmetic | 3.4 | ✔ | Full mod-2³² (`seqnum.py`); wraparound covered end-to-end in tests |
| ISN selection | 3.4.1 | ✘ | Configurable/random, not clock-PRF: a verification model needs reproducible ISNs, not attack resistance |
| Quiet time | 3.4.3 | ✘ | Not modeled (no host reboot concept) |
| Connection establishment (incl. simultaneous open) | 3.5 | ✔ | Active, passive, simultaneous; LISTEN learns the remote socket |
| Reset generation/processing | 3.5.2–3 | ✔ | State-correct RST replies; exact-`RCV.NXT` reset; in-window RST/SYN → challenge ACK (RFC 5961); SYN-RCVD RST returns a passive open to LISTEN (re-wildcarded), refuses an active one |
| Closing (incl. simultaneous close, half-close) | 3.6 | ✔ | FIN_WAIT_1/2, CLOSING, LAST_ACK, TIME_WAIT (2·MSL, configurable); data still delivered in FIN_WAIT_* |
| Segmentation | 3.7 | ✔ | MSS-bounded; PSH on last segment of a pushed buffer |
| PMTUD / IPv6 jumbograms | 3.7.2/3.7.5 | n/a | No IP layer |
| Nagle | 3.7.4 | ◐ | Implemented; **default off** for stimulus determinism (RFC requires the off switch; we invert the default and document it) |
| Retransmission timeout | 3.8.1 / RFC 6298 | ✔ | SRTT/RTTVAR, K=4, clamped; exponential backoff; Karn's algorithm |
| Congestion control | 3.8.2 / RFC 5681 | ✘ | **Out of scope by team decision**: the model exercises a DUT, it is not an internet host; flow control (windowing) is fully modeled |
| Connection failures (R1/R2-style) | 3.8.3 | ✔ | `max_retransmits` give-up + enforced `user_timeout` abort |
| Keepalive | 3.8.4 | ✔ | Default off, fully configurable (idle/interval/count) per the RFC's MUSTs |
| Urgent data | 3.8.5 | ◐ | SND.UP/RCV.UP, URG + pointer on send, urgent indicator on RECEIVE; no urgent-mode byte-stream semantics beyond that |
| Zero-window probing | 3.8.6.1 | ✔ | Persist timer with backoff; window reopen cancels |
| Receiver SWS avoidance | 3.8.6.2.2 | ✔ | Right edge held until it can advance ≥ min(MSS, buf/2); configurable (`sws_avoidance`), default on |
| Sender SWS avoidance | 3.8.6.2.1 | ◐ | Via MSS-bounded sending + optional Nagle |
| Delayed ACK | 3.8.6.3 | ◐ | Implemented; default immediate for determinism (RFC caps delay at 0.5 s — configuration is the user's responsibility) |
| User/TCP interface | 3.9.1 | ✔ | OPEN/SEND/RECEIVE/CLOSE/ABORT/STATUS exactly, with per-state error semantics (3.10.1–3.10.5); SEND/OPEN-in-LISTEN converts passive→active |
| Event processing (SEGMENT ARRIVES) | 3.10 | ✔ | Acceptability test (3.10.7.4 four cases), ordered RST/SYN/ACK/data/FIN checks per state; data/FIN on SYNs delivered after establish; out-of-order FIN deferred until in sequence |
| TIME-WAIT reuse by new SYN | 3.10.7.2 / RFC 6191 | ✘ | MAY not taken: challenge ACK instead (single-TCB model; see section 9 row 22) |
| MD5/AO, ECN, timestamps, SACK | various | ✘ | Not modeled (out of scope for the stimulus use case; options pass through where applicable) |

## 7. Testing

```
python -m pytest tcp_model/tests/unit        # 207 tests, < 2 s
```

The suite layers:

- pure-function tests (`seqnum`, `segment`, `rtt`, scheduler);
- single-engine protocol tests driving hand-crafted peer segments through
  `CaptureNet` (handshake, data, retransmission, teardown, seeding, RFC user
  interface, RFC-completion features);
- **`test_efsm_paper.py`** — every transition of the EFSM paper, figure-cited
  (`[EFSM-n]`); where paper and RFC disagree the RFC behavior is asserted;
- **`test_fsm_matrix.py`** — valid TCP the paper doesn't model: RFC 5961
  challenges across all synchronized states, WL1/WL2 staleness, futuristic
  ACKs, data on SYNs, deferred out-of-order FIN, check-ordering corners;
- **`test_wallclock.py`** — the same timer behaviors under
  `WallClockScheduler` with ms-scale periods: RTO backoff and give-up,
  persist, 2·MSL, keepalive, delayed ACK, user timeout, and full two-engine
  lifecycles in genuinely elapsing time (whole module < 2 s);
- **`test_sim_clock_harness.py`** — the pyhdl-if dress rehearsal:
  `SimScheduler` + `SpoofClock`, asserting retransmission/2·MSL/teardown at
  exact simulated nanosecond timestamps, including loss recovery over a
  delayed `Wire` driven purely by virtual sim time;
- two-engine end-to-end tests over `Wire` (bulk, bidirectional, loss
  recovery, sequence wraparound, full close).

Planned but not in this package: the interop harness that runs the engine
against Linux kernel TCP over a TUN device (asyncio real-time scheduler + IP
adapter with checksum). See the implementation plan, stream M milestone M8.

---

# 8. API reference

Everything below is importable from `tcp_model` unless noted.

## 8.1 `TcpEngine`

```python
TcpEngine(*, local_port, remote_port=0, scheduler, tx,
          snd_mss=1460, rcv_wnd=65535, iss=None, timers=None,
          on_data=None, on_state_change=None, on_error=None, name="tcp")
```

| Parameter | Type | Meaning |
|---|---|---|
| `local_port` | int | Local TCP port (used in emitted segments and RX filtering) |
| `remote_port` | int | Peer port; 0 in LISTEN means "learn from first SYN" |
| `scheduler` | `Scheduler` | Time source: `now()`, `call_later()`, `cancel()` |
| `tx` | `Callable[[bytes], None]` | Called with each outgoing segment's wire bytes |
| `snd_mss` | int | MSS advertised/used (peer's SYN offer may lower the effective value) |
| `rcv_wnd` | int | Receive buffer size = maximum advertised window |
| `iss` | int \| None | Fixed initial send sequence; `None` → random |
| `timers` | `TimerConfig` \| None | All periods + behavior knobs (§8.4) |
| `on_data` | `Callable[[bytes], None]` \| None | Streaming delivery; `b""` = EOF (peer FIN). If unset, data buffers for `receive()` |
| `on_state_change` | `Callable[[ConnState, ConnState], None]` \| None | `(old, new)` on every transition |
| `on_error` | `Callable[[str], None]` \| None | Resets, refusals, timeouts ("connection reset", "user timeout", "keepalive timeout", …) |
| `name` | str | Debug label; part of the connection name |

### RFC 9293 §3.9.1 user calls

**`open(active=True, *, remote_port=None, local_port=None, timeout=None) -> str`**
OPEN. `active=True`: CLOSED/LISTEN → SYN_SENT (SYN emitted). `active=False`:
CLOSED → LISTEN. Optionally (re)binds the socket pair and sets the user
timeout. Returns the local connection name. Raises `RuntimeError` if a
connection already exists in a non-convertible state.

**`send(data: bytes, push=True, urgent=False, timeout=None) -> int`**
SEND. Queues bytes and transmits as the window allows. `push=True` sets PSH on
the segment carrying the last queued byte. `urgent=True` sets SND.UP past the
queued data; outgoing segments covering urgent data carry URG + urgent pointer.
In LISTEN with a known foreign socket, converts to active open (data flows
after ESTABLISHED); raises if the foreign socket is unspecified. Raises in
CLOSED and after `close()`.

**`receive(max_len=65535) -> ReceiveResult`**
RECEIVE. Returns `ReceiveResult(data, push, urgent)`: up to `max_len` in-order
bytes plus indicators — `push` if a PSH-marked segment contributed to delivered
data, `urgent` if urgent data was received. Draining the buffer clears the
indicators; draining may also release a window update (subject to SWS).

**`close() -> None`**
CLOSE. Graceful shutdown: queued data is sent first, then FIN
(ESTABLISHED→FIN_WAIT_1, CLOSE_WAIT→LAST_ACK). In CLOSED/LISTEN/SYN_SENT the
connection is simply dropped.

**`abort() -> None`**
ABORT. Sends RST (in synchronized states), discards all state, → CLOSED.

**`status() -> dict`**
STATUS. The RFC status data block: `local_connection_name`, `local_socket`,
`remote_socket`, `connection_state`, `send_window`, `receive_window`,
`buffers_awaiting_ack`, `bytes_awaiting_ack`, `buffers_pending_receipt`,
`urgent_state`, `transmission_timeout` (current RTO), plus `diffserv_field` /
`security_compartment` reported as `None` (IP-layer concerns).

### Lower-layer and model-specific entry points

**`on_segment(seg_bytes: bytes) -> None`** — the RFC "SEGMENT ARRIVES" event
(not a user call). Feed one received segment as raw wire bytes; runs the full
per-state processing synchronously (replies, state changes, delivery,
callbacks all happen inside this call).

**`seed(TcbSeed) -> None`** — model extension: drop into an arbitrary
state/sequence configuration (see §8.3). The migration path from manual
seq/ack tracking.

**Convenience wrappers:** `active_open()` / `passive_open()` (thin over
`open`), `recv(max_len) -> bytes` (thin over `receive`).

**Introspection:** `state -> ConnState` (property), `get_state() -> str`,
`get_tcb_snapshot() -> dict` (SND.UNA/NXT/WND, RCV.NXT/WND, ISS/IRS, MSS, RTO,
SRTT, `retx_depth`, `ooo_depth`, `recv_buffered`, `send_buffered`).

## 8.2 `ReceiveResult`

`NamedTuple(data: bytes, push: bool, urgent: bool)` — the RECEIVE return.

## 8.3 `ConnState`, `TCB`, `TcbSeed`, `seed_established`

- **`ConnState`** — enum of the 11 RFC states (`CLOSED`, `LISTEN`, `SYN_SENT`,
  `SYN_RECEIVED`, `ESTABLISHED`, `FIN_WAIT_1`, `FIN_WAIT_2`, `CLOSE_WAIT`,
  `CLOSING`, `LAST_ACK`, `TIME_WAIT`).
- **`TCB`** — the connection variables (`iss`, `snd_una`, `snd_nxt`, `snd_wnd`,
  `snd_wl1`, `snd_wl2`, `snd_up`, `irs`, `rcv_nxt`, `rcv_wnd`, `rcv_up`,
  `snd_mss`) + `snapshot()`.
- **`TcbSeed`** — dataclass naming any subset of state/sequence variables;
  unset fields get consistent derived defaults. Optional RTT seeds
  (`srtt`, `rttvar`, `rto`).
- **`seed_established(snd_nxt, rcv_nxt, snd_wnd=65535, rcv_wnd=65535,
  snd_mss=1460) -> TcbSeed`** — consistent mid-stream ESTABLISHED seed
  (SND.UNA = SND.NXT; ISS/IRS derived one behind).

## 8.4 `TimerConfig`

| Field | Default | Meaning |
|---|---|---|
| `rto_initial` | 1.0 | RTO before the first RTT sample (RFC 6298) |
| `rto_min` / `rto_max` | 1.0 / 60.0 | RTO clamp bounds |
| `rtt_g` | 0.001 | Clock granularity G in the RTO formula |
| `max_retransmits` | 12 | Consecutive RTOs before abort |
| `persist_min` / `persist_max` | 1.0 / 60.0 | Zero-window probe backoff bounds |
| `keepalive_idle` | `None` | Idle time before first probe; `None` disables keepalive |
| `keepalive_interval` | 75.0 | Gap between unanswered probes |
| `keepalive_count` | 9 | Unanswered probes before abort |
| `msl` | 1.0 | TIME-WAIT holds 2·msl |
| `delayed_ack` | `None` | `None` = ACK immediately; else coalescing delay |
| `user_timeout` | `None` | Abort if data stays unacknowledged this long |
| `nagle` | `False` | Sender SWS/Nagle; off by default for stimulus determinism |
| `sws_avoidance` | `True` | Receiver SWS avoidance (hold window-edge advances < min(MSS, buf/2)) |

## 8.5 `TcpSegment` and `Flags`

`TcpSegment` fields: `src_port`, `dst_port`, `seq`, `ack`, `flags`, `window`,
`checksum` (carried, never computed), `urgent_ptr`, `payload`, `mss`
(decoded/encoded MSS option), `wscale` (decoded/encoded window-scale option),
`raw_options` (undecoded options, preserved).

Methods and properties: `build() -> bytes` (wire format, options padded to
32-bit words, data offset computed), `TcpSegment.parse(bytes)` (validates
length and data offset; raises `ValueError`), `seg_len` (payload + SYN + FIN),
`syn`/`fin`/`rst`/`ack_flag` booleans, `flag_str()`.

`Flags`: `FIN SYN RST PSH ACK URG ECE CWR` bit constants.

## 8.6 Scheduler port

**`Scheduler`** (protocol): `now() -> float`,
`call_later(delay_s, cb) -> TimerHandle`, `cancel(handle)`.

**`ManualScheduler`** — virtual time for tests:
- `advance(dt) -> int` — move time forward, firing due callbacks in deadline
  order; callbacks scheduled during firing are honored inside the window.
  Returns the number fired.
- `run_until_idle(max_steps=100000) -> int` — fire everything pending in time
  order (raises on a timer storm).
- `pending -> int` — live (uncancelled) timers.

**`TimerHandle`** — `cancelled` flag; cancellation is lazy (fired-over).

**`WallClockScheduler`** — real time (`time.monotonic`):
- `run_for(dt) -> int` — run for `dt` real seconds, sleeping between deadlines
  and firing due callbacks **on the caller's thread**.
- `run_until_idle(timeout=10.0) -> int` — drain all timers or raise on the
  wall-clock guard.
- Single-threaded by construction, so the engine's atomic-entry contract holds
  without locks.

**`ports.sim_clock`** — the external-clock seam (also re-exported at top level):

- **`ExternalTimeService`** (protocol): `now_ns() -> int`,
  `async wait_ns(delay_ns)`. This is the shape a pyhdl-if time service
  presents (`now_ns` imp function + blocking `wait_ns` imp task).
- **`SimScheduler(svc, loop)`** — implements `Scheduler` over a service:
  `call_later` spawns a task awaiting `svc.wait_ns(...)` then firing the
  callback; `now()` = `svc.now_ns()/1e9`; `cancel` cancels the task. All
  callbacks land on the one asyncio loop at simulation timestamps.
- **`SpoofClock(settle_rounds=8)`** — a Python-generated fake simulator clock
  (discrete-event): `await run_for(sim_ns)` / `await run_until_idle()` let the
  loop quiesce, then jump virtual time to the earliest waiter and wake it, one
  per step in deadline order. `pending` counts live waiters. Replacing
  `SpoofClock` with the real pyhdl-if imp object is the entire integration
  step — engine and `SimScheduler` are untouched.

## 8.7 Net-port test helpers

**`CaptureNet`** — records every emitted segment: `sent` (parsed
`TcpSegment`s), `raw` (bytes), `last`, `pop_all()`, `clear()`; `bind(engine)` +
`inject(seg)` to feed hand-crafted segments in.

**`Wire`** — connects two engines through a scheduler-driven channel:
`Wire(scheduler, delay=0.0, a_to_b_filter=None, b_to_a_filter=None)`;
`connect(a, b)`; use `tx_from_a`/`tx_from_b` as the engines' `tx`. Filters are
`(TcpSegment, index) -> bool` (return `False` to drop — loss/reorder
modeling). Counters: `delivered_ab`, `delivered_ba`, `dropped`.

## 8.8 `core.seqnum` (advanced)

`MOD`, `MASK`, `s32(x)`, `add(a, n)`, `sub(a, b)`, `seq_lt/leq/gt/geq`,
`between(lo, x, hi)`, and `acceptable(seg_seq, seg_len, rcv_nxt, rcv_wnd)` —
the RFC 9293 §3.10.7.4 four-case acceptability test. Useful when building
custom harnesses or checkers on top of the model.

## 8.9 Exceptions and error reporting

Programming/state errors raise `RuntimeError` synchronously (`send` in CLOSED,
`open` on an existing connection, SEND-in-LISTEN without a foreign socket).
Protocol-driven failures are reported through `on_error` and drive the FSM
(`"connection refused"`, `"connection reset"`, `"retransmission timeout"`,
`"user timeout"`, `"keepalive timeout"`). Malformed wire input to
`TcpSegment.parse` raises `ValueError`.

User calls raise `RuntimeError` with RFC 9293 3.10.1–3.10.5 wording when made
in an invalid state: `"connection does not exist"` (any call but OPEN in
CLOSED), `"connection already exists (STATE)"` (OPEN elsewhere),
`"connection closing"` (SEND/CLOSE in the FIN states; RECEIVE once drained
in CLOSE-WAIT and always in CLOSING/LAST-ACK/TIME-WAIT), and
`"foreign socket unspecified"` (SEND in an unqualified LISTEN).

One deliberate wording deviation: a RST answering our SYN (SYN-SENT) is
reported as `"connection refused"` — the universal BSD/errno convention — where
the RFC's literal text says "connection reset". See section 9, row 23.

---

# 9. EFSM paper (TR2005-07-22) vs RFC 9293 — divergence catalog

The paper — Zaghal & Khan, *EFSM/SDL modeling of the original TCP standard
(RFC 793) and the Congestion Control Mechanism of TCP Reno*, Kent State
TR2005-07-22 — models **RFC 793** plus Jacobson's Reno additions, under the
simplifying assumptions of its §3.1/§3.2 (unlimited buffers, no STATUS, no
PSH/URG, every sent segment retransmittable). Engine comments cite its figures
as `[EFSM-n]`. This model targets **RFC 9293**, which rolls up RFC 793 with
1122, 5961 (blind-attack mitigations), 6298 (RTO), 6528 (ISN), and others —
so several disagreements below are "793 vs 9293" rather than errors in either
document. Where the two disagree, the engine follows RFC 9293; tests in
`test_efsm_paper.py` / `test_fsm_matrix.py` assert the RFC behavior and cite
the paper figure they diverge from.

| # | Topic | Paper (RFC 793-era) | RFC 9293 | This model |
|---|---|---|---|---|
| 1 | Baseline | RFC 793 + Reno CC | consolidates 793/1122/5961/6298/6528 | RFC 9293 |
| 2 | In-window RST, sync states | reset the connection [EFSM-13/19/23...] | reset only if `SEG.SEQ == RCV.NXT`; else challenge ACK (5961) | RFC |
| 3 | In-window SYN, sync states | send RST, tear down [EFSM-13] | challenge ACK, drop | RFC |
| 4 | SYN-RCVD, unacceptable ACK | RST `SEQ=SND.NXT`, delete TCB → CLOSED [EFSM-10] | RST `SEQ=SEG.ACK`, **remain** in SYN-RCVD | RFC |
| 5 | CLOSED reply, no-ACK segment | RST,ACK with `SEQ=SEG.SEQ+SEG.LEN` [EFSM-2] | `SEQ=0, ACK=SEG.SEQ+SEG.LEN` | RFC |
| 6 | SYN-SENT, unacceptable ACK | RST unconditionally [EFSM-7] | RST "unless the RST bit is set" (then drop) | RFC |
| 7 | ACK beyond `SND.NXT` | drop silently ("invalid ACK, drop!") [EFSM-14] | send an ACK, then drop | RFC |
| 8 | Window update | `SND.WND := min(CWND, SEG.WND)` on every valid ACK; no staleness guard [EFSM-14] | WL1/WL2 guard against stale segments; `SND.WND = SEG.WND` | RFC (no CWND) |
| 9 | FIN macro | `RCV.NXT := SEG.SEQ+1` [EFSM-38] — regresses `RCV.NXT` when the FIN carries data | data consumed first; FIN occupies the following sequence number | RFC |
| 10 | Retransmission bound | `ExpBoff` ×2 capped at 64; never gives up [EFSM-17] | 3.8.3 thresholds must exist; connection aborts | `max_retransmits` + `user_timeout` |
| 11 | What is retransmittable | assumption §3.1-3: *every* sent segment goes on the RexQ (pure ACKs included) | only sequence-consuming segments | RFC |
| 12 | Congestion control | Reno: dACK counting, fast retransmit/recovery, CWND/SSthresh [EFSM-14/16/17/39] | 3.8.2 → RFC 5681 | **excluded by team scope**; no dup-ACK counting or fast retransmit; window = peer's `SEG.WND` |
| 13 | Send batching | hold data until a full segment accumulates [EFSM-11] | Nagle (3.7.4), conditional and MUST be switchable | Nagle implemented, default **off** (stimulus determinism) |
| 14 | Zero window | no persist timer — sender would deadlock | probing SHOULD (3.8.6.1) | persist with exponential backoff |
| 15 | Delayed ACK | none: ACK per in-order segment [EFSM-15] | SHOULD, ≤ 0.5 s (3.8.6.3) | configurable; default immediate (matches the paper) |
| 16 | Keepalive | absent | MAY, default off (3.8.4) | implemented, default off |
| 17 | STATUS / PSH / URG | not modeled (§3.2) | specified | implemented (STATUS via `status()`; PSH/URG on SEND/RECEIVE) |
| 18 | Security/compartment, precedence | not modeled (§3.2) | precedence removed (RFC 2873); security n/a here | n/a (matches both) |
| 19 | User timeout | standalone USERTIME timer, every state [EFSM-36] | 3.8.3 user timeout | deadline checked at RTO firings — equivalent whenever data is outstanding (the only time it can trip); detection granularity = current RTO |
| 20 | Out-of-order reassembly | single-gap merge via "HSEG" contiguity assumption [EFSM-15] | queue and reassemble | full multi-hole reassembly; FIN ahead of a gap is deferred and applied when the gap fills |
| 21 | RTO calculation | 793-style `CalcRTO` (SRTT smoothing only) | RFC 6298 | 6298: SRTT/RTTVAR, K=4, granularity G, Karn, backoff |
| 22 | TIME-WAIT: fresh SYN | not modeled | MAY accept a new SYN with a higher ISN (3.10.7.2 note / RFC 6191) | not implemented — challenge ACK instead (matrix row) |
| 23 | SYN-SENT RST wording | "Connection reset" [EFSM-7] | "connection reset" | reported as `"connection refused"` (BSD convention; deliberate) |
| 24 | LISTEN foreign socket | always filled from the incoming SYN [EFSM-5] | a fully specified passive OPEN matches only its peer | pinned `remote_port` honored; wildcard otherwise; re-wildcarded when SYN-RCVD returns to LISTEN |

Points 2–9 are outright behavioral disagreements (793 vs 9293) — the tests
assert the RFC side. Points 10–16 are paper simplifications with RFC-mandated
mechanisms restored here. Points 17–24 are scope/convention differences.
Where the paper and RFC agree — the state diagram [EFSM-0], the acceptability
macro [EFSM-37], SYN-RCVD's passive-open return to LISTEN [EFSM-9], TIME-WAIT
FIN re-ACK + 2MSL restart [EFSM-29], CLOSE-after-SEND FIN ordering
[EFSM-8/12/31] — the engine matches both, and `test_efsm_paper.py` walks every
such transition figure by figure.
