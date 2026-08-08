"""Mirror class for the SystemVerilog ``tcp_item``.

Ordinary mirror of the SV sequence item -- the same shape ``pyhdl_uvm_pygen``
emits -- with the queue element widths declared rather than left to pyhdl-if's
data-dependent inference. See ``uvm_mirror`` for the mechanism.

``options`` and ``payload`` are wire bytes, so both are 8 bits per element.
"""

from __future__ import annotations

import dataclasses as dc
import typing

import uvm_mirror
from uvm_mirror import q


@uvm_mirror.register
@dc.dataclass
class tcp_item:
    """Field order matches the uvm_field registration order in tcp_item.sv."""

    src_port: int = 0
    dst_port: int = 0
    seq_num: int = 0
    ack_num: int = 0
    flags: int = 0
    window: int = 0
    checksum: int = 0
    urgent_ptr: int = 0
    options: typing.List[int] = q(8)
    payload: typing.List[int] = q(8)
