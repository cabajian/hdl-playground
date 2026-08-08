from tcp_model import ConnState, Flags
from tcp_model.tests.unit.util import client_to_established, make_client, peer_seg

S = ConnState


def test_active_close_full_sequence():
    eng, net, sched = make_client(iss=1000, msl=1.0)
    client_to_established(eng, net, peer_iss=5000)
    eng.close()
    fin = net.last
    assert fin.fin and fin.ack_flag
    assert eng.state == S.FIN_WAIT_1
    fin_seq = fin.seq
    # peer acks our FIN -> FIN_WAIT_2
    eng.on_segment(peer_seg(eng, seq=5001, flags=Flags.ACK, ack=fin_seq + 1).build())
    assert eng.state == S.FIN_WAIT_2
    # peer sends its FIN -> we ack, enter TIME_WAIT
    eng.on_segment(peer_seg(eng, seq=5001, flags=Flags.FIN | Flags.ACK, ack=fin_seq + 1).build())
    assert eng.state == S.TIME_WAIT
    assert net.last.ack == 5002
    # 2*MSL elapses -> CLOSED
    sched.advance(2 * 1.0)
    assert eng.state == S.CLOSED


def test_passive_close():
    eng, net, sched = make_client(iss=1000)
    client_to_established(eng, net, peer_iss=5000)
    # peer closes first
    eng.on_segment(peer_seg(eng, seq=5001, flags=Flags.FIN | Flags.ACK, ack=1001).build())
    assert eng.state == S.CLOSE_WAIT
    assert eng.peer_fin
    assert net.last.ack == 5002        # acked their FIN
    # app closes
    eng.close()
    assert eng.state == S.LAST_ACK
    fin = net.last
    assert fin.fin
    # peer acks our FIN -> CLOSED
    eng.on_segment(peer_seg(eng, seq=5002, flags=Flags.ACK, ack=fin.seq + 1).build())
    assert eng.state == S.CLOSED


def test_simultaneous_close():
    eng, net, sched = make_client(iss=1000, msl=1.0)
    client_to_established(eng, net, peer_iss=5000)
    eng.close()
    fin = net.last
    assert eng.state == S.FIN_WAIT_1
    # peer's FIN arrives BEFORE acking ours -> CLOSING
    eng.on_segment(peer_seg(eng, seq=5001, flags=Flags.FIN | Flags.ACK, ack=fin.seq).build())
    assert eng.state == S.CLOSING
    # now peer acks our FIN -> TIME_WAIT
    eng.on_segment(peer_seg(eng, seq=5002, flags=Flags.ACK, ack=fin.seq + 1).build())
    assert eng.state == S.TIME_WAIT
    sched.advance(2 * 1.0)
    assert eng.state == S.CLOSED


def test_data_then_close_sends_fin_after_data():
    eng, net, sched = make_client(iss=1000)
    client_to_established(eng, net, peer_iss=5000)
    eng.send(b"bye")
    eng.close()
    segs = net.pop_all()
    # last segment is the FIN, sitting after the data
    assert segs[-1].fin
    assert segs[-1].seq == 1001 + 3   # after the 3 data bytes
    assert eng.state == S.FIN_WAIT_1


def test_rst_in_established_resets_connection():
    errors = []
    eng, net, sched = make_client(iss=1000)
    eng.on_error = errors.append
    client_to_established(eng, net, peer_iss=5000)
    rst = peer_seg(eng, seq=5001, flags=Flags.RST)  # exactly at rcv_nxt
    eng.on_segment(rst.build())
    assert eng.state == S.CLOSED
    assert any("reset" in e for e in errors)


def test_out_of_window_rst_is_challenged_not_honored():
    eng, net, sched = make_client(iss=1000)
    client_to_established(eng, net, peer_iss=5000)
    # RST with an in-window-but-not-rcv_nxt seq -> challenge ACK, no reset
    rst = peer_seg(eng, seq=5050, flags=Flags.RST, window=65535)
    eng.on_segment(rst.build())
    assert eng.state == S.ESTABLISHED      # not reset
    assert net.last.ack_flag and not net.last.rst   # challenge ACK


def test_user_abort_sends_rst():
    eng, net, sched = make_client(iss=1000)
    client_to_established(eng, net, peer_iss=5000)
    eng.abort()
    assert net.last.rst
    assert eng.state == S.CLOSED
