"""The pyhdl-if dress rehearsal: engines driven by a spoofed external clock.

``SimScheduler`` implements the engine's Scheduler over an ExternalTimeService
(``now_ns``/``wait_ns``) — the exact shape pyhdl-if imp tasks will present.
``SpoofClock`` plays the SystemVerilog side: a discrete-event virtual clock.
Everything runs on one asyncio loop; sim time advances only via the clock.
Swapping SpoofClock for the real imp object is the only integration change.

Tests are plain sync functions running scenarios with ``asyncio.run`` (no
pytest-asyncio dependency).
"""

import asyncio

from tcp_model import CaptureNet, ConnState as S, Flags, TcpEngine, TimerConfig, Wire
from tcp_model.ports.sim_clock import SimScheduler, SpoofClock
from .util import CLIENT_PORT, SERVER_PORT, peer_seg

MS = 1_000_000            # ns per millisecond
RTO = 0.001               # 1 ms initial RTO for these scenarios


def _cfg(**kw):
    base = dict(rto_initial=RTO, rto_min=RTO, rto_max=8 * RTO, msl=0.0005)
    base.update(kw)
    return TimerConfig(**base)


def _engine(sched, net, **kw):
    eng = TcpEngine(local_port=CLIENT_PORT, remote_port=SERVER_PORT,
                    scheduler=sched, tx=net.tx, iss=1000, timers=_cfg(**kw),
                    name="sim")
    net.bind(eng)
    return eng


async def _establish(eng, net, clk):
    eng.active_open()
    syn = net.pop_all()[0]
    eng.on_segment(peer_seg(eng, seq=5000, flags=Flags.SYN | Flags.ACK,
                            ack=syn.seq + 1).build())
    net.clear()
    await clk.run_for(0)


def test_rto_fires_at_exact_sim_time():
    async def scenario():
        clk = SpoofClock()
        sched = SimScheduler(clk, asyncio.get_running_loop())
        net = CaptureNet()
        eng = _engine(sched, net)
        await _establish(eng, net, clk)
        eng.send(b"lost")
        await clk.run_for(int(2.5 * MS))
        # RTO at 1 ms; backoff doubles -> next due at 3 ms: exactly 1 retransmit
        copies = [s for s in net.pop_all() if s.payload == b"lost"]
        assert len(copies) == 2
        assert clk.now_ns() == int(2.5 * MS)         # sim time is authoritative
        await clk.run_for(int(0.6 * MS))             # cross the 3 ms deadline
        assert len([s for s in net.pop_all() if s.payload == b"lost"]) == 1
        eng.abort()
    asyncio.run(scenario())


def test_ack_cancels_rto_no_spurious_retransmit():
    async def scenario():
        clk = SpoofClock()
        sched = SimScheduler(clk, asyncio.get_running_loop())
        net = CaptureNet()
        eng = _engine(sched, net)
        await _establish(eng, net, clk)
        eng.send(b"prompt")
        await clk.run_for(int(0.2 * MS))
        eng.on_segment(peer_seg(eng, seq=5001, flags=Flags.ACK,
                                ack=1001 + 6).build())      # acked well inside RTO
        net.clear()
        await clk.run_for(int(5 * MS))
        assert [s for s in net.pop_all() if s.payload] == []   # timer cancelled
        assert eng.state == S.ESTABLISHED
        eng.abort()
    asyncio.run(scenario())


def test_time_wait_expires_at_2msl_sim_ns():
    async def scenario():
        clk = SpoofClock()
        sched = SimScheduler(clk, asyncio.get_running_loop())
        net = CaptureNet()
        eng = _engine(sched, net)                   # msl = 0.0005 s = 0.5 ms
        await _establish(eng, net, clk)
        eng.close()
        net.clear()
        eng.on_segment(peer_seg(eng, seq=5001, flags=Flags.FIN | Flags.ACK,
                                ack=1002).build())
        assert eng.state == S.TIME_WAIT
        t0 = clk.now_ns()
        await clk.run_for(int(0.9 * MS))            # 2*MSL = 1 ms: not yet
        assert eng.state == S.TIME_WAIT
        await clk.run_for(int(0.2 * MS))
        assert eng.state == S.CLOSED
        assert clk.now_ns() - t0 == int(1.1 * MS)
    asyncio.run(scenario())


def test_two_engines_lossy_wire_pure_sim_time():
    async def scenario():
        clk = SpoofClock()
        sched = SimScheduler(clk, asyncio.get_running_loop())
        dropped = {"n": 0}

        def drop_first_data(seg, n):
            if seg.payload and dropped["n"] == 0:
                dropped["n"] += 1
                return False
            return True

        wire = Wire(sched, delay=0.00001, a_to_b_filter=drop_first_data)  # 10 us
        ssink = []
        a = TcpEngine(local_port=CLIENT_PORT, remote_port=SERVER_PORT,
                      scheduler=sched, tx=wire.tx_from_a, iss=1000,
                      timers=_cfg(), name="sim-a")
        b = TcpEngine(local_port=SERVER_PORT, remote_port=CLIENT_PORT,
                      scheduler=sched, tx=wire.tx_from_b, iss=5000,
                      timers=_cfg(), on_data=ssink.append, name="sim-b")
        wire.connect(a, b)
        b.passive_open()
        a.active_open()
        await clk.run_for(int(0.1 * MS))            # handshake over wire delays
        assert a.state == S.ESTABLISHED and b.state == S.ESTABLISHED
        a.send(b"through-loss")
        await clk.run_for(int(0.1 * MS))
        assert b"".join(x for x in ssink if x) == b""       # first copy dropped
        await clk.run_for(int(2 * MS))              # RTO recovers it
        assert b"".join(x for x in ssink if x) == b"through-loss"
        assert dropped["n"] == 1
        a.abort(); b.abort()
    asyncio.run(scenario())


def test_full_close_sequence_under_spoofed_clock():
    async def scenario():
        clk = SpoofClock()
        sched = SimScheduler(clk, asyncio.get_running_loop())
        wire = Wire(sched, delay=0.00001)
        a = TcpEngine(local_port=CLIENT_PORT, remote_port=SERVER_PORT,
                      scheduler=sched, tx=wire.tx_from_a, iss=1000,
                      timers=_cfg(), name="cl-a")
        b = TcpEngine(local_port=SERVER_PORT, remote_port=CLIENT_PORT,
                      scheduler=sched, tx=wire.tx_from_b, iss=5000,
                      timers=_cfg(), name="cl-b")
        wire.connect(a, b)
        b.passive_open()
        a.active_open()
        await clk.run_for(int(0.1 * MS))
        a.close()
        await clk.run_for(int(0.1 * MS))
        assert b.state == S.CLOSE_WAIT
        b.close()
        await clk.run_for(int(0.1 * MS))
        assert b.state == S.CLOSED                  # LAST-ACK acked
        assert a.state == S.TIME_WAIT
        await clk.run_for(int(1.2 * MS))            # 2*MSL = 1 ms
        assert a.state == S.CLOSED
        assert clk.pending == 0                     # no timers left behind
    asyncio.run(scenario())
