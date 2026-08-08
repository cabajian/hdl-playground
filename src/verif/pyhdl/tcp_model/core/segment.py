"""TCP segment model and wire codec.

This is the *single* definition of the TCP wire format, shared by the standalone
engine and (per the integration plan) the simulator adapter, which passes the same
``pack()``-format bytes across the pyhdl-if boundary.

The codec does **not** compute or validate the TCP checksum: that requires the IP
pseudo-header, which the engine never sees. The checksum field is carried as-is
(0 on build) and the net adapter (SV IP layer / TUN) owns it.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field


class Flags:
    FIN = 0x01
    SYN = 0x02
    RST = 0x04
    PSH = 0x08
    ACK = 0x10
    URG = 0x20
    ECE = 0x40
    CWR = 0x80


_FLAG_NAMES = [
    (Flags.SYN, "S"),
    (Flags.FIN, "F"),
    (Flags.RST, "R"),
    (Flags.PSH, "P"),
    (Flags.ACK, "A"),
    (Flags.URG, "U"),
    (Flags.ECE, "E"),
    (Flags.CWR, "C"),
]

# TCP option kinds
OPT_EOL = 0
OPT_NOP = 1
OPT_MSS = 2
OPT_WSCALE = 3


@dataclass
class TcpSegment:
    src_port: int = 0
    dst_port: int = 0
    seq: int = 0
    ack: int = 0
    flags: int = 0
    window: int = 0
    checksum: int = 0
    urgent_ptr: int = 0
    payload: bytes = b""
    mss: int | None = None          # decoded MSS option, if present
    wscale: int | None = None       # decoded window-scale option, if present
    raw_options: bytes = b""        # any options not otherwise decoded

    # --- flag helpers -----------------------------------------------------
    @property
    def syn(self) -> bool:
        return bool(self.flags & Flags.SYN)

    @property
    def fin(self) -> bool:
        return bool(self.flags & Flags.FIN)

    @property
    def rst(self) -> bool:
        return bool(self.flags & Flags.RST)

    @property
    def ack_flag(self) -> bool:
        return bool(self.flags & Flags.ACK)

    @property
    def seg_len(self) -> int:
        """Length in sequence space: data + 1 for SYN + 1 for FIN."""
        return len(self.payload) + (1 if self.syn else 0) + (1 if self.fin else 0)

    # --- codec ------------------------------------------------------------
    def _build_options(self) -> bytes:
        opts = bytearray()
        if self.mss is not None:
            opts += struct.pack("!BBH", OPT_MSS, 4, self.mss)
        if self.wscale is not None:
            opts += struct.pack("!BBB", OPT_WSCALE, 3, self.wscale)
        opts += self.raw_options
        # pad to a multiple of 4 bytes with NOPs / EOL
        while len(opts) % 4 != 0:
            opts.append(OPT_NOP)
        return bytes(opts)

    def build(self) -> bytes:
        opts = self._build_options()
        data_offset = (20 + len(opts)) // 4
        offset_byte = (data_offset & 0x0F) << 4  # reserved bits = 0
        header = struct.pack(
            "!HHIIBBHHH",
            self.src_port & 0xFFFF,
            self.dst_port & 0xFFFF,
            self.seq & 0xFFFFFFFF,
            self.ack & 0xFFFFFFFF,
            offset_byte,
            self.flags & 0xFF,
            self.window & 0xFFFF,
            self.checksum & 0xFFFF,
            self.urgent_ptr & 0xFFFF,
        )
        return header + opts + self.payload

    @classmethod
    def parse(cls, data: bytes) -> "TcpSegment":
        if len(data) < 20:
            raise ValueError("TCP segment shorter than 20 bytes")
        (src, dst, seq, ack, offset_byte, flags, window, checksum, urg) = struct.unpack(
            "!HHIIBBHHH", data[:20]
        )
        data_offset = (offset_byte >> 4) & 0x0F
        hdr_len = data_offset * 4
        if hdr_len < 20 or hdr_len > len(data):
            raise ValueError(f"bad data offset {data_offset}")
        opt_bytes = data[20:hdr_len]
        payload = data[hdr_len:]
        seg = cls(
            src_port=src,
            dst_port=dst,
            seq=seq,
            ack=ack,
            flags=flags,
            window=window,
            checksum=checksum,
            urgent_ptr=urg,
            payload=payload,
        )
        seg._parse_options(opt_bytes)
        return seg

    def _parse_options(self, opt_bytes: bytes) -> None:
        i = 0
        leftover = bytearray()
        n = len(opt_bytes)
        while i < n:
            kind = opt_bytes[i]
            if kind == OPT_EOL:
                break
            if kind == OPT_NOP:
                i += 1
                continue
            if i + 1 >= n:
                break
            length = opt_bytes[i + 1]
            if length < 2 or i + length > n:
                break
            body = opt_bytes[i + 2 : i + length]
            if kind == OPT_MSS and length == 4:
                self.mss = struct.unpack("!H", body)[0]
            elif kind == OPT_WSCALE and length == 3:
                self.wscale = body[0]
            else:
                leftover += opt_bytes[i : i + length]
            i += length
        self.raw_options = bytes(leftover)

    def flag_str(self) -> str:
        s = "".join(ch for bit, ch in _FLAG_NAMES if self.flags & bit)
        return s or "-"

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        extra = f" mss={self.mss}" if self.mss is not None else ""
        return (
            f"<TcpSegment {self.src_port}->{self.dst_port} {self.flag_str()} "
            f"seq={self.seq} ack={self.ack} win={self.window} "
            f"len={len(self.payload)}{extra}>"
        )
