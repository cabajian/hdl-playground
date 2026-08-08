from tcp_model import ConnState, Flags, TcpSegment
from tcp_model.tests.unit.util import join, make_pair, pump

S = ConnState


def test_e2e_handshake():
    sched, wire, client, server, csink, ssink = make_pair()
    server.passive_open()
    client.active_open()
    pump(sched)
    assert client.state == S.ESTABLISHED
    assert server.state == S.ESTABLISHED
    # sequence spaces line up
    cs = client.get_tcb_snapshot()
    ss = server.get_tcb_snapshot()
    assert cs["snd_nxt"] == ss["rcv_nxt"]
    assert ss["snd_nxt"] == cs["rcv_nxt"]


def test_e2e_data_transfer():
    sched, wire, client, server, csink, ssink = make_pair()
    server.passive_open()
    client.active_open()
    pump(sched)
    payload = bytes((i * 7) & 0xFF for i in range(5000))  # spans many MSS segments
    client.send(payload)
    pump(sched)
    assert join(ssink) == payload
    # all client data acknowledged
    assert client.get_tcb_snapshot()["retx_depth"] == 0


def test_e2e_bidirectional():
    sched, wire, client, server, csink, ssink = make_pair()
    server.passive_open()
    client.active_open()
    pump(sched)
    client.send(b"ping" * 100)
    server.send(b"pong" * 100)
    pump(sched)
    assert join(ssink) == b"ping" * 100
    assert join(csink) == b"pong" * 100


def test_e2e_recovers_from_loss():
    # drop the 1st and 3rd A->B data segments; retransmission must recover
    dropped = {0, 2}
    seen = {"n": -1}

    def a_to_b(seg, n):
        if seg.payload:
            seen["n"] += 1
            if seen["n"] in dropped:
                return False  # drop
        return True

    sched = None
    from tcp_model import ManualScheduler, Wire, TcpEngine, TimerConfig
    sched = ManualScheduler()
    wire = Wire(sched, delay=0.01, a_to_b_filter=a_to_b)
    ssink = []
    cfg = TimerConfig(rto_initial=0.1, rto_min=0.1)
    client = TcpEngine(local_port=40000, remote_port=80, scheduler=sched,
                       tx=wire.tx_from_a, iss=1000, timers=cfg, name="client")
    server = TcpEngine(local_port=80, remote_port=40000, scheduler=sched,
                       tx=wire.tx_from_b, iss=5000, timers=cfg,
                       on_data=ssink.append, name="server")
    wire.connect(client, server)
    server.passive_open()
    client.active_open()
    pump(sched)
    msg = bytes(range(256)) * 16     # 4096 bytes -> 3 MSS segments
    client.send(msg)
    pump(sched)
    assert b"".join(b for b in ssink if b) == msg
    assert wire.dropped == 2


def test_e2e_sequence_wraparound():
    # client ISN sits just below 2**32 so the data stream crosses the wrap boundary
    sched, wire, client, server, csink, ssink = make_pair(iss_c=(1 << 32) - 4, iss_s=5000)
    server.passive_open()
    client.active_open()
    pump(sched)
    assert client.state == S.ESTABLISHED
    msg = bytes(range(64))               # spans seq ...FFFD, FFFE, FFFF, 0, 1, ...
    client.send(msg)
    pump(sched)
    assert join(ssink) == msg


def test_e2e_full_close():
    sched, wire, client, server, csink, ssink = make_pair(msl=0.2)
    server.passive_open()
    client.active_open()
    pump(sched)
    client.send(b"final message")
    client.close()
    pump(sched)
    # server saw the data and the FIN
    assert join(ssink) == b"final message"
    assert server.state == S.CLOSE_WAIT
    server.close()
    pump(sched)
    assert server.state == S.CLOSED
    assert client.state == S.CLOSED   # TIME_WAIT expired during pump
