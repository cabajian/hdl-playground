"""Mirror class for the SystemVerilog ``tcp_item``, with explicit queue widths.

pyhdl-if's UVM wrapper supports queue / dynamic-array fields, but it does not
know how wide an element is: ``sprint()`` reports a queue as ``da(integral)``
with the *element count*, never the element width, so ``UvmFieldType.size`` is
left at -1 and flagged ``size_unknown``. The library then infers the width:

  * packing   -- ``size = max(abs(x) for x in v).bit_length()``
  * unpacking -- ``size = bits_available // queue_len``

Both are data-dependent. For a byte queue that is simply wrong: a payload whose
largest byte is 0x3F packs as 6-bit elements, an all-zero payload packs as
1-bit elements, and the width silently changes from transaction to transaction.

This module pins the width instead. It is a plain mirror of the SV class -- the
same shape ``pyhdl_uvm_pygen`` would emit -- with one addition: queue fields
carry an ``elem_bits`` entry in their dataclass metadata. ``bind()`` copies
those widths into the live ``UvmObjectType`` and clears ``size_unknown``, so the
inference paths above are never reached.

Nothing in pyhdl-if is modified. ``bind()`` only assigns to the public
``UvmFieldType.size`` / ``.size_unknown`` attributes of the type object that
pyhdl-if already built and handed us.

Because the SV registry caches one ``UvmObjectType`` per SV type
(``m_type2type_m`` in pyhdl_uvm_object_rgy.svh) and hands that same instance to
every wrapped object, binding once per type is enough for every item.
"""

from __future__ import annotations

import dataclasses as dc
import typing


def q(elem_bits: int):
    """Declare a queue field whose elements are ``elem_bits`` wide."""
    return dc.field(default_factory=list, metadata={"elem_bits": elem_bits})


@dc.dataclass
class tcp_item:
    """Mirror of the SV tcp_item. Field order matches uvm_field registration."""

    src_port: int = 0
    dst_port: int = 0
    seq_num: int = 0
    ack_num: int = 0
    flags: int = 0
    window: int = 0
    checksum: int = 0
    urgent_ptr: int = 0
    options: typing.List[int] = q(8)  # wire bytes
    payload: typing.List[int] = q(8)  # wire bytes


# ---------------------------------------------------------------------------
# Binding
# ---------------------------------------------------------------------------
_QUEUE_KIND = "queue"

_bound: typing.Set[int] = set()


def _elem_bits(mirror_cls) -> typing.Dict[str, int]:
    return {f.name: f.metadata["elem_bits"]
            for f in dc.fields(mirror_cls) if "elem_bits" in f.metadata}


def bind(obj, mirror_cls=tcp_item, *, strict: bool = True) -> typing.Dict[str, int]:
    """Pin queue element widths on ``obj``'s UvmObjectType from ``mirror_cls``.

    Returns {field_name: bits} for what was applied. Idempotent per type.

    With ``strict``, raises if the SV type has a queue field the mirror does not
    declare -- that field would otherwise silently fall back to inference, which
    is the whole failure mode this module exists to prevent.
    """
    obj_t = object.__getattribute__(obj, "_uvm_obj_t")
    if obj_t is None:
        raise RuntimeError("bind() before layout discovery: _uvm_obj_t is None")

    widths = _elem_bits(mirror_cls)
    applied: typing.Dict[str, int] = {}
    undeclared: typing.List[str] = []

    for f in obj_t.fields:
        if getattr(f.kind, "value", f.kind) != _QUEUE_KIND:
            continue
        if f.name in widths:
            f.size = widths[f.name]
            f.size_unknown = False
            applied[f.name] = f.size
        else:
            undeclared.append(f.name)

    if strict and undeclared:
        raise RuntimeError(
            f"queue field(s) {undeclared} on SV type "
            f"{obj_t.type_name!r} have no elem_bits in mirror {mirror_cls.__name__}; "
            "element width would be inferred from data"
        )

    _bound.add(id(obj_t))
    return applied


def is_bound(obj) -> bool:
    return id(object.__getattribute__(obj, "_uvm_obj_t")) in _bound
