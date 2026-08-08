"""Every transition of the EFSM paper (Zaghal & Khan TR2005-07-22), fig-cited.

Where the paper (RFC 793-era) and RFC 9293 disagree, these tests assert the
RFC 9293 behavior and name the divergence; the full catalog is
docs/DOCUMENTATION.md section 9.
"""

import pytest

from tcp_model import ConnState as S, Flags
from .util import (
    CLIENT_PORT, SERVER_PORT, enter, make_client, make_server, peer_seg,
    client_to_established,
)


# ---------------------------------------------------------------- CLOSED
def test_closed_active_open_sends_syn():            # [EFSM-1]
    eng, net, _ = make_client()
    eng.open(active=True)
    syn = net.pop_all()[0]
    assert eng.state == S.SYN_SENT and syn.syn and not syn.ack_flag
    assert syn.seq == 1000 and eng.tcb.snd_nxt == 1001


def test_closed_passive_open_listens():             # [EFSM-1]
    eng, _, _ = make_server()
    eng.open(active=False)
    assert eng.state == S.LISTEN


def test_closed_user_calls_error():                 # [EFSM-2] / 3.10.2-3.10.5
    eng, _, _ = make_client()
    for call in (lambda: eng.send(b"x"), eng.receive, eng.close, eng.abort):
        with pytest.raises(RuntimeError, match="does not exist"):
            call()


def test_closed_segment_with_ack_gets_rst():        # [EFSM-2] / 3.10.7.1
    eng, net, _ = make_client()
    eng.on_segment(peer_seg(eng, seq=7000, flags=Flags.ACK, ack=4321).build())
    r = net.pop_all()[0]
    assert r.rst and r.seq == 4321 and not r.ack_flag


def test_closed_segment_without_ack_gets_rst_ack(): # 3.10.7.1 (paper differs)
    eng, net, _ = make_client()
    eng.on_segment(peer_seg(eng, seq=7000, flags=Flags.SYN, payload=b"ab").build())
    r = net.pop_all()[0]
    # RFC: <SEQ=0><ACK=SEG.SEQ+SEG.LEN><CTL=RST,ACK>; SEG.LEN counts the SYN
    assert r.rst and r.ack_flag and r.seq == 0 and r.ack == 7000 + 2 + 1


def test_closed_rst_ignored():
    eng, net, _ = make_client()
    eng.on_segment(peer_seg(eng, seq=7000, flags=Flags.RST).build())
    assert net.pop_all() == [] and eng.state == S.CLOSED


# ---------------------------------------------------------------- LISTEN
def test_listen_open_converts_to_active():          # [EFSM-3] / 3.10.1
    eng, net, _ = make_server()
    eng.passive_open()
    eng.open(active=True, remote_port=CLIENT_PORT)
    assert eng.state == S.SYN_SENT and net.pop_all()[0].syn


def test_listen_send_converts_and_queues():         # [EFSM-3] / 3.10.2
    eng, net, sched = make_server()
    eng.remote_port = CLIENT_PORT                   # foreign socket specified
    eng.passive_open()
    eng.send(b"hello")
    segs = net.pop_all()
    assert eng.state == S.SYN_SENT and len(segs) == 1 and segs[0].syn
    synack = peer_seg(eng, seq=9000, flags=Flags.SYN | Flags.ACK,
                      ack=segs[0].seq + 1)
    eng.on_segment(synack.build())
    out = net.pop_all()
    assert eng.state == S.ESTABLISHED
    assert any(s.payload == b"hello" for s in out)  # queued data flushed


def test_listen_send_without_foreign_socket_errors():   # [EFSM-3]
    eng, _, _ = make_server()
    eng.passive_open()
    with pytest.raises(RuntimeError, match="foreign socket"):
        eng.send(b"x")


def test_listen_close_and_abort_delete_tcb():       # [EFSM-4] / 3.10.4-3.10.5
    for call in ("close", "abort"):
        eng, net, _ = make_server()
        eng.passive_open()
        getattr(eng, call)()
        assert eng.state == S.CLOSED
        assert net.pop_all() == []                  # no RST from LISTEN


def test_listen_rst_ignored():                      # [EFSM-5]
    eng, net, _ = make_server()
    eng.passive_open()
    eng.on_segment(peer_seg(eng, seq=1000, flags=Flags.RST).build())
    assert net.pop_all() == [] and eng.state == S.LISTEN


def test_listen_ack_gets_rst():                     # [EFSM-5] / 3.10.7.2
    eng, net, _ = make_server()
    eng.passive_open()
    eng.on_segment(peer_seg(eng, seq=1000, flags=Flags.ACK, ack=2222).build())
    r = net.pop_all()[0]
    assert r.rst and r.seq == 2222 and eng.state == S.LISTEN


def test_listen_syn_to_syn_rcvd_fields():           # [EFSM-5]
    eng, net, _ = make_server(iss=5000)
    eng.passive_open()
    eng.on_segment(peer_seg(eng, seq=1000, flags=Flags.SYN).build())
    sa = net.pop_all()[0]
    assert eng.state == S.SYN_RECEIVED
    assert sa.syn and sa.ack_flag and sa.seq == 5000 and sa.ack == 1001
    assert eng.tcb.rcv_nxt == 1001 and eng.tcb.irs == 1000
    assert eng.tcb.snd_una == 5000 and eng.tcb.snd_nxt == 5001


# ---------------------------------------------------------------- SYN-SENT
def test_syn_sent_open_already_exists():            # [EFSM-6] / 3.10.1
    eng, net, sched, _ = enter("SYN_SENT")
    with pytest.raises(RuntimeError, match="already exists"):
        eng.active_open()


def test_syn_sent_send_queued_until_established():  # [EFSM-6] / 3.10.2
    eng, net, sched, _ = enter("SYN_SENT")
    eng.send(b"early")
    assert net.pop_all() == []                      # nothing until ESTABLISHED
    eng.on_segment(peer_seg(eng, seq=5000, flags=Flags.SYN | Flags.ACK,
                            ack=1001).build())
    out = net.pop_all()
    assert eng.state == S.ESTABLISHED
    assert any(s.payload == b"early" for s in out)


def test_syn_sent_close_deletes_tcb():              # [EFSM-6] / 3.10.4
    eng, net, sched, _ = enter("SYN_SENT")
    eng.close()
    assert eng.state == S.CLOSED


def test_syn_sent_abort_no_rst():                   # 3.10.5 (not synchronized)
    eng, net, sched, _ = enter("SYN_SENT")
    eng.abort()
    assert eng.state == S.CLOSED and net.pop_all() == []


def test_syn_sent_bad_ack_gets_rst():               # [EFSM-7] / 3.10.7.3
    eng, net, sched, _ = enter("SYN_SENT")
    # SEG.ACK =< ISS
    eng.on_segment(peer_seg(eng, seq=5000, flags=Flags.ACK, ack=1000).build())
    r = net.pop_all()[0]
    assert r.rst and r.seq == 1000 and eng.state == S.SYN_SENT


def test_syn_sent_bad_ack_with_rst_dropped():       # 3.10.7.3 "unless RST"
    eng, net, sched, _ = enter("SYN_SENT")
    eng.on_segment(peer_seg(eng, seq=5000, flags=Flags.RST | Flags.ACK,
                            ack=9999).build())
    assert net.pop_all() == [] and eng.state == S.SYN_SENT


def test_syn_sent_rst_with_acceptable_ack_resets(): # [EFSM-7]
    errors = []
    eng, net, sched = make_client()
    eng.on_error = errors.append
    eng.active_open()
    net.clear()
    eng.on_segment(peer_seg(eng, seq=5000, flags=Flags.RST | Flags.ACK,
                            ack=1001).build())
    assert eng.state == S.CLOSED and errors == ["connection refused"]


def test_syn_sent_rst_without_ack_dropped():        # [EFSM-7] / 3.10.7.3
    eng, net, sched, _ = enter("SYN_SENT")
    eng.on_segment(peer_seg(eng, seq=5000, flags=Flags.RST).build())
    assert eng.state == S.SYN_SENT


def test_syn_sent_synack_establishes_and_acks():    # [EFSM-7]
    eng, net, sched, _ = enter("SYN_SENT")
    eng.on_segment(peer_seg(eng, seq=5000, flags=Flags.SYN | Flags.ACK,
                            ack=1001).build())
    ack = net.pop_all()[0]
    assert eng.state == S.ESTABLISHED
    assert ack.ack_flag and not ack.syn and ack.seq == 1001 and ack.ack == 5001


def test_syn_sent_syn_only_simultaneous_open():     # [EFSM-7] simultaneous open
    eng, net, sched, _ = enter("SYN_SENT")
    eng.on_segment(peer_seg(eng, seq=5000, flags=Flags.SYN).build())
    sa = net.pop_all()[-1]
    assert eng.state == S.SYN_RECEIVED
    assert sa.syn and sa.ack_flag and sa.seq == 1000 and sa.ack == 5001


# ---------------------------------------------------------------- SYN-RCVD
def _to_syn_rcvd(iss=5000, peer_iss=1000):
    eng, net, sched = make_server(iss=iss)
    eng.passive_open()
    eng.on_segment(peer_seg(eng, seq=peer_iss, flags=Flags.SYN).build())
    net.clear()
    return eng, net, sched


def test_syn_rcvd_close_sends_fin_to_fin_wait_1():  # [EFSM-8] / 3.10.4
    eng, net, _ = _to_syn_rcvd()
    eng.close()
    fin = net.pop_all()[0]
    assert fin.fin and eng.state == S.FIN_WAIT_1


def test_syn_rcvd_close_with_queued_data_waits():   # [EFSM-8]: FIN after SENDs
    eng, net, _ = _to_syn_rcvd()
    eng.send(b"queued")
    eng.close()
    assert net.pop_all() == []                      # nothing until ESTABLISHED
    assert eng.state == S.SYN_RECEIVED
    eng.on_segment(peer_seg(eng, seq=1001, flags=Flags.ACK, ack=5001).build())
    out = net.pop_all()
    assert [s.payload for s in out if s.payload] == [b"queued"]
    assert out[-1].fin and eng.state == S.FIN_WAIT_1


def test_syn_rcvd_abort_rst_closed():               # [EFSM-8] / 3.10.5
    eng, net, _ = _to_syn_rcvd()
    eng.abort()
    r = net.pop_all()[0]
    assert r.rst and r.seq == 5001 and eng.state == S.CLOSED


def test_syn_rcvd_rst_passive_returns_to_listen():  # [EFSM-9] / 3.10.7.4
    eng, net, _ = _to_syn_rcvd()
    eng.on_segment(peer_seg(eng, seq=1001, flags=Flags.RST).build())
    assert eng.state == S.LISTEN and net.pop_all() == []
    # the listener accepts a fresh handshake afterwards, from any peer
    eng.on_segment(
        peer_seg(eng, seq=42_000, flags=Flags.SYN).build()
    )
    assert eng.state == S.SYN_RECEIVED and net.pop_all()[0].syn


def test_syn_rcvd_rst_active_open_refused():        # [EFSM-9]: simultaneous-open origin
    errors = []
    eng, net, sched, _ = enter("SYN_SENT")
    eng.on_error = errors.append
    eng.on_segment(peer_seg(eng, seq=5000, flags=Flags.SYN).build())  # -> SYN_RCVD
    net.clear()
    eng.on_segment(peer_seg(eng, seq=5001, flags=Flags.RST).build())
    assert eng.state == S.CLOSED and errors == ["connection refused"]


def test_syn_rcvd_unacceptable_segment_gets_dup_ack():   # [EFSM-9] first check
    eng, net, _ = _to_syn_rcvd()
    eng.on_segment(peer_seg(eng, seq=999, flags=Flags.ACK, ack=5001,
                            payload=b"x").build())
    a = net.pop_all()[0]
    assert a.ack_flag and a.ack == 1001 and eng.state == S.SYN_RECEIVED


def test_syn_rcvd_valid_ack_establishes():          # [EFSM-10]
    eng, net, _ = _to_syn_rcvd()
    eng.on_segment(peer_seg(eng, seq=1001, flags=Flags.ACK, ack=5001).build())
    assert eng.state == S.ESTABLISHED


def test_syn_rcvd_invalid_ack_rst_and_stays():      # 3.10.7.4 ([EFSM-10] closes: sec. 9)
    eng, net, _ = _to_syn_rcvd()
    eng.on_segment(peer_seg(eng, seq=1001, flags=Flags.ACK, ack=7777).build())
    r = net.pop_all()[0]
    assert r.rst and r.seq == 7777
    assert eng.state == S.SYN_RECEIVED              # RFC remains; paper tears down


def test_syn_rcvd_ack_with_fin_lands_close_wait():  # [EFSM-10] continue processing
    eng, net, _ = _to_syn_rcvd()
    eng.on_segment(peer_seg(eng, seq=1001, flags=Flags.ACK | Flags.FIN,
                            ack=5001).build())
    assert eng.state == S.CLOSE_WAIT and eng.peer_fin


# ---------------------------------------------------------------- ESTABLISHED
def test_established_open_already_exists():         # [EFSM-11] / 3.10.1
    eng, net, sched, _ = enter("ESTABLISHED")
    with pytest.raises(RuntimeError, match="already exists"):
        eng.active_open()


def test_established_close_flushes_data_then_fin(): # [EFSM-12]: CLOSE after SENDs
    eng, net, sched, _ = enter("ESTABLISHED")
    eng.send(b"tail")
    eng.close()
    out = net.pop_all()
    assert out[0].payload == b"tail" and out[-1].fin
    assert eng.state == S.FIN_WAIT_1


def test_established_abort_rst():                   # [EFSM-12] / 3.10.5
    eng, net, sched, _ = enter("ESTABLISHED")
    eng.abort()
    r = net.pop_all()[0]
    assert r.rst and r.seq == 1001 and eng.state == S.CLOSED


def test_established_fin_to_close_wait():           # [EFSM-15]/[EFSM-38]
    eng, net, sched, ctx = enter("ESTABLISHED")
    eng.on_segment(peer_seg(eng, seq=5001, flags=Flags.FIN | Flags.ACK,
                            ack=1001).build())
    a = net.pop_all()[-1]
    assert eng.state == S.CLOSE_WAIT and a.ack == 5002   # FIN consumed one seq


# ---------------------------------------------------------------- FIN-WAIT-1
def test_fin_wait_1_send_and_close_error():         # [EFSM-18/19] / 3.10.2/3.10.4
    eng, net, sched, _ = enter("FIN_WAIT_1")
    with pytest.raises(RuntimeError, match="closing"):
        eng.send(b"x")
    with pytest.raises(RuntimeError, match="closing"):
        eng.close()


def test_fin_wait_1_receive_still_works():          # [EFSM-18]
    eng, net, sched, _ = enter("FIN_WAIT_1")
    eng.on_segment(peer_seg(eng, seq=5001, flags=Flags.ACK, ack=1001,
                            payload=b"late").build())
    assert eng.receive().data == b"late"


def test_fin_wait_1_ack_of_fin_to_fin_wait_2():     # [EFSM-20/21]
    eng, net, sched, _ = enter("FIN_WAIT_1")
    eng.on_segment(peer_seg(eng, seq=5001, flags=Flags.ACK, ack=1002).build())
    assert eng.state == S.FIN_WAIT_2


def test_fin_wait_1_fin_to_closing():               # [EFSM-21] simultaneous close
    eng, net, sched, _ = enter("FIN_WAIT_1")
    eng.on_segment(peer_seg(eng, seq=5001, flags=Flags.FIN | Flags.ACK,
                            ack=1001).build())
    assert eng.state == S.CLOSING


def test_fin_wait_1_fin_plus_ack_direct_time_wait():   # [EFSM-21] recv FIN,ACK
    eng, net, sched, _ = enter("FIN_WAIT_1")
    eng.on_segment(peer_seg(eng, seq=5001, flags=Flags.FIN | Flags.ACK,
                            ack=1002).build())
    assert eng.state == S.TIME_WAIT


# ---------------------------------------------------------------- FIN-WAIT-2
def test_fin_wait_2_send_close_error_receive_ok():  # [EFSM-22/23]
    eng, net, sched, _ = enter("FIN_WAIT_2")
    with pytest.raises(RuntimeError, match="closing"):
        eng.send(b"x")
    with pytest.raises(RuntimeError, match="closing"):
        eng.close()
    eng.on_segment(peer_seg(eng, seq=5001, flags=Flags.ACK, ack=1002,
                            payload=b"trail").build())
    assert eng.receive().data == b"trail"           # half-close: 3.6.1


def test_fin_wait_2_fin_to_time_wait():             # [EFSM-25]
    eng, net, sched, _ = enter("FIN_WAIT_2")
    eng.on_segment(peer_seg(eng, seq=5001, flags=Flags.FIN | Flags.ACK,
                            ack=1002).build())
    a = net.pop_all()[-1]
    assert eng.state == S.TIME_WAIT and a.ack == 5002


# ---------------------------------------------------------------- CLOSING
def test_closing_user_calls_error():                # [EFSM-26]
    eng, net, sched, _ = enter("CLOSING")
    for call in (lambda: eng.send(b"x"), eng.receive, eng.close):
        with pytest.raises(RuntimeError, match="closing"):
            call()


def test_closing_ack_of_fin_to_time_wait():         # [EFSM-27]
    eng, net, sched, _ = enter("CLOSING")
    eng.on_segment(peer_seg(eng, seq=5002, flags=Flags.ACK, ack=1002).build())
    assert eng.state == S.TIME_WAIT


def test_closing_stays_without_fin_ack():           # [EFSM-27]
    eng, net, sched, _ = enter("CLOSING")
    eng.on_segment(peer_seg(eng, seq=5002, flags=Flags.ACK, ack=1001).build())
    assert eng.state == S.CLOSING


# ---------------------------------------------------------------- TIME-WAIT
def test_time_wait_user_calls_error_abort_ok():     # [EFSM-28]
    eng, net, sched, _ = enter("TIME_WAIT")
    for call in (lambda: eng.send(b"x"), eng.receive, eng.close):
        with pytest.raises(RuntimeError, match="closing"):
            call()
    eng.abort()                                     # OK; no RST from TIME-WAIT
    assert eng.state == S.CLOSED and net.pop_all() == []


def test_time_wait_fin_retransmit_reack_and_restart():   # [EFSM-29]
    eng, net, sched, _ = enter("TIME_WAIT", msl=1.0)
    sched.advance(1.9)                              # 2MSL = 2.0 not yet reached
    eng.on_segment(peer_seg(eng, seq=5001, flags=Flags.FIN | Flags.ACK,
                            ack=1002).build())      # retransmitted FIN
    assert net.pop_all()[-1].ack == 5002            # re-acknowledged
    sched.advance(1.9)                              # old deadline passes: restarted
    assert eng.state == S.TIME_WAIT
    sched.advance(0.2)
    assert eng.state == S.CLOSED                    # [EFSM-29] 2MSL timeout


def test_time_wait_2msl_expiry():                   # [EFSM-29] / [EFSM-0]
    eng, net, sched, _ = enter("TIME_WAIT", msl=1.0)
    sched.advance(2.0)
    assert eng.state == S.CLOSED


# ---------------------------------------------------------------- CLOSE-WAIT
def test_close_wait_send_still_allowed():           # [EFSM-30] / 3.6.1
    eng, net, sched, _ = enter("CLOSE_WAIT")
    eng.send(b"still-open")
    assert net.pop_all()[0].payload == b"still-open"


def test_close_wait_receive_drains_then_errors():   # [EFSM-31] / 3.10.3
    eng, net, sched, _ = enter("ESTABLISHED")
    eng.on_segment(peer_seg(eng, seq=5001, flags=Flags.ACK, ack=1001,
                            payload=b"leftover").build())
    eng.on_segment(peer_seg(eng, seq=5009, flags=Flags.FIN | Flags.ACK,
                            ack=1001).build())
    assert eng.state == S.CLOSE_WAIT
    assert eng.receive().data == b"leftover"
    with pytest.raises(RuntimeError, match="closing"):
        eng.receive()


def test_close_wait_close_fin_to_last_ack():        # [EFSM-31]
    eng, net, sched, _ = enter("CLOSE_WAIT")
    eng.close()
    fin = net.pop_all()[-1]
    assert fin.fin and eng.state == S.LAST_ACK


def test_close_wait_fin_retransmit_reacked():       # [EFSM-33] FIN reprocessing
    eng, net, sched, _ = enter("CLOSE_WAIT")
    eng.on_segment(peer_seg(eng, seq=5001, flags=Flags.FIN | Flags.ACK,
                            ack=1001).build())      # dup of the consumed FIN
    a = net.pop_all()[-1]
    assert a.ack_flag and a.ack == 5002 and eng.state == S.CLOSE_WAIT


# ---------------------------------------------------------------- LAST-ACK
def test_last_ack_user_calls_error():               # [EFSM-34]
    eng, net, sched, _ = enter("LAST_ACK")
    for call in (lambda: eng.send(b"x"), eng.receive, eng.close):
        with pytest.raises(RuntimeError, match="closing"):
            call()


def test_last_ack_fin_retransmitted_on_rexmt():     # [EFSM-34] REXMT TIMEOUT
    eng, net, sched, _ = enter("LAST_ACK", rto_initial=1.0, rto_min=1.0)
    sched.advance(1.0)
    fin = net.pop_all()[-1]
    assert fin.fin and fin.seq == 1001


def test_last_ack_ack_of_fin_closes():              # [EFSM-35] / [EFSM-0]
    eng, net, sched, _ = enter("LAST_ACK")
    eng.on_segment(peer_seg(eng, seq=5002, flags=Flags.ACK, ack=1002).build())
    assert eng.state == S.CLOSED


# ------------------------------------------------------- USER-TIME TIMEOUT
def test_user_timeout_aborts_any_state():           # [EFSM-36] / 3.8.3
    errors = []
    eng, net, sched, _ = enter("ESTABLISHED", user_timeout=5.0,
                               rto_initial=1.0, rto_min=1.0, rto_max=2.0)
    eng.on_error = errors.append
    eng.send(b"never-acked")
    net.clear()
    sched.advance(10.0)
    assert eng.state == S.CLOSED and "user timeout" in errors
    assert any(s.rst for s in net.pop_all())        # was synchronized: RST out


# ------------------------------------------------------- acceptability macro
@pytest.mark.parametrize(
    "wnd,payload,seq_off,ok,deliver",
    [
        (65535, b"",   0, True,  b""),    # len0/wnd>0: RCV.NXT <= SEQ < NXT+WND
        (65535, b"",  -1, False, b""),
        (65535, b"d",  0, True,  b"d"),   # len>0/wnd>0: head in window
        (65535, b"dd", -1, True,  b"d"),  # tail overlaps: trimmed, 2nd byte lands
        (65535, b"d", -1, False, b""),    # fully below RCV.NXT: pure duplicate
        (0,     b"",   0, True,  b""),    # len0/wnd0: SEQ == RCV.NXT
        (0,     b"",   1, False, b""),
        (0,     b"d",  0, False, b""),    # len>0/wnd0: never acceptable
    ],
)
def test_acceptability_four_cases(wnd, payload, seq_off, ok, deliver):  # [EFSM-37]
    eng, net, sched = make_client(rcv_wnd=wnd if wnd else 0)
    client_to_established(eng, net)
    eng.rcv_wnd_max = wnd
    seg = peer_seg(eng, seq=5001 + seq_off, flags=Flags.ACK, ack=1001,
                   payload=payload)
    eng.on_segment(seg.build())
    out = net.pop_all()
    if ok:
        assert eng.recv() == deliver
    else:
        # unacceptable -> dup ACK <SEQ=SND.NXT><ACK=RCV.NXT>, nothing delivered
        assert out and out[-1].ack == eng.tcb.rcv_nxt
        assert eng.recv() == b""
