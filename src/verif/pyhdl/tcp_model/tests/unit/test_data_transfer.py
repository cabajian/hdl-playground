from tcp_model import ConnState, Flags
from tcp_model.tests.unit.util import client_to_established, make_client, peer_seg

S = ConnState


def test_send_emits_data_segment():
    eng, net, sched = make_client(iss=1000)
    client_to_established(eng, net, peer_iss=5000)
    eng.send(b"hello")
    seg = net.last
    assert seg.payload == b"hello"
    assert seg.seq == 1001
    assert seg.ack_flag and seg.flags & Flags.PSH  # PSH on buffer-emptying segment
    assert eng.get_tcb_snapshot()["snd_nxt"] == 1006


def test_mss_segmentation():
    eng, net, sched = make_client(iss=1000)
    eng._cfg_mss = 4
    eng.tcb.snd_mss = 4
    client_to_established(eng, net, peer_iss=5000)
    eng.tcb.snd_mss = 4  # ensure handshake didn't widen it
    eng.send(b"ABCDEFGHIJ")  # 10 bytes -> 4 + 4 + 2
    segs = net.pop_all()
    payloads = [s.payload for s in segs if s.payload]
    assert payloads == [b"ABCD", b"EFGH", b"IJ"]
    # only the final segment carries PSH
    assert segs[-1].flags & Flags.PSH
    assert not (segs[0].flags & Flags.PSH)


def test_flow_control_respects_window():
    eng, net, sched = make_client(iss=1000)
    client_to_established(eng, net, peer_iss=5000, peer_win=4)
    eng.send(b"ABCDEFGH")        # 8 bytes, window only 4
    segs = net.pop_all()
    sent = b"".join(s.payload for s in segs)
    assert sent == b"ABCD"       # only a window's worth
    assert eng.get_tcb_snapshot()["send_buffered"] == 4
    # peer acks 4 bytes and opens the window
    ack = peer_seg(eng, seq=5001, flags=Flags.ACK, ack=1005, window=65535)
    eng.on_segment(ack.build())
    segs = net.pop_all()
    sent2 = b"".join(s.payload for s in segs)
    assert sent2 == b"EFGH"      # remainder flushed when window opened


def test_recv_in_order():
    eng, net, sched = make_client(iss=1000)
    client_to_established(eng, net, peer_iss=5000)
    data = peer_seg(eng, seq=5001, flags=Flags.ACK | Flags.PSH, ack=1001, payload=b"world")
    eng.on_segment(data.build())
    assert eng.recv() == b"world"
    assert eng.get_tcb_snapshot()["rcv_nxt"] == 5006
    ack = net.last
    assert ack.ack_flag and ack.ack == 5006


def test_recv_out_of_order_then_fill_gap():
    eng, net, sched = make_client(iss=1000)
    client_to_established(eng, net, peer_iss=5000)
    # send the SECOND segment first (gap at 5001..5005)
    seg2 = peer_seg(eng, seq=5006, flags=Flags.ACK, ack=1001, payload=b"WORLD")
    eng.on_segment(seg2.build())
    assert eng.recv() == b""              # nothing deliverable yet
    dup = net.last
    assert dup.ack == 5001                # duplicate ACK still points at the gap
    # now the missing first segment arrives
    seg1 = peer_seg(eng, seq=5001, flags=Flags.ACK, ack=1001, payload=b"hello")
    eng.on_segment(seg1.build())
    assert eng.recv() == b"helloWORLD"    # reassembled in order
    assert eng.get_tcb_snapshot()["rcv_nxt"] == 5011


def test_duplicate_old_data_is_reacked_not_redelivered():
    eng, net, sched = make_client(iss=1000)
    client_to_established(eng, net, peer_iss=5000)
    seg = peer_seg(eng, seq=5001, flags=Flags.ACK, ack=1001, payload=b"hello")
    eng.on_segment(seg.build())
    assert eng.recv() == b"hello"
    # retransmit the same (already-received) segment
    eng.on_segment(seg.build())
    assert eng.recv() == b""              # not delivered twice
    assert net.last.ack == 5006           # re-ACK


def test_streaming_on_data_callback_keeps_window_open():
    sink = []
    eng, net, sched = make_client(iss=1000)
    eng.on_data = sink.append
    client_to_established(eng, net, peer_iss=5000)
    eng.on_segment(peer_seg(eng, seq=5001, flags=Flags.ACK, ack=1001, payload=b"abc").build())
    eng.on_segment(peer_seg(eng, seq=5004, flags=Flags.ACK, ack=1001, payload=b"def").build())
    assert b"".join(sink) == b"abcdef"
    # Buffer is empty (streamed out), so the window is still wide open. With
    # receiver SWS avoidance the advertised right edge is *held* rather than
    # advanced by tiny increments, so the window reads max-6 until the edge can
    # advance by >= min(MSS, max/2).
    assert net.last.window == eng.rcv_wnd_max - 6
    assert eng.get_tcb_snapshot()["recv_buffered"] == 0
