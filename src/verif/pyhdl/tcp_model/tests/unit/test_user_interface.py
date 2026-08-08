"""Verify the engine's user-facing API matches the RFC 9293 3.9.1 service calls:
OPEN, SEND, RECEIVE, CLOSE, ABORT, STATUS.
"""

from tcp_model import ConnState, Flags, ReceiveResult, TcpEngine
from tcp_model.tests.unit.util import client_to_established, make_client, peer_seg

S = ConnState

RFC_USER_CALLS = ["open", "send", "receive", "close", "abort", "status"]


def test_all_rfc_user_calls_exist():
    for name in RFC_USER_CALLS:
        assert callable(getattr(TcpEngine, name)), f"missing RFC user call: {name}"


# ---- OPEN ----
def test_open_active_returns_connection_name():
    eng, net, sched = make_client(iss=1000)
    name = eng.open(active=True)
    assert eng.state == S.SYN_SENT
    assert isinstance(name, str) and name
    assert net.last.syn


def test_open_passive_listens():
    eng, net, sched = make_client(iss=1000)
    name = eng.open(active=False)
    assert eng.state == S.LISTEN
    assert name == eng.connection_name


def test_open_can_set_sockets():
    eng, net, sched = make_client(iss=1000)
    eng.open(active=True, remote_port=443, local_port=51000)
    assert eng.remote_port == 443
    assert eng.local_port == 51000
    assert net.last.dst_port == 443
    assert net.last.src_port == 51000


# ---- SEND (PUSH / URGENT flags) ----
def test_send_push_sets_psh():
    eng, net, sched = make_client(iss=1000)
    client_to_established(eng, net, peer_iss=5000)
    eng.send(b"hello", push=True)
    assert net.last.flags & Flags.PSH


def test_send_no_push_clears_psh():
    eng, net, sched = make_client(iss=1000)
    client_to_established(eng, net, peer_iss=5000)
    eng.send(b"hello", push=False)
    assert not (net.last.flags & Flags.PSH)


def test_send_urgent_sets_urg_and_pointer():
    eng, net, sched = make_client(iss=1000)
    client_to_established(eng, net, peer_iss=5000)
    eng.send(b"hello", urgent=True)
    seg = net.last
    assert seg.flags & Flags.URG
    assert seg.urgent_ptr == 5      # points past the 5 urgent bytes


# ---- RECEIVE (returns data + PUSH + URGENT) ----
def test_receive_returns_namedtuple():
    eng, net, sched = make_client(iss=1000)
    client_to_established(eng, net, peer_iss=5000)
    eng.on_segment(peer_seg(eng, seq=5001, flags=Flags.ACK, ack=1001, payload=b"abc").build())
    r = eng.receive()
    assert isinstance(r, ReceiveResult)
    assert r.data == b"abc"
    assert r.push is False and r.urgent is False


def test_receive_reports_push():
    eng, net, sched = make_client(iss=1000)
    client_to_established(eng, net, peer_iss=5000)
    eng.on_segment(
        peer_seg(eng, seq=5001, flags=Flags.ACK | Flags.PSH, ack=1001, payload=b"hi").build()
    )
    r = eng.receive()
    assert r.data == b"hi"
    assert r.push is True


def test_receive_reports_urgent():
    eng, net, sched = make_client(iss=1000)
    client_to_established(eng, net, peer_iss=5000)
    seg = peer_seg(eng, seq=5001, flags=Flags.ACK | Flags.URG, ack=1001, payload=b"!")
    seg.urgent_ptr = 1
    eng.on_segment(seg.build())
    r = eng.receive()
    assert r.urgent is True


def test_recv_convenience_returns_bytes():
    eng, net, sched = make_client(iss=1000)
    client_to_established(eng, net, peer_iss=5000)
    eng.on_segment(peer_seg(eng, seq=5001, flags=Flags.ACK, ack=1001, payload=b"xy").build())
    assert eng.recv() == b"xy"   # bytes, not a ReceiveResult


# ---- STATUS ----
def test_status_block_fields():
    eng, net, sched = make_client(iss=1000)
    client_to_established(eng, net, peer_iss=5000)
    st = eng.status()
    # the RFC status-data fields
    for key in [
        "local_connection_name", "local_socket", "remote_socket",
        "connection_state", "send_window", "receive_window",
        "buffers_awaiting_ack", "buffers_pending_receipt",
        "urgent_state", "transmission_timeout",
    ]:
        assert key in st, f"STATUS missing field: {key}"
    assert st["connection_state"] == "ESTABLISHED"
    assert st["local_socket"] == ("", eng.local_port)
    assert st["remote_socket"] == ("", eng.remote_port)


def test_status_tracks_unacked_send():
    eng, net, sched = make_client(iss=1000, rto_initial=10.0)
    client_to_established(eng, net, peer_iss=5000)
    eng.send(b"hello")
    st = eng.status()
    assert st["buffers_awaiting_ack"] == 1
    assert st["bytes_awaiting_ack"] == 5
