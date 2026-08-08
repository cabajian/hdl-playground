from tcp_model import ConnState, Flags
from tcp_model.tests.unit.util import make_client, make_server, peer_seg

S = ConnState


def test_active_open_sends_syn():
    eng, net, sched = make_client(iss=1000)
    eng.active_open()
    assert eng.state == S.SYN_SENT
    syn = net.last
    assert syn.syn and not syn.ack_flag
    assert syn.seq == 1000
    assert syn.mss is not None  # MSS option advertised on SYN


def test_active_open_completes_on_synack():
    eng, net, sched = make_client(iss=1000)
    eng.active_open()
    net.clear()
    synack = peer_seg(eng, seq=5000, flags=Flags.SYN | Flags.ACK, ack=1001)
    eng.on_segment(synack.build())
    assert eng.state == S.ESTABLISHED
    ack = net.last
    assert ack.ack_flag and not ack.syn
    assert ack.ack == 5001   # acks peer's SYN
    assert ack.seq == 1001   # our next seq
    snap = eng.get_tcb_snapshot()
    assert snap["snd_una"] == 1001
    assert snap["rcv_nxt"] == 5001


def test_passive_open_handshake():
    eng, net, sched = make_server(iss=5000)
    eng.passive_open()
    assert eng.state == S.LISTEN
    syn = peer_seg(eng, seq=1000, flags=Flags.SYN)
    eng.on_segment(syn.build())
    assert eng.state == S.SYN_RECEIVED
    synack = net.last
    assert synack.syn and synack.ack_flag
    assert synack.seq == 5000
    assert synack.ack == 1001
    # peer completes with ACK
    ack = peer_seg(eng, seq=1001, flags=Flags.ACK, ack=5001)
    eng.on_segment(ack.build())
    assert eng.state == S.ESTABLISHED
    assert eng.remote_port != 0  # learned from the SYN


def test_passive_open_adopts_peer_mss():
    eng, net, sched = make_server(iss=5000)
    eng.passive_open()
    syn = peer_seg(eng, seq=1000, flags=Flags.SYN, mss=536)
    eng.on_segment(syn.build())
    assert eng.tcb.snd_mss == 536  # min(default 1460, peer 536)


def test_rst_to_closed_with_ack():
    eng, net, sched = make_client()
    # engine is CLOSED; an incoming ACK should elicit a RST
    seg = peer_seg(eng, seq=9999, flags=Flags.ACK, ack=4321)
    eng.on_segment(seg.build())
    rst = net.last
    assert rst.rst
    assert rst.seq == 4321  # SEQ = SEG.ACK


def test_rst_to_closed_without_ack():
    eng, net, sched = make_client()
    seg = peer_seg(eng, seq=9999, flags=Flags.SYN)  # no ACK
    eng.on_segment(seg.build())
    rst = net.last
    assert rst.rst and rst.ack_flag
    assert rst.ack == 10000  # SEG.SEQ + SEG.LEN (SYN counts 1)


def test_syn_sent_rejected_by_rst_is_refused():
    errors = []
    eng, net, sched = make_client(iss=1000)
    eng.on_error = errors.append
    eng.active_open()
    net.clear()
    # RST+ACK acking our SYN -> connection refused
    rst = peer_seg(eng, seq=0, flags=Flags.RST | Flags.ACK, ack=1001)
    eng.on_segment(rst.build())
    assert eng.state == S.CLOSED
    assert any("refused" in e for e in errors)


def test_simultaneous_open():
    eng, net, sched = make_client(iss=1000)
    eng.active_open()       # SYN sent, SYN_SENT
    net.clear()
    # peer's SYN arrives with NO ack (simultaneous open)
    syn = peer_seg(eng, seq=5000, flags=Flags.SYN)
    eng.on_segment(syn.build())
    assert eng.state == S.SYN_RECEIVED
    out = net.last
    assert out.syn and out.ack_flag      # we respond SYN-ACK
    assert out.seq == 1000               # same ISS, no new seq consumed
    assert out.ack == 5001
    # peer now acks our SYN -> ESTABLISHED
    ack = peer_seg(eng, seq=5001, flags=Flags.ACK, ack=1001)
    eng.on_segment(ack.build())
    assert eng.state == S.ESTABLISHED
