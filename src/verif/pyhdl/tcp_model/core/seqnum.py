"""32-bit modular sequence-number arithmetic.

TCP sequence numbers live in a 32-bit space and wrap around. Comparisons must be
done modulo 2**32 using the "serial number arithmetic" trick (RFC 1982): treat the
difference as a signed 32-bit value. ``a < b`` iff ``(int32)(a - b) < 0``.
"""

MOD = 1 << 32
MASK = MOD - 1
_HALF = 1 << 31


def s32(x: int) -> int:
    """Map an unsigned 32-bit difference to a signed value in [-2**31, 2**31)."""
    x &= MASK
    return x - MOD if x >= _HALF else x


def add(a: int, n: int) -> int:
    """Add ``n`` to sequence number ``a`` modulo 2**32."""
    return (a + n) & MASK


def sub(a: int, b: int) -> int:
    """Unsigned distance ``a - b`` modulo 2**32 (result in [0, 2**32))."""
    return (a - b) & MASK


def seq_lt(a: int, b: int) -> bool:
    return s32(a - b) < 0


def seq_leq(a: int, b: int) -> bool:
    return s32(a - b) <= 0


def seq_gt(a: int, b: int) -> bool:
    return s32(a - b) > 0


def seq_geq(a: int, b: int) -> bool:
    return s32(a - b) >= 0


def between(lo: int, x: int, hi: int) -> bool:
    """Inclusive window test: ``lo <= x <= hi`` in modular space.

    Used for ACK acceptability (e.g. ``SND.UNA < SEG.ACK <= SND.NXT`` is
    ``seq_lt(una, ack) and between(una, ack, nxt)`` etc.).
    """
    return seq_leq(lo, x) and seq_leq(x, hi)


def acceptable(seg_seq: int, seg_len: int, rcv_nxt: int, rcv_wnd: int) -> bool:
    """RFC 9293 3.10.7.4 segment-acceptability test (four cases)."""
    win_end = add(rcv_nxt, rcv_wnd)  # one past the window
    if seg_len == 0:
        if rcv_wnd == 0:
            return seg_seq == rcv_nxt
        # rcv_nxt <= seg_seq < rcv_nxt + rcv_wnd
        return seq_leq(rcv_nxt, seg_seq) and seq_lt(seg_seq, win_end)
    # seg_len > 0
    if rcv_wnd == 0:
        return False
    last = add(seg_seq, seg_len - 1)
    in_win = lambda s: seq_leq(rcv_nxt, s) and seq_lt(s, win_end)
    return in_win(seg_seq) or in_win(last)
