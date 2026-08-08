"""Valid TCP behavior the EFSM paper does not model.

Mostly RFC 9293 material that post-dates RFC 793: blind-attack mitigations
(RFC 5961), the WL1/WL2 window-update guard, futuristic-ACK handling, data on
SYNs, out-of-order FIN sequencing, and check-ordering corners.
"""

import pytest

from tcp_model import ConnState as S, Flags
from .util import enter, make_client, peer_seg, client_to_established

SYNC_STATES = ["ESTABLISHED", "FIN_WAIT_1", "FIN_WAIT_2",
               "CLOSE_WAIT", "CLOSING", "LAST_ACK"]


# ------------------------------------------------ RFC 5961 mitigations
@pytest.mark.parametrize("state", SYNC_STATES)
def test_in_window_syn_gets_challenge_ack_not_reset(state):     # 3.10.7.4 / 5961
    eng, net, sched, ctx = enter(state)
    eng.on_segment(peer_seg(eng, seq=ctx["peer_seq"], flags=Flags.SYN | Flags.ACK,
                            ack=ctx["our_una"]).build())
    out = net.pop_all()
    assert eng.state == getattr(S, state)           # paper would RST + close
    assert out and out[-1].ack_flag and not out[-1].rst


@pytest.mark.parametrize("state", SYNC_STATES)
def test_in_window_rst_not_at_rcv_nxt_challenged(state):        # 3.10.7.4 / 5961
    eng, net, sched, ctx = enter(state)
    eng.on_segment(peer_seg(eng, seq=ctx["peer_seq"] + 100, flags=Flags.RST).build())
    out = net.pop_all()
    assert eng.state == getattr(S, state)
    assert out and out[-1].ack_flag and not out[-1].rst


@pytest.mark.parametrize("state", SYNC_STATES)
def test_exact_seq_rst_resets(state):                           # 3.10.7.4
    eng, net, sched, ctx = enter(state)
    eng.on_segment(peer_seg(eng, seq=ctx["peer_seq"], flags=Flags.RST).build())
    assert eng.state in (S.CLOSED, S.LISTEN)


def test_rst_wins_over_syn_in_check_order():        # 3.10.7.4 second vs fourth check
    eng, net, sched, ctx = enter("ESTABLISHED")
    eng.on_segment(peer_seg(eng, seq=ctx["peer_seq"],
                            flags=Flags.RST | Flags.SYN).build())
    assert eng.state == S.CLOSED                    # RST processed first


# ------------------------------------------------ ACK processing corners
def test_segment_without_ack_bit_dropped():         # 3.10.7.4 fifth check
    eng, net, sched, ctx = enter("ESTABLISHED")
    eng.on_segment(peer_seg(eng, seq=ctx["peer_seq"], flags=0,
                            payload=b"no-ack").build())
    assert eng.recv() == b"" and net.pop_all() == []


def test_futuristic_ack_acked_and_dropped():        # 3.10.7.4 (paper drops silently)
    eng, net, sched, ctx = enter("ESTABLISHED")
    eng.on_segment(peer_seg(eng, seq=ctx["peer_seq"], flags=Flags.ACK,
                            ack=ctx["our_nxt"] + 500).build())
    out = net.pop_all()
    assert out and out[-1].ack_flag and eng.tcb.snd_una == ctx["our_una"]


def test_stale_segment_does_not_update_window():    # 3.10.7.4 WL1/WL2 guard
    eng, net, sched, ctx = enter("ESTABLISHED")
    # fresh window from the peer's current seq
    eng.on_segment(peer_seg(eng, seq=ctx["peer_seq"], flags=Flags.ACK,
                            ack=ctx["our_una"], window=8000).build())
    assert eng.tcb.snd_wnd == 8000
    # an old (lower-seq) segment carrying a bigger window must be ignored
    eng.on_segment(peer_seg(eng, seq=ctx["peer_seq"] - 10, flags=Flags.ACK,
                            ack=ctx["our_una"], window=60000).build())
    assert eng.tcb.snd_wnd == 8000                  # paper has no such guard


# ------------------------------------------------ FIN sequencing
def test_fin_with_data_consumes_len_plus_one():     # 3.10.7.4 (paper [EFSM-38] bug)
    eng, net, sched, ctx = enter("ESTABLISHED")
    eng.on_segment(peer_seg(eng, seq=ctx["peer_seq"], flags=Flags.FIN | Flags.ACK,
                            ack=ctx["our_una"], payload=b"bye").build())
    a = net.pop_all()[-1]
    assert eng.recv() == b"bye"
    assert a.ack == ctx["peer_seq"] + 3 + 1         # data then FIN
    assert eng.state == S.CLOSE_WAIT


def test_out_of_order_fin_deferred_until_gap_fills():   # FIN only in sequence
    eng, net, sched, ctx = enter("ESTABLISHED")
    p = ctx["peer_seq"]
    # FIN + tail data arrives first, with a hole before it
    eng.on_segment(peer_seg(eng, seq=p + 4, flags=Flags.FIN | Flags.ACK,
                            ack=ctx["our_una"], payload=b"tail").build())
    assert eng.state == S.ESTABLISHED               # not processed yet
    # hole fills -> data reassembles and the deferred FIN lands, no retransmit
    eng.on_segment(peer_seg(eng, seq=p, flags=Flags.ACK,
                            ack=ctx["our_una"], payload=b"head").build())
    assert eng.recv() == b"headtail"
    assert eng.state == S.CLOSE_WAIT
    assert net.pop_all()[-1].ack == p + 8 + 1


def test_multi_hole_reassembly_then_fin():
    eng, net, sched, ctx = enter("ESTABLISHED")
    p = ctx["peer_seq"]
    eng.on_segment(peer_seg(eng, seq=p + 6, flags=Flags.ACK, ack=1001,
                            payload=b"cc").build())
    eng.on_segment(peer_seg(eng, seq=p + 3, flags=Flags.ACK, ack=1001,
                            payload=b"bbb").build())
    eng.on_segment(peer_seg(eng, seq=p + 8, flags=Flags.FIN | Flags.ACK,
                            ack=1001).build())
    assert eng.state == S.ESTABLISHED
    eng.on_segment(peer_seg(eng, seq=p, flags=Flags.ACK, ack=1001,
                            payload=b"aaa").build())
    assert eng.recv() == b"aaabbbcc"
    assert eng.state == S.CLOSE_WAIT


# ------------------------------------------------ data on SYNs
def test_synack_payload_delivered_after_establish():    # 3.10.7.3
    eng, net, sched, _ = enter("SYN_SENT")
    eng.on_segment(peer_seg(eng, seq=5000, flags=Flags.SYN | Flags.ACK,
                            ack=1001, payload=b"greet").build())
    assert eng.state == S.ESTABLISHED
    assert eng.recv() == b"greet"
    assert net.pop_all()[-1].ack == 5001 + 5        # SYN + payload acked


def test_simultaneous_open_syn_payload_queued():        # 3.10.7.3
    eng, net, sched, _ = enter("SYN_SENT")
    eng.on_segment(peer_seg(eng, seq=5000, flags=Flags.SYN,
                            payload=b"early").build())
    assert eng.state == S.SYN_RECEIVED and eng.recv() == b""
    eng.on_segment(peer_seg(eng, seq=5006, flags=Flags.ACK, ack=1001).build())
    assert eng.state == S.ESTABLISHED
    assert eng.recv() == b"early"                   # released on establish


def test_syn_rcvd_ack_data_fin_full_cascade():      # single segment does it all
    eng, net, sched, _ = enter("SYN_SENT")
    eng.on_segment(peer_seg(eng, seq=5000, flags=Flags.SYN).build())   # sim-open
    net.clear()
    eng.on_segment(peer_seg(eng, seq=5001, flags=Flags.ACK | Flags.FIN,
                            ack=1001, payload=b"all-in-one").build())
    assert eng.state == S.CLOSE_WAIT
    assert eng.recv() == b"all-in-one"
    assert net.pop_all()[-1].ack == 5001 + 10 + 1


# ------------------------------------------------ TIME-WAIT details
def test_time_wait_non_fin_does_not_restart_timer():
    eng, net, sched, _ = enter("TIME_WAIT", msl=1.0)
    sched.advance(1.9)
    eng.on_segment(peer_seg(eng, seq=5002, flags=Flags.ACK, ack=1002).build())
    assert net.pop_all()[-1].ack_flag               # acked...
    sched.advance(0.2)                              # ...but 2MSL not extended
    assert eng.state == S.CLOSED


def test_time_wait_exact_rst_closes_in_window_rst_challenged():
    eng, net, sched, ctx = enter("TIME_WAIT")
    eng.on_segment(peer_seg(eng, seq=ctx["peer_seq"] + 7, flags=Flags.RST).build())
    assert eng.state == S.TIME_WAIT                 # challenge, not close
    assert net.pop_all()[-1].ack_flag
    eng.on_segment(peer_seg(eng, seq=ctx["peer_seq"], flags=Flags.RST).build())
    assert eng.state == S.CLOSED


# ------------------------------------------------ demux / listen pinning
def test_pinned_listener_ignores_foreign_syn():
    eng, net, sched = make_client()                 # remote_port pinned to 80
    eng.passive_open()
    wrong = peer_seg(eng, seq=7000, flags=Flags.SYN)
    wrong.src_port = 9999
    eng.on_segment(wrong.build())
    assert eng.state == S.LISTEN and net.pop_all() == []
    right = peer_seg(eng, seq=7000, flags=Flags.SYN)   # src = pinned port
    eng.on_segment(right.build())
    assert eng.state == S.SYN_RECEIVED


def test_segment_for_other_local_port_ignored():
    eng, net, sched, ctx = enter("ESTABLISHED")
    seg = peer_seg(eng, seq=ctx["peer_seq"], flags=Flags.RST)
    seg.dst_port = eng.local_port + 1
    eng.on_segment(seg.build())
    assert eng.state == S.ESTABLISHED and net.pop_all() == []


# ------------------------------------------------ zero-window interplay
def test_zero_window_data_refused_probe_acked():    # 3.8.6 receiver side
    eng, net, sched, ctx = enter("ESTABLISHED")
    eng.rcv_wnd_max = 0                             # advertise zero
    eng.on_segment(peer_seg(eng, seq=ctx["peer_seq"], flags=Flags.ACK,
                            ack=ctx["our_una"], payload=b"p").build())
    a = net.pop_all()[-1]
    assert a.ack == ctx["peer_seq"] and eng.recv() == b""   # probe answered
