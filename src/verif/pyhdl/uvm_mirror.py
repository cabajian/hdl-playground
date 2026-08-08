"""Declare pyhdl-if queue-element widths from a mirror class.

pyhdl-if's UVM wrapper supports queue / dynamic-array fields, but it does not
know how wide an element is. UVM's printer reports a queue as ``da(integral)``
with the element *count*, never the element width, so ``UvmFieldType.size`` is
left at -1 and flagged ``size_unknown``. The library then infers the width from
the data:

  * packing   -- ``size = max(abs(x) for x in v).bit_length()``
  * unpacking -- ``size = bits_available // queue_len``

Both are content-dependent, which is wrong for any fixed-width element type. A
byte queue whose largest element happens to be 0x3F packs as 6-bit elements; an
all-zero one packs as 1-bit elements; the width silently changes from one
transaction to the next.

This module lets the width be *declared* instead. Mark queue fields on an
ordinary mirror dataclass -- the same shape ``pyhdl_uvm_pygen`` emits -- with
:func:`q`, then call :func:`bind` on a wrapped object of that type::

    import uvm_mirror
    from uvm_mirror import q

    @dc.dataclass
    class my_item:
        header: int = 0
        payload: list = q(8)      # byte queue
        words:   list = q(32)     # word queue

    uvm_mirror.register(my_item)          # optional: enables bind(obj) lookup
    ...
    obj = seq.create_req()
    uvm_mirror.bind(obj, my_item)         # or just bind(obj) if registered

Nothing in pyhdl-if is modified: :func:`bind` only assigns to the public
``UvmFieldType.size`` / ``.size_unknown`` attributes of the ``UvmObjectType``
that pyhdl-if already built and attached to the object.

Binding once per SV type is enough. The SV registry caches a single
``UvmObjectType`` per type (``m_type2type_m`` in ``pyhdl_uvm_object_rgy.svh``)
and attaches that same instance to every object it wraps.

Note for callers: UVM only emits a queue's 32-bit element count when the packer
has metadata enabled, and pyhdl-if's model always reads it. Set
``uvm_default_packer.use_metadata = 1`` on the SV side or queues will unpack
into whatever size they already had.
"""

from __future__ import annotations

import dataclasses as dc
import typing

__all__ = ["q", "bind", "register", "declared_widths", "is_bound", "MirrorError"]

_ELEM_BITS = "elem_bits"
_QUEUE_KIND = "queue"

# SV type name -> mirror class
_registry: typing.Dict[str, type] = {}

# id() of every UvmObjectType already bound
_bound: typing.Set[int] = set()


class MirrorError(RuntimeError):
    """Raised when a mirror cannot be applied to an object's type."""


def q(elem_bits: int, **kwargs):
    """Declare a queue field whose elements are ``elem_bits`` wide.

    Extra keyword arguments are passed through to ``dataclasses.field``.
    """
    if not isinstance(elem_bits, int) or elem_bits <= 0:
        raise ValueError(f"elem_bits must be a positive int, got {elem_bits!r}")
    metadata = dict(kwargs.pop("metadata", {}))
    metadata[_ELEM_BITS] = elem_bits
    kwargs.setdefault("default_factory", list)
    return dc.field(metadata=metadata, **kwargs)


def declared_widths(mirror_cls) -> typing.Dict[str, int]:
    """Return {field_name: elem_bits} for every queue field on ``mirror_cls``."""
    if not dc.is_dataclass(mirror_cls):
        raise MirrorError(f"{mirror_cls!r} is not a dataclass")
    return {f.name: f.metadata[_ELEM_BITS]
            for f in dc.fields(mirror_cls) if _ELEM_BITS in f.metadata}


def register(mirror_cls, sv_type_name: typing.Optional[str] = None) -> type:
    """Register ``mirror_cls`` so :func:`bind` can find it by SV type name.

    Defaults to the class's own name, which is the usual case for a mirror.
    Usable as a decorator.
    """
    _registry[sv_type_name or mirror_cls.__name__] = mirror_cls
    return mirror_cls


def _obj_type(obj):
    try:
        obj_t = object.__getattribute__(obj, "_uvm_obj_t")
    except AttributeError as e:
        raise MirrorError(f"{obj!r} is not a pyhdl-if UVM object wrapper") from e
    if obj_t is None:
        raise MirrorError("bind() before layout discovery: _uvm_obj_t is None")
    return obj_t


def bind(obj, mirror_cls=None, *, strict: bool = True) -> typing.Dict[str, int]:
    """Pin queue element widths on ``obj``'s UvmObjectType from a mirror class.

    ``mirror_cls`` defaults to whatever was :func:`register`-ed for the object's
    SV type name. Returns {field_name: bits} for the widths applied.

    Idempotent: re-binding the same type is harmless, since the widths written
    are the same. With ``strict`` (the default), a queue field on the SV type
    that the mirror does not declare raises -- that field would otherwise fall
    back to inference, which is the failure mode this module exists to prevent.
    """
    obj_t = _obj_type(obj)

    if mirror_cls is None:
        mirror_cls = _registry.get(obj_t.type_name)
        if mirror_cls is None:
            raise MirrorError(
                f"no mirror registered for SV type {obj_t.type_name!r}; "
                f"pass mirror_cls explicitly or call register()"
            )

    widths = declared_widths(mirror_cls)
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
        raise MirrorError(
            f"queue field(s) {undeclared} on SV type {obj_t.type_name!r} have no "
            f"{_ELEM_BITS} in mirror {mirror_cls.__name__}; element width would "
            f"be inferred from data"
        )

    _bound.add(id(obj_t))
    return applied


def is_bound(obj) -> bool:
    """True if this object's UvmObjectType has already been bound."""
    return id(_obj_type(obj)) in _bound
