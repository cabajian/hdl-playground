"""Tests for the RFC-completion features:

  * user timeout (RFC 9293 3.8.3)
  * keepalive (RFC 9293 3.8.4)
  * Nagle / sender SWS avoidance (RFC 9293 3.7.4)
  * receiver SWS avoidance (RFC 9293 3.8.6.2.2)
  * SEND in LISTEN -> active-open conversion (RFC 9293 3.9.1 / FSM)
"""

from tcp_model import ConnState, Flags
from tcp_model.tests.unit.util import client_to_established, make_client, peer_seg

S = ConnState


# ---- user timeout (3.8.3) ----
def test_user_timeout_aborts_connection():
    errors = []
    eng, net, sched = make_client(
        iss=1000, rto_initial=1.0, rto_min=1.0, rto_max=2.0,
        max_retransmits=50, user_timeout=3.0,
    )
    eng.on_error = errors.append
    client_to_established(eng, net, peer_iss=5000)
    eng.send(b"x")
    sched.advance(10.0)     # RTOs at ~1s, 3s -> the 3s check trips the user timeout
    assert eng.state == S.CLOSED
    assert any("user timeout" in e for e in errors)


def test_user_timeout_reset_by_progress():
    errors = []
    eng, net, sched = make_client(
        iss=1000, rto_initial=1.0, rto_min=1.0, rto_max=1.0,
        max_retransmits=50, user_timeout=3.0,
    )
    eng.on_error = errors.append
    client_to_established(eng, net, peer_iss=5000)
    eng.send(b"ab")
    sched.advance(2.0)      # not yet timed out
    # partial progress: peer acks 1 byte -> the clock restarts
    eng.on_segment(peer_seg(eng, seq=5001, flags=Flags.ACK, ack=1002, window=65535).build())
    sched.advance(2.0)      # 2s since progress < 3s
    assert eng.state == S.ESTABLISHED
    assert not errors


def test_user_timeout_disabled_by_default():
    eng, net, sched = make_client(
        iss=1000, rto_initial=1.0, rto_min=1.0, rto_max=1.0, max_retransmits=4,
    )
    client_to_established(eng, net, peer_iss=5000)
    eng.send(b"x")
    sched.advance(3.0)
    # still retransmitting (max_retransmits governs, not a user timeout)
    assert eng.state == S.ESTABLISHED


# ---- keepalive (3.8.4) ----
def test_keepalive_disabled_by_default():
    eng, net, sched = make_client(iss=1000)
    client_to_established(eng, net, peer_iss=5000)
    fired = sched.advance(10000.0)
    assert fired == 0                   # no timers pending on an idle connection
    assert net.pop_all() == []


def test_keepalive_probe_format_and_reply():
    eng, net, sched = make_client(
        iss=1000, keepalive_idle=2.0, keepalive_interval=1.0, keepalive_count=3,
    )
    client_to_established(eng, net, peer_iss=5000)
    sched.advance(2.0)                  # idle expires -> probe
    probe = net.pop_all()[-1]
    assert probe.payload == b""
    assert probe.ack_flag
    snap = eng.get_tcb_snapshot()
    assert probe.seq == (snap["snd_una"] - 1) & 0xFFFFFFFF   # SND.UNA - 1
    # peer answers (the probe elicits a plain ACK) -> probes reset, no abort
    eng.on_segment(peer_seg(eng, seq=5001, flags=Flags.ACK, ack=1001, window=65535).build())
    sched.advance(1.5)                  # interval passes without a new *idle* expiry
    assert eng.state == S.ESTABLISHED


def test_keepalive_gives_up_after_count():
    errors = []
    eng, net, sched = make_client(
        iss=1000, keepalive_idle=2.0, keepalive_interval=1.0, keepalive_count=2,
    )
    eng.on_error = errors.append
    client_to_established(eng, net, peer_iss=5000)
    sched.advance(2.0)                  # probe 1
    sched.advance(1.0)                  # probe 2
    assert len([s for s in net.pop_all() if not s.payload]) == 2
    sched.advance(1.0)                  # count reached -> abort
    assert eng.state == S.CLOSED
    assert any("keepalive" in e for e in errors)


def test_keepalive_probe_elicits_ack_from_peer_side():
    # the probe (seq = rcv_nxt - 1, len 0) must be *unacceptable* to a receiver,
    # which therefore re-ACKs: verify our own engine responds to one that way
    eng, net, sched = make_client(iss=1000)
    client_to_established(eng, net, peer_iss=5000)
    probe = peer_seg(eng, seq=5000, flags=Flags.ACK, ack=1001)   # rcv_nxt-1, no data
    eng.on_segment(probe.build())
    reply = net.last
    assert reply.ack_flag and reply.ack == 5001   # duplicate ACK, no state change
    assert eng.state == S.ESTABLISHED


# ---- Nagle (3.7.4) ----
def test_nagle_holds_small_segment_with_data_in_flight():
    eng, net, sched = make_client(iss=1000, nagle=True, rto_initial=10.0)
    client_to_established(eng, net, peer_iss=5000)
    eng.send(b"aa")                     # nothing outstanding -> goes immediately
    first = net.pop_all()
    assert any(s.payload == b"aa" for s in first)
    eng.send(b"bb")                     # sub-MSS with data in flight -> held
    assert net.pop_all() == []
    assert eng.get_tcb_snapshot()["send_buffered"] == 2
    # ACK arrives -> held data flushes
    eng.on_segment(peer_seg(eng, seq=5001, flags=Flags.ACK, ack=1003, window=65535).build())
    flushed = net.pop_all()
    assert any(s.payload == b"bb" for s in flushed)


def test_nagle_off_by_default_sends_immediately():
    eng, net, sched = make_client(iss=1000, rto_initial=10.0)
    client_to_established(eng, net, peer_iss=5000)
    eng.send(b"aa")
    eng.send(b"bb")
    payloads = [s.payload for s in net.pop_all() if s.payload]
    assert payloads == [b"aa", b"bb"]   # both emitted without waiting


def test_nagle_full_mss_segments_not_held():
    eng, net, sched = make_client(iss=1000, nagle=True, rto_initial=10.0)
    eng._cfg_mss = 4
    eng.tcb.snd_mss = 4
    client_to_established(eng, net, peer_iss=5000)
    eng.tcb.snd_mss = 4
    eng.send(b"ABCDEFGH")               # two full-MSS chunks -> both go
    payloads = [s.payload for s in net.pop_all() if s.payload]
    assert payloads == [b"ABCD", b"EFGH"]


# ---- receiver SWS avoidance (3.8.6.2.2) ----
def test_sws_holds_window_update_below_threshold():
    # rcv_wnd_max=8 -> threshold = min(MSS, 8//2) = 4
    eng, net, sched = make_client(iss=1000, rcv_wnd=8)
    client_to_established(eng, net, peer_iss=5000)
    eng.on_segment(
        peer_seg(eng, seq=5001, flags=Flags.ACK, ack=1001, payload=b"12345678").build()
    )
    assert net.last.window == 0         # buffer full -> zero window advertised
    net.clear()
    assert eng.recv(1) == b"1"          # 1 byte drained: advance < threshold
    assert net.pop_all() == []          # no window update yet (SWS hold)
    assert eng.recv(3) == b"234"        # total 4 drained: advance >= threshold
    upd = net.pop_all()
    assert len(upd) == 1
    assert upd[0].window == 4           # right edge advanced by the full 4


def test_sws_disabled_updates_immediately():
    eng, net, sched = make_client(iss=1000, rcv_wnd=8, sws_avoidance=False)
    client_to_established(eng, net, peer_iss=5000)
    eng.on_segment(
        peer_seg(eng, seq=5001, flags=Flags.ACK, ack=1001, payload=b"12345678").build()
    )
    net.clear()
    eng.recv(1)
    upd = net.pop_all()
    assert len(upd) == 1 and upd[0].window == 1   # tiny update allowed when off


# ---- SEND in LISTEN (3.9.1) ----
def test_send_in_listen_converts_to_active_open():
    eng, net, sched = make_client(iss=1000)   # remote_port preset by helper
    eng.passive_open()
    assert eng.state == S.LISTEN
    eng.send(b"hi")
    assert eng.state == S.SYN_SENT            # LISTEN --SEND--> SYN-SENT
    syn = net.pop_all()[0]
    assert syn.syn
    assert eng.get_tcb_snapshot()["send_buffered"] == 2
    # handshake completes -> queued data flushes
    eng.on_segment(
        peer_seg(eng, seq=5000, flags=Flags.SYN | Flags.ACK, ack=syn.seq + 1).build()
    )
    assert eng.state == S.ESTABLISHED
    sent = [s.payload for s in net.pop_all() if s.payload]
    assert sent == [b"hi"]


def test_send_in_listen_without_remote_raises():
    eng, net, sched = make_client(iss=1000, peer_port=0)   # no foreign socket
    eng.passive_open()
    try:
        eng.send(b"hi")
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "foreign socket" in str(e)
    assert eng.state == S.LISTEN
