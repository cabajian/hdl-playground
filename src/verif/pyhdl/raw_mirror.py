"""Python side of the raw-bytes transport (see pyhdl_raw.sv).

Where the normal path has Python assign every field of a mirror object, this
path has it assign exactly one: a byte queue holding the transaction's wire
image. SystemVerilog reconstructs the real sequence item from those bytes.

Use it when an item's *shape* is awkward for pyhdl-if's packer or UVM's printer
-- nested objects, unions, fields whose width the mirror cannot express -- and
the item already has, or can cheaply get, a byte-level codec. It does not help
with item *size*: the same total bytes still cross in one pack, so
UVM_MAX_STREAMBITS still applies.

    import raw_mirror

    class MySeq(uvm_sequence_impl):
        async def body(self):
            await raw_mirror.send_raw(self.proxy, bytes(some_packet))

The element width is declared as 8 bits through ``uvm_mirror.q`` for the same
reason every other queue field in this repo declares one: pyhdl-if otherwise
infers it from the data, so an all-zero image would pack as 1-bit elements.

The other queue prerequisite, ``uvm_default_packer.use_metadata = 1``, is set by
``pyhdl_raw_seq`` itself, so a testbench using this path does not have to know
about it.
"""

from __future__ import annotations

import dataclasses as dc
import typing

import uvm_mirror
from uvm_mirror import q

__all__ = ["bytes_item", "bind", "send_raw", "MAX_IMAGE_BYTES"]

# Largest image one pack_ints()/unpack_ints() round trip can carry: UVM's
# bitstream is UVM_STREAMBITS wide (4096 unless the testbench raises
# `UVM_MAX_STREAMBITS), and the carrier spends 32 of those bits on the queue's
# element count. Mirrors RAW_MAX_IMAGE_BYTES in pyhdl_raw.sv.
#
# Checked here rather than only in SV because past this size the pack/unpack
# round trip truncates silently -- by the time SV sees the queue, the length it
# would test is already wrong. SV keeps a backstop check for images that arrive
# oversized by some other route.
MAX_IMAGE_BYTES = (4096 - 32) // 8


@uvm_mirror.register
@dc.dataclass
class bytes_item:
    """Mirror of the SV ``bytes_item``: one byte queue, nothing else."""

    raw: typing.List[int] = q(8)


def bind(req) -> None:
    """Declare the queue element width on `req`'s type, once per SV type."""
    if not uvm_mirror.is_bound(req):
        uvm_mirror.bind(req, bytes_item)


async def send_raw(proxy, data: bytes) -> None:
    """Drive one wire image through `proxy`'s sequencer.

    The caller is responsible for holding whatever lock serializes SV-blocking
    calls (see best_practices.md 1) -- this issues three of them.
    """
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(
            f"raw image of {len(data)} B exceeds MAX_IMAGE_BYTES={MAX_IMAGE_BYTES}. "
            "This path does not fragment: either raise `UVM_MAX_STREAMBITS on the SV "
            "side (and MAX_IMAGE_BYTES here to match), or chunk the transaction."
        )

    req = proxy.create_req()
    bind(req)

    v = req.pack()
    v.raw = list(data)
    req.unpack(v)

    await proxy.start_item(req)
    await proxy.finish_item(req)
