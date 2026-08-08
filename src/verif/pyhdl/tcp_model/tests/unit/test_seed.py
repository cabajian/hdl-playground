from tcp_model import ConnState, Flags, TcbSeed, seed_established
from tcp_model.tests.unit.util import make_client, peer_seg

S = ConnState


def test_seed_established_consistency():
    eng, net, sched = make_client(iss=1000)
    eng.seed(seed_established(snd_nxt=2000, rcv_nxt=8000))
    snap = eng.get_tcb_snapshot()
    assert eng.state == S.ESTABLISHED
    assert snap["snd_una"] == 2000
    assert snap["snd_nxt"] == 2000
    assert snap["rcv_nxt"] == 8000


def test_seeded_engine_can_send():
    eng, net, sched = make_client(iss=1000)
    eng.seed(seed_established(snd_nxt=2000, rcv_nxt=8000, snd_wnd=65535))
    eng.send(b"payload")
    seg = net.last
    assert seg.seq == 2000          # picks up where seq tracking left off
    assert seg.ack == 8000
    assert seg.payload == b"payload"


def test_seeded_engine_can_receive():
    eng, net, sched = make_client(iss=1000)
    eng.seed(seed_established(snd_nxt=2000, rcv_nxt=8000))
    eng.on_segment(peer_seg(eng, seq=8000, flags=Flags.ACK, ack=2000, payload=b"in").build())
    assert eng.recv() == b"in"
    assert net.last.ack == 8002


def test_seed_arbitrary_state():
    eng, net, sched = make_client(iss=1000)
    eng.seed(TcbSeed(state=S.CLOSE_WAIT, iss=999, irs=4999,
                     snd_una=1000, snd_nxt=1000, rcv_nxt=5050))
    assert eng.state == S.CLOSE_WAIT
    # closing from CLOSE_WAIT goes to LAST_ACK
    eng.close()
    assert eng.state == S.LAST_ACK
    assert net.last.fin


def test_seeded_close_handshake_completes():
    eng, net, sched = make_client(iss=1000, msl=0.5)
    eng.seed(seed_established(snd_nxt=2000, rcv_nxt=8000))
    eng.close()
    fin = net.last
    assert eng.state == S.FIN_WAIT_1
    eng.on_segment(peer_seg(eng, seq=8000, flags=Flags.ACK, ack=fin.seq + 1).build())
    assert eng.state == S.FIN_WAIT_2
    eng.on_segment(peer_seg(eng, seq=8000, flags=Flags.FIN | Flags.ACK, ack=fin.seq + 1).build())
    assert eng.state == S.TIME_WAIT
    sched.advance(1.0)
    assert eng.state == S.CLOSED
