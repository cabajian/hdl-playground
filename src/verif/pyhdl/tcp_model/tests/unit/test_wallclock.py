"""Timer behavior verified against the real wall clock (WallClockScheduler).

Same engine, same tests conceptually as the ManualScheduler suites — but time
actually elapses. TimerConfig periods are ms-scale so the whole module stays
under a few seconds of wall time.
"""

import pytest

from tcp_model import (
    CaptureNet, ConnState as S, Flags, TcpEngine, TimerConfig, Wire,
)
from tcp_model.ports.scheduler import WallClockScheduler
from .util import CLIENT_PORT, SERVER_PORT, peer_seg

U = 0.02  # base time unit: 20 ms


def _client(sched, **cfg_kw):
    net = CaptureNet()
    cfg = TimerConfig(rto_initial=2 * U, rto_min=2 * U, rto_max=8 * U,
                      msl=2 * U, persist_min=2 * U, persist_max=8 * U, **cfg_kw)
    eng = TcpEngine(local_port=CLIENT_PORT, remote_port=SERVER_PORT,
                    scheduler=sched, tx=net.tx, iss=1000, timers=cfg,
                    name="wc-client")
    net.bind(eng)
    return eng, net


def _establish(eng, net):
    eng.active_open()
    syn = net.pop_all()[0]
    eng.on_segment(peer_seg(eng, seq=5000, flags=Flags.SYN | Flags.ACK,
                            ack=syn.seq + 1).build())
    net.clear()


def test_rto_retransmits_with_backoff():
    sched = WallClockScheduler()
    eng, net = _client(sched)
    _establish(eng, net)
    rto0 = eng.rtt.rto
    eng.send(b"lost")
    sched.run_for(7 * U)                     # first RTO at 2U, backoff doubles
    sent = [s for s in net.pop_all() if s.payload == b"lost"]
    assert len(sent) >= 2                    # original + at least one retransmit
    assert eng.rtt.rto > rto0                # exponential backoff engaged


def test_rto_gives_up_and_aborts():
    errors = []
    sched = WallClockScheduler()
    eng, net = _client(sched, max_retransmits=2)
    eng.on_error = errors.append
    _establish(eng, net)
    eng.send(b"doomed")
    sched.run_until_idle(timeout=3.0)
    assert eng.state == S.CLOSED and "retransmission timeout" in errors


def test_persist_probes_zero_window_then_recovers():
    sched = WallClockScheduler()
    eng, net = _client(sched)
    _establish(eng, net)
    eng.on_segment(peer_seg(eng, seq=5001, flags=Flags.ACK, ack=1001,
                            window=0).build())          # peer closes window
    eng.send(b"held")
    assert net.pop_all() == []               # nothing sendable yet
    sched.run_for(3 * U)                     # persist_min = 2U
    probes = [s for s in net.pop_all() if s.payload]
    assert probes and probes[0].payload == b"h"          # 1-byte probe
    eng.on_segment(peer_seg(eng, seq=5001, flags=Flags.ACK, ack=probes[0].seq + 1,
                            window=4096).build())        # window opens
    rest = [s for s in net.pop_all() if s.payload]
    assert b"".join(s.payload for s in rest) == b"eld"


def test_time_wait_expires_after_2msl():
    sched = WallClockScheduler()
    eng, net = _client(sched)
    _establish(eng, net)
    eng.close()
    net.clear()
    eng.on_segment(peer_seg(eng, seq=5001, flags=Flags.FIN | Flags.ACK,
                            ack=1002).build())
    assert eng.state == S.TIME_WAIT
    t0 = sched.now()
    sched.run_until_idle(timeout=2.0)
    assert eng.state == S.CLOSED
    assert sched.now() - t0 >= 2 * (2 * U) * 0.9         # ~2*MSL elapsed


def test_keepalive_probes_then_aborts():
    errors = []
    sched = WallClockScheduler()
    eng, net = _client(sched, keepalive_idle=2 * U, keepalive_interval=U,
                       keepalive_count=2)
    eng.on_error = errors.append
    _establish(eng, net)
    sched.run_for(3 * U)
    probes = net.pop_all()
    assert probes and probes[0].seq == 1000              # SND.UNA - 1
    sched.run_until_idle(timeout=2.0)
    assert eng.state == S.CLOSED and "keepalive timeout" in errors


def test_delayed_ack_fires_on_timer():
    sched = WallClockScheduler()
    eng, net = _client(sched, delayed_ack=2 * U)
    _establish(eng, net)
    eng.on_segment(peer_seg(eng, seq=5001, flags=Flags.ACK, ack=1001,
                            payload=b"one").build())
    assert net.pop_all() == []               # ACK withheld
    sched.run_for(3 * U)
    a = net.pop_all()[-1]
    assert a.ack_flag and a.ack == 5004      # coalesced ACK fired on timer


def test_user_timeout_aborts_in_real_time():
    errors = []
    sched = WallClockScheduler()
    eng, net = _client(sched, user_timeout=5 * U)
    eng.on_error = errors.append
    _establish(eng, net)
    eng.send(b"unacked")
    sched.run_for(12 * U)
    assert eng.state == S.CLOSED and "user timeout" in errors


def test_full_lifecycle_two_engines_wall_clock():
    sched = WallClockScheduler()
    wire = Wire(sched, delay=U / 4)
    csink, ssink = [], []
    cfg = dict(rto_initial=4 * U, rto_min=4 * U, msl=U)
    a = TcpEngine(local_port=CLIENT_PORT, remote_port=SERVER_PORT, scheduler=sched,
                  tx=wire.tx_from_a, iss=1000, timers=TimerConfig(**cfg),
                  on_data=csink.append, name="wc-a")
    b = TcpEngine(local_port=SERVER_PORT, remote_port=CLIENT_PORT, scheduler=sched,
                  tx=wire.tx_from_b, iss=5000, timers=TimerConfig(**cfg),
                  on_data=ssink.append, name="wc-b")
    wire.connect(a, b)
    b.passive_open()
    a.active_open()
    sched.run_for(2 * U)
    assert a.state == S.ESTABLISHED and b.state == S.ESTABLISHED
    a.send(b"ping")
    sched.run_for(U)
    b.send(b"pong")
    sched.run_for(U)
    a.close()
    sched.run_for(U)
    b.close()
    sched.run_until_idle(timeout=3.0)
    assert a.state == S.CLOSED and b.state == S.CLOSED
    assert b"".join(x for x in ssink if x) == b"ping"
    assert b"".join(x for x in csink if x) == b"pong"


def test_retransmission_recovers_over_lossy_wire():
    sched = WallClockScheduler()
    dropped = {"n": 0}

    def drop_first_data(seg, n):
        if seg.payload and dropped["n"] == 0:
            dropped["n"] += 1
            return False                     # drop it
        return True

    wire = Wire(sched, delay=U / 4, a_to_b_filter=drop_first_data)
    ssink = []
    cfg = dict(rto_initial=2 * U, rto_min=2 * U, msl=U)
    a = TcpEngine(local_port=CLIENT_PORT, remote_port=SERVER_PORT, scheduler=sched,
                  tx=wire.tx_from_a, iss=1000, timers=TimerConfig(**cfg), name="la")
    b = TcpEngine(local_port=SERVER_PORT, remote_port=CLIENT_PORT, scheduler=sched,
                  tx=wire.tx_from_b, iss=5000, timers=TimerConfig(**cfg),
                  on_data=ssink.append, name="lb")
    wire.connect(a, b)
    b.passive_open()
    a.active_open()
    sched.run_for(2 * U)
    a.send(b"resilient")
    sched.run_for(6 * U)                     # RTO at 2U recovers the loss
    assert b"".join(x for x in ssink if x) == b"resilient"
    assert dropped["n"] == 1
