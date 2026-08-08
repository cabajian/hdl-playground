import struct

import pytest

from tcp_model import Flags, TcpSegment


def test_build_parse_roundtrip_header_only():
    seg = TcpSegment(
        src_port=40000, dst_port=80, seq=1234, ack=5678,
        flags=Flags.ACK | Flags.PSH, window=4096,
    )
    out = TcpSegment.parse(seg.build())
    assert out.src_port == 40000
    assert out.dst_port == 80
    assert out.seq == 1234
    assert out.ack == 5678
    assert out.flags == Flags.ACK | Flags.PSH
    assert out.window == 4096
    assert out.payload == b""


def test_build_parse_with_payload():
    payload = bytes(range(200))
    seg = TcpSegment(src_port=1, dst_port=2, seq=0, flags=Flags.ACK, payload=payload)
    out = TcpSegment.parse(seg.build())
    assert out.payload == payload


def test_seg_len_counts_syn_fin():
    assert TcpSegment(flags=Flags.SYN).seg_len == 1
    assert TcpSegment(flags=Flags.FIN, payload=b"abc").seg_len == 4
    assert TcpSegment(flags=Flags.SYN | Flags.FIN).seg_len == 2
    assert TcpSegment(flags=Flags.ACK, payload=b"abcd").seg_len == 4


def test_mss_option_roundtrip():
    seg = TcpSegment(src_port=1, dst_port=2, flags=Flags.SYN, mss=1460)
    raw = seg.build()
    # header must be 24 bytes (20 + 4-byte MSS option)
    data_offset = (raw[12] >> 4) & 0xF
    assert data_offset == 6
    out = TcpSegment.parse(raw)
    assert out.mss == 1460


def test_options_padding_alignment():
    # MSS (4) + window scale (3) = 7 bytes -> padded to 8 -> data offset 7 words
    seg = TcpSegment(flags=Flags.SYN, mss=1460, wscale=7)
    raw = seg.build()
    assert len(raw) % 4 == 0
    data_offset = (raw[12] >> 4) & 0xF
    assert data_offset == 7
    out = TcpSegment.parse(raw)
    assert out.mss == 1460
    assert out.wscale == 7


def test_flag_properties():
    seg = TcpSegment(flags=Flags.SYN | Flags.ACK)
    assert seg.syn and seg.ack_flag
    assert not seg.fin and not seg.rst
    assert "S" in seg.flag_str() and "A" in seg.flag_str()


def test_parse_rejects_short_segment():
    with pytest.raises(ValueError):
        TcpSegment.parse(b"\x00" * 10)


def test_parse_rejects_bad_data_offset():
    raw = bytearray(TcpSegment(flags=Flags.ACK).build())
    raw[12] = 0x20  # data offset = 2 words = 8 bytes, < 20
    with pytest.raises(ValueError):
        TcpSegment.parse(bytes(raw))


def test_checksum_field_not_computed():
    # codec leaves checksum as-is (the net adapter owns it)
    seg = TcpSegment(flags=Flags.ACK, checksum=0)
    raw = seg.build()
    csum = struct.unpack("!H", raw[16:18])[0]
    assert csum == 0
