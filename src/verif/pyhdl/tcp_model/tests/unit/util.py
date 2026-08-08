"""Helpers shared across the unit tests (not a test module itself)."""

from __future__ import annotations

from tcp_model import (
    CaptureNet,
    Flags,
    ManualScheduler,
    TcpEngine,
    TcpSegment,
    TimerConfig,
    Wire,
)

CLIENT_PORT = 40000
SERVER_PORT = 80


def peer_seg(engine, seq, flags, ack=0, window=65535, payload=b"", mss=None):
    """Build a segment as if it came from the engine's peer."""
    s = TcpSegment(
        src_port=engine.remote_port or CLIENT_PORT,
        dst_port=engine.local_port,
        seq=seq,
        ack=ack,
        flags=flags,
        window=window,
        payload=payload,
    )
    if mss is not None:
        s.mss = mss
    return s


def make_client(iss=1000, peer_port=SERVER_PORT, rcv_wnd=65535, **timer_kw):
    sched = ManualScheduler()
    net = CaptureNet()
    eng = TcpEngine(
        local_port=CLIENT_PORT,
        remote_port=peer_port,
        scheduler=sched,
        tx=net.tx,
        iss=iss,
        rcv_wnd=rcv_wnd,
        timers=TimerConfig(**timer_kw) if timer_kw else None,
        name="client",
    )
    net.bind(eng)
    return eng, net, sched


def make_server(iss=5000, **timer_kw):
    sched = ManualScheduler()
    net = CaptureNet()
    eng = TcpEngine(
        local_port=SERVER_PORT,
        remote_port=0,
        scheduler=sched,
        tx=net.tx,
        iss=iss,
        timers=TimerConfig(**timer_kw) if timer_kw else None,
        name="server",
    )
    net.bind(eng)
    return eng, net, sched


def client_to_established(eng, net, peer_iss=5000, peer_win=65535):
    """active_open + inject SYN-ACK -> ESTABLISHED. Returns the sent SYN."""
    eng.active_open()
    syn = net.pop_all()[0]
    synack = peer_seg(
        eng, seq=peer_iss, flags=Flags.SYN | Flags.ACK, ack=syn.seq + 1, window=peer_win
    )
    eng.on_segment(synack.build())
    net.clear()  # drop the client's ACK
    return syn, peer_iss


def server_to_established(eng, net, peer_iss=1000, peer_win=65535):
    """passive_open + inject SYN, then ACK -> ESTABLISHED."""
    eng.passive_open()
    syn = peer_seg(eng, seq=peer_iss, flags=Flags.SYN, window=peer_win)
    eng.on_segment(syn.build())
    synack = net.pop_all()[0]
    ack = peer_seg(
        eng, seq=peer_iss + 1, flags=Flags.ACK, ack=synack.seq + 1, window=peer_win
    )
    eng.on_segment(ack.build())
    net.clear()
    return peer_iss, synack.seq


def make_pair(iss_c=1000, iss_s=5000, delay=0.001, **timer_kw):
    """Two engines wired back-to-back through a shared scheduler."""
    sched = ManualScheduler()
    wire = Wire(sched, delay=delay)
    csink: list[bytes] = []
    ssink: list[bytes] = []
    cfg = TimerConfig(**timer_kw) if timer_kw else None
    cfg_s = TimerConfig(**timer_kw) if timer_kw else None
    client = TcpEngine(
        local_port=CLIENT_PORT, remote_port=SERVER_PORT, scheduler=sched,
        tx=wire.tx_from_a, iss=iss_c, on_data=csink.append, timers=cfg, name="client",
    )
    server = TcpEngine(
        local_port=SERVER_PORT, remote_port=CLIENT_PORT, scheduler=sched,
        tx=wire.tx_from_b, iss=iss_s, on_data=ssink.append, timers=cfg_s, name="server",
    )
    wire.connect(client, server)
    return sched, wire, client, server, csink, ssink


def enter(state, **timer_kw):
    """Drive a fresh client engine into ``state``; return (eng, net, sched, ctx).

    ctx: peer_seq = peer's next in-order SEQ, our_nxt = our SND.NXT,
    our_una = our SND.UNA. Client ISS=1000, peer ISS=5000 throughout.
    """
    eng, net, sched = make_client(**timer_kw)
    if state == "CLOSED":
        return eng, net, sched, dict(peer_seq=5000, our_nxt=1000, our_una=1000)
    if state == "SYN_SENT":
        eng.active_open()
        net.clear()
        return eng, net, sched, dict(peer_seq=5000, our_nxt=1001, our_una=1000)
    client_to_established(eng, net)          # our 1001/1001, peer next 5001
    ctx = dict(peer_seq=5001, our_nxt=1001, our_una=1001)
    if state == "ESTABLISHED":
        return eng, net, sched, ctx
    if state == "CLOSE_WAIT":
        eng.on_segment(peer_seg(eng, seq=5001, flags=Flags.FIN | Flags.ACK, ack=1001).build())
        net.clear()
        ctx["peer_seq"] = 5002
        return eng, net, sched, ctx
    if state == "LAST_ACK":
        eng.on_segment(peer_seg(eng, seq=5001, flags=Flags.FIN | Flags.ACK, ack=1001).build())
        eng.close()                           # FIN seq=1001
        net.clear()
        ctx.update(peer_seq=5002, our_nxt=1002)
        return eng, net, sched, ctx
    # active-close family: our FIN at 1001
    eng.close()
    net.clear()
    ctx["our_nxt"] = 1002
    if state == "FIN_WAIT_1":
        return eng, net, sched, ctx
    if state == "CLOSING":
        eng.on_segment(peer_seg(eng, seq=5001, flags=Flags.FIN | Flags.ACK, ack=1001).build())
        net.clear()
        ctx["peer_seq"] = 5002
        return eng, net, sched, ctx
    eng.on_segment(peer_seg(eng, seq=5001, flags=Flags.ACK, ack=1002).build())  # FIN acked
    ctx["our_una"] = 1002
    if state == "FIN_WAIT_2":
        return eng, net, sched, ctx
    if state == "TIME_WAIT":
        eng.on_segment(peer_seg(eng, seq=5001, flags=Flags.FIN | Flags.ACK, ack=1002).build())
        net.clear()
        ctx["peer_seq"] = 5002
        return eng, net, sched, ctx
    raise ValueError(state)


def pump(sched, max_steps=100000):
    return sched.run_until_idle(max_steps=max_steps)


def join(sink):
    return b"".join(b for b in sink if b)  # drop EOF markers
