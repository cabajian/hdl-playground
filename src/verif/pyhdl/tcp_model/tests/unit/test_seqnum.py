from tcp_model.core import seqnum as sn


def test_basic_order_no_wrap():
    assert sn.seq_lt(5, 10)
    assert sn.seq_gt(10, 5)
    assert sn.seq_leq(5, 5)
    assert sn.seq_geq(5, 5)
    assert not sn.seq_lt(10, 5)


def test_wraparound():
    near_top = sn.MASK - 5      # 0xFFFFFFFA
    low = 5
    # near_top is "less than" low across the wrap boundary
    assert sn.seq_lt(near_top, low)
    assert sn.seq_gt(low, near_top)
    assert sn.add(near_top, 11) == low  # wraps past 2**32


def test_add_sub_mod():
    assert sn.add(sn.MASK, 1) == 0
    assert sn.sub(0, 1) == sn.MASK
    assert sn.sub(10, 3) == 7


def test_s32_signedness():
    assert sn.s32(1) == 1
    assert sn.s32(sn.MASK) == -1
    assert sn.s32(1 << 31) == -(1 << 31)


def test_between_inclusive():
    assert sn.between(5, 5, 10)
    assert sn.between(5, 10, 10)
    assert sn.between(5, 7, 10)
    assert not sn.between(5, 11, 10)
    assert not sn.between(5, 4, 10)


def test_between_wrap():
    lo = sn.MASK - 2
    hi = 2
    assert sn.between(lo, sn.MASK, hi)
    assert sn.between(lo, 0, hi)
    assert not sn.between(lo, 5, hi)


def test_acceptable_zero_len_zero_window():
    # len 0, wnd 0 -> only seq == rcv_nxt
    assert sn.acceptable(100, 0, 100, 0)
    assert not sn.acceptable(101, 0, 100, 0)


def test_acceptable_zero_len_window():
    assert sn.acceptable(100, 0, 100, 10)
    assert sn.acceptable(109, 0, 100, 10)
    assert not sn.acceptable(110, 0, 100, 10)   # one past window
    assert not sn.acceptable(99, 0, 100, 10)


def test_acceptable_data_window():
    # data overlapping the window edge is acceptable
    assert sn.acceptable(100, 5, 100, 10)
    assert sn.acceptable(108, 5, 100, 10)       # tail past window, head inside
    assert sn.acceptable(95, 10, 100, 10)       # head before, tail inside
    assert not sn.acceptable(200, 5, 100, 10)   # wholly outside


def test_acceptable_data_zero_window():
    # any data with a closed window is unacceptable
    assert not sn.acceptable(100, 5, 100, 0)
