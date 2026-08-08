"""Connection state enum, the Transmission Control Block, and the seed API."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class ConnState(enum.Enum):
    CLOSED = "CLOSED"
    LISTEN = "LISTEN"
    SYN_SENT = "SYN_SENT"
    SYN_RECEIVED = "SYN_RECEIVED"
    ESTABLISHED = "ESTABLISHED"
    FIN_WAIT_1 = "FIN_WAIT_1"
    FIN_WAIT_2 = "FIN_WAIT_2"
    CLOSE_WAIT = "CLOSE_WAIT"
    CLOSING = "CLOSING"
    LAST_ACK = "LAST_ACK"
    TIME_WAIT = "TIME_WAIT"


@dataclass
class TCB:
    """Transmission Control Block: the per-connection protocol variables."""

    state: ConnState = ConnState.CLOSED

    # Send sequence space (RFC 9293 3.3.1)
    iss: int = 0
    snd_una: int = 0
    snd_nxt: int = 0
    snd_wnd: int = 0
    snd_wl1: int = 0    # seq of last window update
    snd_wl2: int = 0    # ack of last window update
    snd_up: int = 0     # send urgent pointer

    # Receive sequence space
    irs: int = 0
    rcv_nxt: int = 0
    rcv_wnd: int = 0    # advertised window (current; recomputed from buffer)
    rcv_up: int = 0     # receive urgent pointer

    # Negotiated
    snd_mss: int = 1460   # MSS we will send toward the peer

    def snapshot(self) -> dict:
        return {
            "state": self.state.value,
            "iss": self.iss,
            "snd_una": self.snd_una,
            "snd_nxt": self.snd_nxt,
            "snd_wnd": self.snd_wnd,
            "irs": self.irs,
            "rcv_nxt": self.rcv_nxt,
            "rcv_wnd": self.rcv_wnd,
            "snd_mss": self.snd_mss,
        }


@dataclass
class TcbSeed:
    """Seed the model into an arbitrary starting state.

    Leave a field ``None`` to use a sensible derived default. ``seed_established``
    is the convenience constructor for the common "drop in mid-stream" case, which
    is also the migration path from manual seq/ack tracking.
    """

    state: ConnState = ConnState.CLOSED
    iss: int | None = None      # our ISN
    irs: int | None = None      # peer's ISN we received
    snd_una: int | None = None
    snd_nxt: int | None = None
    snd_wnd: int = 65535
    rcv_nxt: int | None = None
    rcv_wnd: int = 65535
    snd_mss: int = 1460
    # optional RTT/RTO seeds
    srtt: float | None = None
    rttvar: float | None = None
    rto: float | None = None


def seed_established(
    snd_nxt: int,
    rcv_nxt: int,
    snd_wnd: int = 65535,
    rcv_wnd: int = 65535,
    snd_mss: int = 1460,
) -> TcbSeed:
    """Build a consistent ESTABLISHED seed.

    Post-handshake, SND.UNA == SND.NXT and the ISN is one before SND.NXT.
    Pass your current manually-tracked seq as ``snd_nxt`` and ack as ``rcv_nxt``.
    """
    return TcbSeed(
        state=ConnState.ESTABLISHED,
        iss=(snd_nxt - 1) & 0xFFFFFFFF,
        irs=(rcv_nxt - 1) & 0xFFFFFFFF,
        snd_una=snd_nxt,
        snd_nxt=snd_nxt,
        snd_wnd=snd_wnd,
        rcv_nxt=rcv_nxt,
        rcv_wnd=rcv_wnd,
        snd_mss=snd_mss,
    )
