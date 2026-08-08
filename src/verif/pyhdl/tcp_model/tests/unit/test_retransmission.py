from tcp_model import ConnState, Flags
from tcp_model.tests.unit.util import client_to_established, make_client, peer_seg

S = ConnState


def test_rto_retransmits_unacked_data():
    eng, net, sched = make_client(iss=1000, rto_initial=1.0, rto_min=1.0)
    client_to_established(eng, net, peer_iss=5000)
    eng.send(b"hello")
    first = net.pop_all()
    assert any(s.payload == b"hello" for s in first)
    # no ACK arrives; RTO fires
    sched.advance(1.0)
    retx = net.pop_all()
    assert any(s.payload == b"hello" and s.seq == 1001 for s in retx)


def test_rto_exponential_backoff():
    eng, net, sched = make_client(iss=1000, rto_initial=1.0, rto_min=1.0, rto_max=100.0)
    client_to_established(eng, net, peer_iss=5000)
    eng.send(b"x")
    net.clear()
    sched.advance(1.0)          # first RTO at t=1
    assert len(net.pop_all()) == 1
    sched.advance(1.5)          # next RTO is ~2s later, not yet
    assert net.pop_all() == []
    sched.advance(0.6)          # now past t=3
    assert len(net.pop_all()) == 1


def test_retransmit_stops_after_ack():
    eng, net, sched = make_client(iss=1000, rto_initial=1.0, rto_min=1.0)
    client_to_established(eng, net, peer_iss=5000)
    eng.send(b"hello")
    net.clear()
    ack = peer_seg(eng, seq=5001, flags=Flags.ACK, ack=1006, window=65535)
    eng.on_segment(ack.build())
    assert eng.get_tcb_snapshot()["retx_depth"] == 0
    sched.advance(5.0)          # timer should be stopped
    assert net.pop_all() == []


def test_karn_no_rtt_sample_on_retransmitted_segment():
    eng, net, sched = make_client(iss=1000, rto_initial=1.0, rto_min=1.0)
    client_to_established(eng, net, peer_iss=5000)
    # the handshake produced one RTT sample; reset estimator to observe Karn cleanly
    eng.rtt.srtt = None
    eng.rtt.rttvar = None
    eng.rtt.rto = 1.0
    eng.send(b"hello")
    net.clear()
    sched.advance(1.0)              # RTO -> retransmit, RTT timing invalidated (Karn)
    net.clear()
    ack = peer_seg(eng, seq=5001, flags=Flags.ACK, ack=1006, window=65535)
    eng.on_segment(ack.build())
    assert eng.rtt.srtt is None     # ambiguous ACK was NOT sampled


def test_fresh_segment_after_retransmit_is_sampled():
    eng, net, sched = make_client(iss=1000, rto_initial=1.0, rto_min=0.0, rtt_g=0.0)
    client_to_established(eng, net, peer_iss=5000)
    eng.rtt.srtt = None
    eng.rtt.rto = 1.0               # keep RTO above the 0.2s we measure
    eng.send(b"hello")
    net.clear()
    ack = peer_seg(eng, seq=5001, flags=Flags.ACK, ack=1006, window=65535)
    sched.advance(0.2)
    eng.on_segment(ack.build())
    assert eng.rtt.srtt is not None      # un-retransmitted segment yields a sample
    assert abs(eng.rtt.srtt - 0.2) < 1e-6


def test_give_up_after_max_retransmits():
    errors = []
    eng, net, sched = make_client(iss=1000, rto_initial=1.0, rto_min=1.0, rto_max=4.0,
                                  max_retransmits=3)
    eng.on_error = errors.append
    client_to_established(eng, net, peer_iss=5000)
    eng.send(b"x")
    # never ack; advance well past 3 backed-off RTOs
    sched.advance(100.0)
    assert eng.state == S.CLOSED
    assert any("timeout" in e for e in errors)


def test_zero_window_persist_probe():
    eng, net, sched = make_client(iss=1000, persist_min=1.0, persist_max=10.0)
    # peer advertises a zero window in the SYN-ACK
    client_to_established(eng, net, peer_iss=5000, peer_win=0)
    eng.send(b"DATA")
    assert net.pop_all() == []          # cannot send into a zero window
    sched.advance(1.0)                  # persist timer fires -> 1-byte probe
    probe = net.pop_all()
    assert len(probe) == 1
    assert len(probe[0].payload) == 1   # exactly one probe byte
    assert probe[0].payload == b"D"
