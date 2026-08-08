"""The synchronous TCP engine.

Implements the RFC 9293 connection FSM: user calls (3.10.1-3.10.6), SEGMENT
ARRIVES processing (3.10.7), timeouts (3.10.8), RFC 6298 retransmission with
Karn's algorithm, flow control, persist, keepalive, and teardown.

References cited in comments:
- ``3.x[.y]``   RFC 9293 section numbers.
- ``[EFSM-n]``  figure *n* of Zaghal & Khan, "EFSM/SDL modeling of the original
  TCP standard (RFC 793) ...", Kent State TR2005-07-22. Cited where a transition
  maps onto the paper's model; divergences are catalogued in
  docs/DOCUMENTATION.md section 9.

The engine is synchronous: every entry point runs to completion, no awaiting.
Segments go out via the injected ``tx``; time exists only via the injected
``scheduler``. SEGMENT-ARRIVES logic lives here rather than a separate fsm.py
because it operates directly on engine/TCB state.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, replace
from typing import Callable, NamedTuple, Optional

from .rtt import RttEstimator
from .segment import Flags, TcpSegment
from .seqnum import (
    MASK,
    acceptable,
    add,
    between,
    s32,
    seq_geq,
    seq_gt,
    seq_leq,
    seq_lt,
    sub,
)
from .tcb import TCB, ConnState, TcbSeed
from .timers import TimerConfig

S = ConnState


class ReceiveResult(NamedTuple):
    """Result of a RECEIVE call (RFC 9293 3.9.1): data plus PUSH and URGENT flags."""

    data: bytes
    push: bool
    urgent: bool


@dataclass
class _Outgoing:
    """A sent-but-unacknowledged segment held on the retransmission queue."""

    seq: int
    flags: int
    payload: bytes
    sent_time: float
    retransmitted: bool = False

    @property
    def seg_len(self) -> int:
        return (
            len(self.payload)
            + (1 if self.flags & Flags.SYN else 0)
            + (1 if self.flags & Flags.FIN else 0)
        )

    @property
    def end(self) -> int:
        return add(self.seq, self.seg_len)


class TcpEngine:
    def __init__(
        self,
        *,
        local_port: int,
        remote_port: int = 0,
        scheduler,
        tx: Callable[[bytes], None],
        snd_mss: int = 1460,
        rcv_wnd: int = 65535,
        iss: Optional[int] = None,
        timers: Optional[TimerConfig] = None,
        on_data: Optional[Callable[[bytes], None]] = None,
        on_state_change: Optional[Callable[[ConnState, ConnState], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        name: str = "tcp",
    ):
        self.local_port = local_port
        self.remote_port = remote_port
        self._cfg_remote_port = remote_port   # restored on SYN-RCVD -> LISTEN return
        self.sched = scheduler
        self._tx = tx
        self.cfg = timers or TimerConfig()
        self.rtt = RttEstimator(self.cfg)
        self.name = name

        self._cfg_mss = snd_mss
        self.rcv_wnd_max = rcv_wnd
        self._iss_config = iss

        self.tcb = TCB(snd_mss=snd_mss)
        self.tcb.rcv_wnd = rcv_wnd

        # buffers / queues
        self.app_buf = bytearray()       # app bytes not yet segmented
        self.recv_buf = bytearray()       # in-order received bytes, undelivered
        self.retx: list[_Outgoing] = []    # sent, unacked (seq order)
        self.ooo: list[list] = []          # out-of-order: [seq, bytes]

        # close / fin state
        self.passive = False
        self.fin_pending = False
        self.fin_sent = False
        self.fin_acked = False
        self.fin_seq = 0
        self.peer_fin = False
        self._pending_fin = None   # seq of a FIN received ahead of missing data

        # PUSH / URGENT (RFC 9293 SEND/RECEIVE flags)
        self._push = False           # PSH requested for currently buffered data
        self._push_seen = False      # a PSH-bearing segment delivered data to us
        self._urgent_seen = False    # urgent data was received
        self.connection_name = f"{name}:{local_port}->{remote_port}"

        # timers
        self._rt_timer = None
        self._persist_timer = None
        self._persist_backoff = self.cfg.persist_min
        self._tw_timer = None
        self._ack_timer = None
        self._ack_count = 0
        self._rto_count = 0

        # RTT timing (Karn): (seq_end_being_timed, time_sent) or None
        self._rtt_pending = None
        self._zero_win_advertised = False

        # RFC-completeness state
        self._una_since = None       # when the oldest unacked data was queued (user timeout)
        self._ka_timer = None        # keepalive probe timer
        self._ka_probes = 0          # unanswered keepalive probes
        self._adv_right = None       # highest advertised RCV window right edge (receiver SWS)

        # callbacks
        self.on_data = on_data
        self.on_state_change = on_state_change
        self.on_error = on_error

    # ------------------------------------------------------------------ #
    # introspection
    # ------------------------------------------------------------------ #
    @property
    def state(self) -> ConnState:
        return self.tcb.state

    def get_state(self) -> str:
        return self.tcb.state.value

    def get_tcb_snapshot(self) -> dict:
        snap = self.tcb.snapshot()
        snap.update(
            rto=round(self.rtt.rto, 4),
            srtt=None if self.rtt.srtt is None else round(self.rtt.srtt, 4),
            retx_depth=len(self.retx),
            ooo_depth=len(self.ooo),
            recv_buffered=len(self.recv_buf),
            send_buffered=len(self.app_buf),
        )
        return snap

    def status(self) -> dict:
        """RFC 9293 STATUS: return the connection's status data block.

        Fields mirror the RFC's enumerated status data. Diffserv and
        security/compartment are IP-layer concerns the engine does not own and
        are reported as None.
        """
        return {
            "local_connection_name": self.connection_name,
            "local_socket": ("", self.local_port),
            "remote_socket": ("", self.remote_port),
            "connection_state": self.tcb.state.value,
            "send_window": self.tcb.snd_wnd,
            "receive_window": self._rcv_wnd(),
            "buffers_awaiting_ack": len(self.retx),
            "bytes_awaiting_ack": sub(self.tcb.snd_nxt, self.tcb.snd_una),
            "buffers_pending_receipt": len(self.recv_buf) + len(self.ooo),
            "urgent_state": self._urgent_seen or seq_lt(self.tcb.rcv_nxt, self.tcb.rcv_up),
            "diffserv_field": None,        # IP-layer concern, not modeled
            "security_compartment": None,  # not modeled
            "transmission_timeout": round(self.rtt.rto, 4),
        }

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TcpEngine {self.name} {self.tcb.state.value}>"

    # ------------------------------------------------------------------ #
    # user (App port) API
    # ------------------------------------------------------------------ #
    def seed(self, s: TcbSeed) -> None:
        """Drop the model into an arbitrary starting state (see TcbSeed)."""
        t = self.tcb
        t.state = s.state
        t.iss = s.iss if s.iss is not None else 0
        t.snd_una = s.snd_una if s.snd_una is not None else (
            s.snd_nxt if s.snd_nxt is not None else t.iss
        )
        t.snd_nxt = s.snd_nxt if s.snd_nxt is not None else t.snd_una
        t.snd_wnd = s.snd_wnd
        t.irs = s.irs if s.irs is not None else 0
        t.rcv_nxt = s.rcv_nxt if s.rcv_nxt is not None else (
            add(s.irs, 1) if s.irs is not None else 0
        )
        t.rcv_wnd = s.rcv_wnd
        self.rcv_wnd_max = s.rcv_wnd
        t.snd_mss = s.snd_mss
        self._cfg_mss = s.snd_mss
        t.snd_wl1 = t.rcv_nxt
        t.snd_wl2 = t.snd_una
        if s.srtt is not None:
            self.rtt.srtt = s.srtt
        if s.rttvar is not None:
            self.rtt.rttvar = s.rttvar
        if s.rto is not None:
            self.rtt.rto = s.rto
        if seq_gt(t.snd_una, t.snd_nxt):
            self._warn("seed: SND.UNA > SND.NXT")
        self._adv_right = None
        if t.state == S.ESTABLISHED:
            self._restart_keepalive(reset_probes=True)

    def open(
        self,
        active: bool = True,
        *,
        remote_port: Optional[int] = None,
        local_port: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> str:
        """OPEN (3.10.1). ``active=True`` -> SYN-SENT; ``active=False`` -> LISTEN.
        Returns the local connection name.

        ``timeout`` sets the user timeout (3.8.3). Diffserv and
        security/compartment are IP-layer parameters the engine does not own.
        """
        if remote_port is not None:
            self.remote_port = remote_port
        if local_port is not None:
            self.local_port = local_port
        self.connection_name = f"{self.name}:{self.local_port}->{self.remote_port}"
        if timeout is not None:
            self.cfg.user_timeout = timeout
        if active:
            self.active_open()
        else:
            self.passive_open()
        return self.connection_name

    def active_open(self) -> None:
        # LISTEN permitted: 3.10.1 OPEN in LISTEN converts passive -> active [EFSM-3].
        if self.tcb.state not in (S.CLOSED, S.LISTEN):
            raise RuntimeError(f"connection already exists ({self.tcb.state})")  # 3.10.1
        self.passive = False
        self.tcb.iss = self._new_iss()
        self.tcb.snd_una = self.tcb.iss
        self.tcb.snd_nxt = self.tcb.iss
        self.tcb.snd_wnd = 0
        self._set_state(S.SYN_SENT)
        self._queue_and_send(Flags.SYN, b"", with_mss=True)

    def passive_open(self) -> None:
        if self.tcb.state != S.CLOSED:
            raise RuntimeError(f"connection already exists ({self.tcb.state})")  # 3.10.1
        self.passive = True
        self._set_state(S.LISTEN)

    def send(self, data: bytes, push: bool = True, urgent: bool = False,
             timeout: Optional[float] = None) -> int:
        """SEND (3.10.2). ``push`` sets PSH on the segment carrying the last
        buffered byte; ``urgent`` sets URG + urgent pointer (3.8.5); ``timeout``
        sets the user timeout (3.8.3).
        """
        st = self.tcb.state
        if st == S.CLOSED:
            raise RuntimeError("connection does not exist")            # 3.10.2 [EFSM-2]
        if st in (S.FIN_WAIT_1, S.FIN_WAIT_2, S.CLOSING, S.LAST_ACK, S.TIME_WAIT) or (
            self.fin_pending
        ):
            raise RuntimeError("connection closing")                   # 3.10.2 [EFSM-18]
        if timeout is not None:
            self.cfg.user_timeout = timeout
        if push:
            self._push = True
        self.app_buf += data
        if st == S.LISTEN:
            # 3.10.2 SEND in LISTEN: convert passive -> active; data queued
            # until ESTABLISHED [EFSM-3].
            if not self.remote_port:
                raise RuntimeError("foreign socket unspecified")       # 3.10.2
            self.active_open()
        if urgent and data:
            # SND.UP points just past the urgent data (end of everything buffered)
            self.tcb.snd_up = add(self.tcb.snd_nxt, len(self.app_buf))
        if self.tcb.state in (S.ESTABLISHED, S.CLOSE_WAIT):
            self._send_pending()
        return len(data)

    def receive(self, max_len: int = 65535) -> ReceiveResult:
        """RFC 9293 RECEIVE. Returns (data, push, urgent)."""
        st = self.tcb.state
        if st == S.CLOSED:
            raise RuntimeError("connection does not exist")            # 3.10.3 [EFSM-2]
        if st in (S.CLOSING, S.LAST_ACK, S.TIME_WAIT):
            raise RuntimeError("connection closing")                   # 3.10.3 [EFSM-26]
        if st == S.CLOSE_WAIT and not self.recv_buf:
            # 3.10.3 CLOSE-WAIT: return remaining data, else "closing" [EFSM-31]
            raise RuntimeError("connection closing")
        n = min(max_len, len(self.recv_buf))
        data = bytes(self.recv_buf[:n])
        del self.recv_buf[:n]
        if data and self._zero_win_advertised and self._rcv_wnd() > 0:
            self._zero_win_advertised = False
            self._send_ack()  # window update
        push = self._push_seen
        urgent = self._urgent_seen
        if not self.recv_buf:  # all buffered data drained -> clear the indicators
            self._push_seen = False
            self._urgent_seen = False
        return ReceiveResult(data=data, push=push, urgent=urgent)

    def recv(self, max_len: int = 65535) -> bytes:
        """Convenience wrapper around RECEIVE returning only the bytes."""
        return self.receive(max_len).data

    def close(self) -> None:
        st = self.tcb.state
        if st == S.CLOSED:
            raise RuntimeError("connection does not exist")            # 3.10.4 [EFSM-2]
        if st in (S.FIN_WAIT_1, S.FIN_WAIT_2, S.CLOSING, S.LAST_ACK, S.TIME_WAIT):
            raise RuntimeError("connection closing")                   # 3.10.4 [EFSM-26]
        if st in (S.LISTEN, S.SYN_SENT):
            self._enter_closed()                                       # [EFSM-4/6]
        else:  # SYN-RCVD / ESTABLISHED / CLOSE-WAIT: FIN after queued data
            self.fin_pending = True                                    # [EFSM-8/12/31]
            self._send_pending()

    def abort(self) -> None:
        st = self.tcb.state
        if st == S.CLOSED:
            raise RuntimeError("connection does not exist")            # 3.10.5 [EFSM-2]
        # 3.10.5: RST only from synchronized-with-peer states [EFSM-8/12]
        if st in (S.SYN_RECEIVED, S.ESTABLISHED, S.FIN_WAIT_1, S.FIN_WAIT_2, S.CLOSE_WAIT):
            self._transmit(self._make_segment(self.tcb.snd_nxt, Flags.RST, b""))
        self._enter_closed()

    # ------------------------------------------------------------------ #
    # Net port: receive
    # ------------------------------------------------------------------ #
    def on_segment(self, data: bytes) -> None:
        seg = TcpSegment.parse(data)
        if self.local_port and seg.dst_port and seg.dst_port != self.local_port:
            return  # not for us
        if self.tcb.state == S.ESTABLISHED:
            self._restart_keepalive(reset_probes=True)   # peer activity observed
        st = self.tcb.state
        if st == S.CLOSED:
            self._handle_closed(seg)
        elif st == S.LISTEN:
            self._handle_listen(seg)
        elif st == S.SYN_SENT:
            self._handle_syn_sent(seg)
        else:
            self._handle_synchronized(seg)

    # ---- CLOSED (3.10.7.1 / [EFSM-2]) ----
    def _handle_closed(self, seg: TcpSegment) -> None:
        if seg.rst:
            return
        if seg.ack_flag:
            self._emit_rst_reply(seg, seq=seg.ack)
        else:
            # <SEQ=0><ACK=SEG.SEQ+SEG.LEN><CTL=RST,ACK> ([EFSM-2] differs: docs sec. 9)
            self._emit_rst_reply(seg, seq=0, ack=add(seg.seq, seg.seg_len), ack_flag=True)

    # ---- LISTEN (3.10.7.2 / [EFSM-5]) ----
    def _handle_listen(self, seg: TcpSegment) -> None:
        if seg.rst:
            return                                   # first: ignore RST
        if seg.ack_flag:
            self._emit_rst_reply(seg, seq=seg.ack)   # second: RST <SEQ=SEG.ACK>
            return
        if not seg.syn:
            return                                   # fourth: drop non-SYN
        if self.remote_port and seg.src_port and seg.src_port != self.remote_port:
            return   # fully-specified passive OPEN: foreign socket must match
        if not self.remote_port:
            self.remote_port = seg.src_port
        self.tcb.irs = seg.seq
        self.tcb.rcv_nxt = add(seg.seq, 1)
        self.tcb.iss = self._new_iss()
        self.tcb.snd_una = self.tcb.iss
        self.tcb.snd_nxt = self.tcb.iss
        self.tcb.snd_wnd = seg.window
        self.tcb.snd_wl1 = seg.seq
        self.tcb.snd_wl2 = self.tcb.iss
        if seg.mss:
            self.tcb.snd_mss = min(self._cfg_mss, seg.mss)
        self._set_state(S.SYN_RECEIVED)
        self._queue_and_send(Flags.SYN | Flags.ACK, b"", with_mss=True)

    # ---- SYN-SENT ----
    def _handle_syn_sent(self, seg: TcpSegment) -> None:
        ack_ok = False
        if seg.ack_flag:
            if seq_leq(seg.ack, self.tcb.iss) or seq_gt(seg.ack, self.tcb.snd_nxt):
                if not seg.rst:
                    self._emit_rst_reply(seg, seq=seg.ack)
                return
            if between(self.tcb.snd_una, seg.ack, self.tcb.snd_nxt):
                ack_ok = True
        if seg.rst:
            if ack_ok:
                self._signal_error("connection refused")
                self._enter_closed()
            return
        if not seg.syn:
            return
        self.tcb.irs = seg.seq
        self.tcb.rcv_nxt = add(seg.seq, 1)
        if seg.mss:
            self.tcb.snd_mss = min(self._cfg_mss, seg.mss)
        if seg.ack_flag:
            self._process_ack(seg.ack)
        self.tcb.snd_wnd = seg.window
        self.tcb.snd_wl1 = seg.seq
        self.tcb.snd_wl2 = seg.ack if seg.ack_flag else self.tcb.snd_una
        if seq_gt(self.tcb.snd_una, self.tcb.iss):
            # our SYN acked -> ESTABLISHED [EFSM-7]
            self._set_state(S.ESTABLISHED)
            self._send_ack()
            self._send_pending()
            if seg.payload or seg.fin:
                # 3.10.7.3: data/controls on the SYN are processed in
                # ESTABLISHED. Re-dispatch with SYN consumed (payload begins
                # at SEG.SEQ+1).
                cont = replace(seg, flags=seg.flags & ~Flags.SYN, seq=add(seg.seq, 1))
                self._handle_synchronized(cont)
        else:
            # simultaneous open [EFSM-7]: our outstanding SYN becomes SYN-ACK
            self._set_state(S.SYN_RECEIVED)
            if self.retx and (self.retx[0].flags & Flags.SYN):
                self.retx[0].flags |= Flags.ACK
            if seg.payload:
                # 3.10.7.3: queue SYN data until ESTABLISHED
                self._store_ooo(add(seg.seq, 1), seg.payload, seg.flags & ~Flags.FIN)
            if seg.fin:
                self._pending_fin = add(add(seg.seq, 1), len(seg.payload))
            self._transmit(
                self._make_segment(self.tcb.iss, Flags.SYN | Flags.ACK, b"", with_mss=True)
            )

    # ---- synchronized states (SYN-RECEIVED .. TIME-WAIT) ----
    def _handle_synchronized(self, seg: TcpSegment) -> None:
        st = self.tcb.state

        if st == S.TIME_WAIT:
            if seg.rst:
                # exact-seq RST closes; else challenge ACK (3.10.7.4 / RFC 5961)
                if seg.seq == self.tcb.rcv_nxt:
                    self._enter_closed()
                else:
                    self._send_ack()
                return
            self._send_ack()
            if seg.fin:
                self._restart_time_wait()   # retransmitted FIN [EFSM-29]
            return

        # 1. acceptability (3.10.7.4 four-case test / [EFSM-37])
        if not acceptable(seg.seq, seg.seg_len, self.tcb.rcv_nxt, self._rcv_wnd()):
            if not seg.rst:
                self._send_ack()   # dup ACK <SEQ=SND.NXT><ACK=RCV.NXT>
            return

        # 2. RST: exact-seq resets, in-window gets a challenge ACK
        # (3.10.7.4 / RFC 5961; [EFSM-13] resets on any in-window RST: docs sec. 9)
        if seg.rst:
            if seg.seq == self.tcb.rcv_nxt:
                self._do_reset()
            else:
                self._send_ack()
            return

        # 4. SYN in window: challenge ACK, not reset
        # (3.10.7.4 / RFC 5961; [EFSM-13] sends RST and closes: docs sec. 9)
        if seg.syn:
            self._send_ack()
            return

        # 5. ACK: if the ACK bit is off, drop (3.10.7.4 fifth check)
        if not seg.ack_flag:
            return
        ack = seg.ack

        if st == S.SYN_RECEIVED:
            if seq_lt(self.tcb.snd_una, ack) and seq_leq(ack, self.tcb.snd_nxt):
                self._process_ack(ack)
                self.tcb.snd_wnd = seg.window
                self.tcb.snd_wl1 = seg.seq
                self.tcb.snd_wl2 = ack
                self._set_state(S.ESTABLISHED)   # [EFSM-10]
                self._merge_reassembly()         # simultaneous-open SYN data, if any
                self._send_pending()
            else:
                # unacceptable ACK: RST <SEQ=SEG.ACK>, remain in SYN-RCVD
                # (3.10.7.4; [EFSM-10] instead tears down — see docs section 9)
                self._emit_rst_reply(seg, seq=ack)
                return
        else:
            if seq_gt(ack, self.tcb.snd_nxt):
                self._send_ack()  # SEG.ACK > SND.NXT: ACK, drop (3.10.7.4)
                return
            if seq_gt(ack, self.tcb.snd_una):
                self._process_ack(ack)
            # window update guard (3.10.7.4; [EFSM-14] has none: docs sec. 9)
            if seq_lt(self.tcb.snd_wl1, seg.seq) or (
                self.tcb.snd_wl1 == seg.seq and seq_leq(self.tcb.snd_wl2, ack)
            ):
                old_wnd = self.tcb.snd_wnd
                self.tcb.snd_wnd = seg.window
                self.tcb.snd_wl1 = seg.seq
                self.tcb.snd_wl2 = ack
                if self.tcb.snd_wnd > 0 and old_wnd == 0:
                    self._cancel_persist()
            # state transitions driven by our FIN being acked
            stx = self.tcb.state
            if stx == S.FIN_WAIT_1 and self.fin_acked:
                self._set_state(S.FIN_WAIT_2)
            elif stx == S.CLOSING and self.fin_acked:
                self._enter_time_wait()
                return
            elif stx == S.LAST_ACK and self.fin_acked:
                self._enter_closed()
                return

        # 7. segment text (3.10.7.4: only EST/FIN-WAIT-1/FIN-WAIT-2 process data)
        if seg.payload and self.tcb.state in (S.ESTABLISHED, S.FIN_WAIT_1, S.FIN_WAIT_2):
            self._recv_data(seg)

        # window may have opened; push anything pending
        self._send_pending()

        # 8. FIN (3.10.7.4 eighth check / [EFSM-38])
        if seg.fin:
            fin_seq = add(seg.seq, len(seg.payload))
            if self.tcb.rcv_nxt == fin_seq:
                self._process_fin()
            elif seq_gt(fin_seq, self.tcb.rcv_nxt):
                # FIN ahead of missing data: processed once reassembly
                # reaches it (3.10.7.4 processes FIN only in sequence)
                self._pending_fin = fin_seq
        self._check_pending_fin()

    def _process_fin(self) -> None:
        """FIN is in sequence at RCV.NXT: consume it and transition [EFSM-38]."""
        self._pending_fin = None
        self.tcb.rcv_nxt = add(self.tcb.rcv_nxt, 1)
        self.peer_fin = True
        self._signal_close()
        self._send_ack()
        stx = self.tcb.state
        if stx == S.ESTABLISHED:
            self._set_state(S.CLOSE_WAIT)      # [EFSM-15]
        elif stx == S.FIN_WAIT_1:
            self._set_state(S.CLOSING)         # [EFSM-21]
        elif stx == S.FIN_WAIT_2:
            self._enter_time_wait()            # [EFSM-25]
        # CLOSE-WAIT/CLOSING/LAST-ACK: remain (3.10.7.4)

    def _check_pending_fin(self) -> None:
        if self._pending_fin is not None and self.tcb.rcv_nxt == self._pending_fin:
            self._process_fin()

    # ------------------------------------------------------------------ #
    # sending
    # ------------------------------------------------------------------ #
    def _make_segment(
        self, seq: int, flags: int, payload: bytes = b"", with_mss: bool = False,
        urgent_ptr: int = 0,
    ) -> TcpSegment:
        wnd = self._rcv_wnd()
        if wnd == 0:
            self._zero_win_advertised = True
        right = add(self.tcb.rcv_nxt, wnd)
        if self._adv_right is None or seq_gt(right, self._adv_right):
            self._adv_right = right
        seg = TcpSegment(
            src_port=self.local_port,
            dst_port=self.remote_port,
            seq=seq,
            ack=self.tcb.rcv_nxt if (flags & Flags.ACK) else 0,
            flags=flags,
            window=wnd,
            urgent_ptr=urgent_ptr,
            payload=payload,
        )
        if with_mss:
            seg.mss = self.tcb.snd_mss
        return seg

    def _transmit(self, seg: TcpSegment) -> None:
        self._tx(seg.build())

    def _queue_and_send(self, flags: int, payload: bytes, with_mss: bool = False,
                        urgent_ptr: int = 0) -> None:
        seq = self.tcb.snd_nxt
        seg = self._make_segment(seq, flags, payload, with_mss=with_mss,
                                 urgent_ptr=urgent_ptr)
        o = _Outgoing(seq=seq, flags=flags, payload=payload, sent_time=self.sched.now())
        if not self.retx:
            self._una_since = self.sched.now()   # oldest unacked data starts now
        self.retx.append(o)
        self.tcb.snd_nxt = add(seq, o.seg_len)
        if self._rtt_pending is None:
            self._rtt_pending = (self.tcb.snd_nxt, self.sched.now())
        self._transmit(seg)
        self._arm_rt()

    def _send_ack(self) -> None:
        self._transmit(self._make_segment(self.tcb.snd_nxt, Flags.ACK, b""))
        self._ack_count = 0
        if self._ack_timer is not None:
            self.sched.cancel(self._ack_timer)
            self._ack_timer = None

    def _schedule_ack(self, force: bool = False) -> None:
        if self.cfg.delayed_ack is None or force:
            self._send_ack()
            return
        self._ack_count += 1
        if self._ack_count >= 2:
            self._send_ack()
            return
        if self._ack_timer is None:
            self._ack_timer = self.sched.call_later(self.cfg.delayed_ack, self._on_delack)

    def _on_delack(self) -> None:
        self._ack_timer = None
        self._send_ack()

    def _emit_rst_reply(self, seg: TcpSegment, seq: int, ack: int = 0, ack_flag: bool = False) -> None:
        flags = Flags.RST | (Flags.ACK if ack_flag else 0)
        r = TcpSegment(
            src_port=seg.dst_port or self.local_port,
            dst_port=seg.src_port or self.remote_port,
            seq=seq,
            ack=ack,
            flags=flags,
            window=0,
        )
        self._transmit(r)

    def _usable_window(self) -> int:
        win_right = add(self.tcb.snd_una, self.tcb.snd_wnd)
        return s32(sub(win_right, self.tcb.snd_nxt))

    def _send_pending(self) -> None:
        st = self.tcb.state
        # 3.10.2: queued data is transmitted only once ESTABLISHED (or in
        # CLOSE-WAIT for the half-closed continue-to-send case, 3.6.1)
        if st not in (S.ESTABLISHED, S.CLOSE_WAIT):
            if st == S.SYN_RECEIVED and self.fin_pending and not self.app_buf:
                pass   # CLOSE in SYN-RCVD with nothing queued: FIN now [EFSM-8]
            else:
                return
        # data
        while self.app_buf:
            if self.tcb.snd_wnd == 0:
                self._arm_persist()
                return
            usable = self._usable_window()
            if usable <= 0:
                return
            n = min(self.tcb.snd_mss, usable, len(self.app_buf))
            if self.cfg.nagle and self.retx and n < self.tcb.snd_mss:
                return   # Nagle 3.7.4: hold sub-MSS while data is outstanding
            chunk = bytes(self.app_buf[:n])
            del self.app_buf[:n]
            last = (len(self.app_buf) == 0 and not self.fin_pending)
            flags = Flags.ACK
            if last and self._push:
                flags |= Flags.PSH
            urg_ptr = 0
            if seq_lt(self.tcb.snd_nxt, self.tcb.snd_up):
                flags |= Flags.URG
                urg_ptr = min(0xFFFF, sub(self.tcb.snd_up, self.tcb.snd_nxt))
            self._queue_and_send(flags, chunk, urgent_ptr=urg_ptr)
            if last and self._push:
                self._push = False
        # FIN
        if self.fin_pending and not self.fin_sent and not self.app_buf:
            if self.tcb.snd_wnd == 0:
                self._arm_persist()
                return
            if self._usable_window() < 1:
                return
            self.fin_seq = self.tcb.snd_nxt
            self._queue_and_send(Flags.ACK | Flags.FIN, b"")
            self.fin_sent = True
            st = self.tcb.state
            if st == S.ESTABLISHED:
                self._set_state(S.FIN_WAIT_1)
            elif st == S.CLOSE_WAIT:
                self._set_state(S.LAST_ACK)
            elif st == S.SYN_RECEIVED:
                self._set_state(S.FIN_WAIT_1)

    # ------------------------------------------------------------------ #
    # ack processing
    # ------------------------------------------------------------------ #
    def _process_ack(self, ack: int) -> None:
        # RTT sample (Karn: only if not invalidated by a retransmission)
        if self._rtt_pending is not None:
            end, t = self._rtt_pending
            if seq_geq(ack, end):
                self.rtt.sample(self.sched.now() - t)
                self._rtt_pending = None

        self.tcb.snd_una = ack

        # trim retransmission queue
        while self.retx:
            o = self.retx[0]
            if seq_leq(o.end, ack):
                self.retx.pop(0)
            elif seq_lt(o.seq, ack):
                trim = sub(ack, o.seq)
                if (o.flags & Flags.SYN) and trim >= 1:
                    o.flags &= ~Flags.SYN
                    trim -= 1
                    o.seq = add(o.seq, 1)
                if trim > 0:
                    o.payload = o.payload[trim:]
                    o.seq = ack
                break
            else:
                break

        if self.fin_sent and seq_gt(self.tcb.snd_una, self.fin_seq):
            self.fin_acked = True

        # user-timeout basis: progress resets the clock for the (new) oldest unacked
        self._una_since = None if not self.retx else self.sched.now()

        self._rto_count = 0
        if self.retx:
            self._restart_rt()
        else:
            self._stop_rt()

    # ------------------------------------------------------------------ #
    # receiving data / reassembly
    # ------------------------------------------------------------------ #
    def _rcv_wnd(self) -> int:
        raw = max(0, self.rcv_wnd_max - len(self.recv_buf))
        if not self.cfg.sws_avoidance or self._adv_right is None:
            return raw
        # receiver SWS avoidance 3.8.6.2.2: advance the advertised right edge
        # only by >= min(MSS, buf/2); never move it left
        cur_right = add(self.tcb.rcv_nxt, raw)
        if seq_gt(cur_right, self._adv_right):
            thresh = min(self.tcb.snd_mss, max(1, self.rcv_wnd_max // 2))
            if sub(cur_right, self._adv_right) < thresh:
                return max(0, s32(sub(self._adv_right, self.tcb.rcv_nxt)))
        return raw

    def _deliver(self, data: bytes) -> None:
        if self.on_data:
            self.on_data(bytes(data))
        else:
            self.recv_buf += data

    def _recv_data(self, seg: TcpSegment) -> None:
        data = seg.payload
        seq = seg.seq
        if not data:
            return
        if seq_lt(seq, self.tcb.rcv_nxt):
            off = sub(self.tcb.rcv_nxt, seq)
            if off >= len(data):
                self._schedule_ack(force=True)
                return
            data = data[off:]
            seq = self.tcb.rcv_nxt
        space = s32(sub(add(self.tcb.rcv_nxt, self._rcv_wnd()), seq))
        if space <= 0:
            self._schedule_ack(force=True)
            return
        if len(data) > space:
            data = data[:space]
        if seq == self.tcb.rcv_nxt:
            self._deliver(data)
            self.tcb.rcv_nxt = add(self.tcb.rcv_nxt, len(data))
            if seg.flags & Flags.PSH:
                self._push_seen = True
            if seg.flags & Flags.URG:
                self._urgent_seen = True
                self.tcb.rcv_up = add(seg.seq, seg.urgent_ptr)
            self._merge_reassembly()
            self._schedule_ack()
        else:
            self._store_ooo(seq, data, seg.flags)
            self._schedule_ack(force=True)

    def _store_ooo(self, seq: int, data: bytes, flags: int = 0) -> None:
        for entry in self.ooo:
            if entry[0] == seq and len(entry[1]) >= len(data):
                return
        self.ooo.append([seq, bytes(data), flags])

    def _merge_reassembly(self) -> None:
        progressed = True
        while progressed:
            progressed = False
            for i, (s, d, fl) in enumerate(self.ooo):
                s_end = add(s, len(d))
                if seq_leq(s_end, self.tcb.rcv_nxt):
                    self.ooo.pop(i)
                    progressed = True
                    break
                if seq_leq(s, self.tcb.rcv_nxt) and seq_gt(s_end, self.tcb.rcv_nxt):
                    off = sub(self.tcb.rcv_nxt, s)
                    tail = d[off:]
                    if tail:
                        self._deliver(tail)
                        self.tcb.rcv_nxt = add(self.tcb.rcv_nxt, len(tail))
                    if fl & Flags.PSH:
                        self._push_seen = True
                    if fl & Flags.URG:
                        self._urgent_seen = True
                    self.ooo.pop(i)
                    progressed = True
                    break

    # ------------------------------------------------------------------ #
    # timers
    # ------------------------------------------------------------------ #
    def _arm_rt(self) -> None:
        if not self.retx:
            return
        if self._rt_timer is not None and not self._rt_timer.cancelled:
            return
        self._rt_timer = self.sched.call_later(self.rtt.rto, self._on_rto)

    def _restart_rt(self) -> None:
        self.sched.cancel(self._rt_timer)
        self._rt_timer = None
        if self.retx:
            self._rt_timer = self.sched.call_later(self.rtt.rto, self._on_rto)

    def _stop_rt(self) -> None:
        self.sched.cancel(self._rt_timer)
        self._rt_timer = None
        self._rto_count = 0

    def _on_rto(self) -> None:
        self._rt_timer = None
        if not self.retx:
            return
        # user timeout 3.8.3 ([EFSM-36] models a standalone timer: docs sec. 9)
        if (
            self.cfg.user_timeout is not None
            and self._una_since is not None
            and self.sched.now() - self._una_since >= self.cfg.user_timeout
        ):
            self._abort("user timeout")
            return
        self._rto_count += 1
        if self._rto_count > self.cfg.max_retransmits:
            self._abort("retransmission timeout")
            return
        self.rtt.backoff()
        self._rtt_pending = None  # Karn: cancel timing across retransmission
        o = self.retx[0]
        o.retransmitted = True
        o.sent_time = self.sched.now()
        seg = self._make_segment(
            o.seq, o.flags, o.payload, with_mss=bool(o.flags & Flags.SYN)
        )
        self._transmit(seg)
        self._rt_timer = self.sched.call_later(self.rtt.rto, self._on_rto)

    def _arm_persist(self) -> None:
        if self._persist_timer is not None and not self._persist_timer.cancelled:
            return
        self._persist_backoff = self.cfg.persist_min
        self._persist_timer = self.sched.call_later(self._persist_backoff, self._on_persist)

    def _cancel_persist(self) -> None:
        self.sched.cancel(self._persist_timer)
        self._persist_timer = None
        self._send_pending()

    def _on_persist(self) -> None:
        self._persist_timer = None
        if self.tcb.snd_wnd > 0:
            self._send_pending()
            return
        # zero-window probe 3.8.6.1: one byte (or the FIN) past SND.NXT
        if self.app_buf:
            chunk = bytes(self.app_buf[:1])
            del self.app_buf[:1]
            self._queue_and_send(Flags.ACK, chunk)
        elif self.fin_pending and not self.fin_sent:
            self.fin_seq = self.tcb.snd_nxt
            self._queue_and_send(Flags.ACK | Flags.FIN, b"")
            self.fin_sent = True
            if self.tcb.state in (S.ESTABLISHED, S.SYN_RECEIVED):
                self._set_state(S.FIN_WAIT_1)
            elif self.tcb.state == S.CLOSE_WAIT:
                self._set_state(S.LAST_ACK)
        else:
            return
        self._persist_backoff = min(
            max(self._persist_backoff * 2, self.cfg.persist_min), self.cfg.persist_max
        )
        self._persist_timer = self.sched.call_later(self._persist_backoff, self._on_persist)

    def _enter_time_wait(self) -> None:
        self._stop_rt()
        self._cancel_persist()
        self._set_state(S.TIME_WAIT)
        self._tw_timer = self.sched.call_later(2 * self.cfg.msl, self._on_time_wait)

    def _restart_time_wait(self) -> None:
        self.sched.cancel(self._tw_timer)
        self._tw_timer = self.sched.call_later(2 * self.cfg.msl, self._on_time_wait)

    def _on_time_wait(self) -> None:
        self._tw_timer = None
        self._enter_closed()

    # -- keepalive (RFC 9293 3.8.4; default off, configurable) --
    def _restart_keepalive(self, reset_probes: bool = False) -> None:
        if self.cfg.keepalive_idle is None:
            return
        if reset_probes:
            self._ka_probes = 0
        self.sched.cancel(self._ka_timer)
        self._ka_timer = None
        if self.tcb.state != S.ESTABLISHED:
            return
        delay = (
            self.cfg.keepalive_idle if self._ka_probes == 0 else self.cfg.keepalive_interval
        )
        self._ka_timer = self.sched.call_later(delay, self._on_keepalive)

    def _cancel_keepalive(self) -> None:
        self.sched.cancel(self._ka_timer)
        self._ka_timer = None
        self._ka_probes = 0

    def _on_keepalive(self) -> None:
        self._ka_timer = None
        if self.cfg.keepalive_idle is None or self.tcb.state != S.ESTABLISHED:
            return
        if self.retx:
            self._restart_keepalive()   # RTO owns liveness while data is in flight
            return
        if self._ka_probes >= self.cfg.keepalive_count:
            self._abort("keepalive timeout")
            return
        # probe: SEG.SEQ = SND.UNA-1, len 0 -> unacceptable, elicits ACK (3.8.4)
        probe = self._make_segment(sub(self.tcb.snd_una, 1), Flags.ACK, b"")
        self._transmit(probe)
        self._ka_probes += 1
        self._restart_keepalive()

    # ------------------------------------------------------------------ #
    # resets / shutdown
    # ------------------------------------------------------------------ #
    def _do_reset(self) -> None:
        # 3.10.7.4 RST in SYN-RCVD: passive open returns to LISTEN [EFSM-9];
        # active open is refused; synchronized states signal reset and close.
        if self.tcb.state == S.SYN_RECEIVED and self.passive:
            self._set_state(S.LISTEN)
            self._reset_buffers()
            self._stop_rt()
            self._cancel_persist()
            self.remote_port = self._cfg_remote_port   # re-wildcard the peer
        elif self.tcb.state == S.SYN_RECEIVED:
            self._signal_error("connection refused")   # [EFSM-9]
            self._enter_closed()
        else:
            self._signal_error("connection reset")
            self._enter_closed()

    def _abort(self, reason: str) -> None:
        if self.tcb.state in (
            S.ESTABLISHED, S.FIN_WAIT_1, S.FIN_WAIT_2, S.CLOSE_WAIT, S.SYN_RECEIVED
        ):
            self._transmit(self._make_segment(self.tcb.snd_nxt, Flags.RST, b""))
        self._signal_error(reason)
        self._enter_closed()

    def _reset_buffers(self) -> None:
        self.app_buf.clear()
        self.recv_buf.clear()
        self.retx.clear()
        self.ooo.clear()
        self.fin_pending = self.fin_sent = self.fin_acked = False
        self._pending_fin = None
        self._rtt_pending = None
        self._una_since = None
        self._adv_right = None

    def _enter_closed(self) -> None:
        self._stop_rt()
        self._cancel_persist()
        self._cancel_keepalive()
        self.sched.cancel(self._tw_timer)
        self._tw_timer = None
        self.sched.cancel(self._ack_timer)
        self._ack_timer = None
        self._reset_buffers()
        self._set_state(S.CLOSED)

    # ------------------------------------------------------------------ #
    # misc helpers
    # ------------------------------------------------------------------ #
    def _new_iss(self) -> int:
        if self._iss_config is not None:
            return self._iss_config & MASK
        return random.randint(0, MASK)

    def _set_state(self, new: ConnState) -> None:
        old = self.tcb.state
        if old == new:
            return
        self.tcb.state = new
        if new == S.ESTABLISHED:
            self._restart_keepalive(reset_probes=True)
        elif old == S.ESTABLISHED:
            self._cancel_keepalive()
        if self.on_state_change:
            self.on_state_change(old, new)

    def _signal_error(self, msg: str) -> None:
        if self.on_error:
            self.on_error(msg)

    def _signal_close(self) -> None:
        if self.on_data:
            self.on_data(b"")  # EOF marker for streaming sinks

    def _warn(self, msg: str) -> None:  # pragma: no cover
        if self.on_error:
            self.on_error("WARN: " + msg)
