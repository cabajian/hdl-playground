# tcp_model — Architecture Diagrams

Companion to `DOCUMENTATION.md` (sections cited as *doc §n*). Diagrams are
Mermaid; they render on GitHub/GitLab/VS Code. References are to `core/` and
`ports/` at v0.3.0.

---

## 1. Ports-and-adapters view

The engine is synchronous and dependency-free; the world exists only through
three injected ports (*doc §1–2*).

```mermaid
flowchart LR
    APP["App port — 3.9.1 user calls<br/>open · send · receive · close · abort · status"]
    subgraph CORE["core/ — no simulator, no asyncio, no pyhdl-if"]
        direction TB
        ENG["<b>TcpEngine</b> (engine.py)<br/>FSM · SEGMENT ARRIVES · send/recv · timers"]
        HLP["TCB · TcpSegment codec · seqnum ·<br/>RttEstimator (6298) · TimerConfig"]
        ENG -.- HLP
    end
    SCH["Scheduler port<br/>now · call_later · cancel"]
    NET["Net port<br/>tx(bytes) ↔ on_segment(bytes)"]
    APP --> ENG
    ENG <--> SCH
    ENG <--> NET
```

## 2. One engine, four bindings

Same `TcpEngine`, different port bindings per environment (*doc §5, §7*).
The rightmost column is Boundary 2 of the implementation plan; swapping
`SpoofClock` → the pyhdl-if imp object is the only change between the last
two columns.

```mermaid
flowchart TB
    ENG["<b>TcpEngine</b> — one implementation, unchanged"]
    ENG --> MS & WS & SC1 & SC2

    subgraph B1["unit tests"]
        MS["ManualScheduler<br/>(virtual time)"] --- CN["CaptureNet / Wire"]
    end
    subgraph B2["wall-clock tests"]
        WS["WallClockScheduler<br/>(time.monotonic)"] --- W2["Wire<br/>(real delays)"]
    end
    subgraph B3["sim harness — today"]
        SC1["SimScheduler"] --- SP["SpoofClock<br/>(Python fake SV clock)"]
    end
    subgraph B4["simulator — deploy"]
        SC2["SimScheduler<br/>(same class)"] --- HIF["pyhdl-if imp object<br/>now_ns / wait_ns"]
    end
```

## 3. Connection FSM

The canonical diagram ([EFSM-0], RFC 9293 3.10) as implemented, plus the one
9293 arc worth drawing: SYN-RECEIVED returning to LISTEN on a RST when the
open was passive. Every arc is a cited test in `test_efsm_paper.py`.

```mermaid
stateDiagram-v2
    [*] --> CLOSED
    CLOSED --> SYN_SENT: active OPEN / snd SYN
    CLOSED --> LISTEN: passive OPEN
    LISTEN --> SYN_SENT: OPEN(active) or SEND / snd SYN
    LISTEN --> SYN_RECEIVED: rcv SYN / snd SYN-ACK
    SYN_SENT --> SYN_RECEIVED: rcv SYN / snd SYN-ACK
    SYN_SENT --> ESTABLISHED: rcv SYN-ACK / snd ACK
    SYN_RECEIVED --> LISTEN: rcv RST (passive open)
    SYN_RECEIVED --> ESTABLISHED: rcv ACK of SYN
    SYN_RECEIVED --> FIN_WAIT_1: CLOSE / snd FIN
    ESTABLISHED --> FIN_WAIT_1: CLOSE / snd FIN
    ESTABLISHED --> CLOSE_WAIT: rcv FIN / snd ACK
    FIN_WAIT_1 --> FIN_WAIT_2: rcv ACK of FIN
    FIN_WAIT_1 --> CLOSING: rcv FIN / snd ACK
    FIN_WAIT_1 --> TIME_WAIT: rcv FIN+ACK-of-FIN / snd ACK
    FIN_WAIT_2 --> TIME_WAIT: rcv FIN / snd ACK
    CLOSING --> TIME_WAIT: rcv ACK of FIN
    CLOSE_WAIT --> LAST_ACK: CLOSE / snd FIN
    LAST_ACK --> CLOSED: rcv ACK of FIN
    TIME_WAIT --> TIME_WAIT: rcv FIN / re-ACK + restart 2MSL
    TIME_WAIT --> CLOSED: 2·MSL expiry
```

Not drawn (they cross everything, and the RFC's own figure omits them too):
CLOSE/ABORT from LISTEN or SYN-SENT → CLOSED; ABORT with RST from any
synchronized state → CLOSED; exact-`RCV.NXT` RST → CLOSED (SYN-RECEIVED with
an active open reports "connection refused"); in-window RST/SYN → challenge
ACK with **no** transition (RFC 5961); retransmission give-up and user
timeout → CLOSED via `_abort`.

## 4a. SEGMENT ARRIVES — per-state dispatch

`on_segment` fans out by state; the synchronized states continue into the
check chain of §4b.

```mermaid
flowchart LR
    RX["on_segment(bytes)"] --> P["parse ·<br/>port demux"]
    P --> C["CLOSED — RST reply rules<br/>(3.10.7.1)"]
    P --> L["LISTEN — RST ignored · ACK→RST ·<br/>SYN→SYN-ACK, SYN_RECEIVED (3.10.7.2)"]
    P --> SS["SYN_SENT — handshake ·<br/>simultaneous open (3.10.7.3)"]
    P --> TW["TIME_WAIT — exact-seq RST closes ·<br/>ACK everything · FIN restarts 2MSL"]
    P --> SYNC["synchronized states<br/>→ ordered checks (4b)"]
```

## 4b. SEGMENT ARRIVES — synchronized-state checks

The RFC's ordered checks as a single spine; every rejection is its own
terminal (3.10.7.4). The handler tail always runs `_check_pending_fin`, which
fires a deferred FIN the moment reassembly reaches it.

```mermaid
flowchart TB
    A["1 · acceptability — four-case test [EFSM-37]"]
    A -->|unacceptable| X1(["dup ACK — unless RST"])
    A -->|acceptable| R["2 · RST"]
    R -->|"SEQ == RCV.NXT"| X2(["_do_reset: passive SYN_RCVD → LISTEN ·<br/>active → refused · sync → connection reset"])
    R -->|in-window| X3(["challenge ACK"])
    R -->|no RST| SY["4 · SYN in window"]
    SY -->|yes| X4(["challenge ACK — no reset (5961)"])
    SY -->|no| AK["5 · ACK bit"]
    AK -->|off| X5(["drop"])
    AK -->|on| PRC["process ACK<br/>SYN_RCVD: valid → ESTABLISHED · invalid → RST(SEQ=SEG.ACK), stay<br/>ACK > SND.NXT → ACK + drop · WL1/WL2 window update<br/>FIN acked: FW1→FW2 · CLOSING→TIME_WAIT · LAST_ACK→CLOSED"]
    PRC --> TXT["7 · text — EST / FW1 / FW2 only<br/>(deliver, reassemble, ACK policy)"]
    TXT --> F["8 · FIN"]
    F -->|"in sequence"| PF(["_process_fin: EST→CLOSE_WAIT ·<br/>FW1→CLOSING · FW2→TIME_WAIT"])
    F -->|"ahead of a gap"| DF(["defer — _pending_fin"])
```

## 5. Send path

`send()` through segmentation to the retransmission queue. The segmentation
loop is one node — it repeats while window and data allow.

```mermaid
flowchart TB
    S["send(data, push, urgent, timeout)"]
    S -->|"CLOSED / closing states"| E1(["RuntimeError (3.10.2)"])
    S -->|LISTEN| CVT["convert to active open —<br/>SYN out, data queued (3.10.2)"]
    S -->|ok| BUF["app_buf += data<br/>push → PSH pending · urgent → SND.UP"]
    CVT --> BUF
    BUF --> GATE["_send_pending — state gate:<br/>ESTABLISHED / CLOSE_WAIT (SYN_RCVD: FIN only)"]
    GATE -->|"SND.WND == 0"| PERS(["persist: 1-byte probe,<br/>exponential backoff (3.8.6.1)"])
    GATE -->|window open| SEG["segmentation loop — chunk ≤ MSS ·<br/>Nagle holds sub-MSS if data outstanding ·<br/>PSH on last · URG pointer ·<br/>repeat while usable window and data"]
    SEG --> QS["_queue_and_send — retx.append ·<br/>SND.NXT += len · RTT stamp (Karn) · arm RTO"]
    QS --> TX(["_make_segment: advertise RCV.WND<br/>with SWS avoidance → tx()"])
    SEG -->|"buffer drained ∧ fin_pending"| FIN(["FIN out: EST/SYN_RCVD → FIN_WAIT_1 ·<br/>CLOSE_WAIT → LAST_ACK"])
```

## 6. Receive path

`_recv_data` + multi-hole reassembly + ACK policy, as a straight spine.

```mermaid
flowchart TB
    IN["_recv_data(seg)"]
    IN -->|"entirely below RCV.NXT"| D1(["duplicate — force dup ACK"])
    IN -->|overlap| TR["trim head to RCV.NXT"]
    IN -->|"at / above RCV.NXT"| CL
    TR --> CL["clamp to RCV.WND"]
    CL -->|"no room"| D2(["window full — force dup ACK"])
    CL -->|fits| ORD["in order?  SEQ == RCV.NXT"]
    ORD -->|yes| DLV["deliver (on_data / recv_buf)<br/>RCV.NXT += len · PSH / URG flags"]
    DLV --> MRG["_merge_reassembly —<br/>drain ooo, multi-hole, until no progress"]
    MRG --> ACK(["_schedule_ack — immediate, or coalesce<br/>(every 2nd segment / delayed_ack timer)"])
    ORD -->|no| OOO["_store_ooo(seq, data, flags)"]
    OOO --> D3(["force dup ACK"])
```

## 7. Timer and clock stack

Policy → users → protocol → implementations → external service. Per-timer
behavior is the table in *doc §5*; the user timeout is a deadline check
inside `_on_rto`, not a scheduled timer.

```mermaid
flowchart TB
    POL["TimerConfig — policy<br/>rto · persist · msl · keepalive · delayed_ack · user_timeout"]
    USE["engine timer users<br/>_on_rto (6298 + Karn + give-up + user-timeout) ·<br/>_on_persist · _on_delack · _on_time_wait · _on_keepalive"]
    PROTO["Scheduler protocol — now · call_later · cancel"]
    POL --> USE --> PROTO
    PROTO --> M["ManualScheduler<br/>virtual"] & W["WallClockScheduler<br/>monotonic, caller thread"] & SIM["SimScheduler<br/>asyncio task per timer"]
    SIM --> SVC["ExternalTimeService<br/>now_ns · wait_ns"]
    SVC --> SP["SpoofClock<br/>today"] & HIF["pyhdl-if imp<br/>deploy"]
```

## 8. External-clock rendezvous (the pyhdl-if seam)

One RTO under `SimScheduler`. Today the service is `SpoofClock` and
`run_for` drives; under the simulator it is the pyhdl-if imp object and the
SV scheduler drives — the engine and `SimScheduler` cannot tell the
difference. Everything executes on the one asyncio loop, so engine entries
stay atomic (*doc §3*).

```mermaid
sequenceDiagram
    participant E as TcpEngine (sync)
    participant S as SimScheduler
    participant C as ExternalTimeService<br/>(SpoofClock | pyhdl-if imp)
    participant D as clock driver<br/>(run_for | SV scheduler)

    E->>S: call_later(RTO, _on_rto)
    S-)C: task: await wait_ns(rto_ns)
    Note over C: waiter registered<br/>(deadline-ordered heap)
    D->>D: let loop quiesce
    D->>C: advance to earliest deadline
    C--)S: wait_ns returns — task resumes
    S->>E: _on_rto() — same loop, atomic
    E->>E: backoff · retransmit head of retx
    E->>S: call_later(RTO', _on_rto)
    Note over E,D: ACK arrives first → sched.cancel(h) → task.cancel()<br/>→ waiter future cancelled → skipped by driver
```
