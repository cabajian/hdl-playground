"""Python logging that interleaves correctly with the simulator's own output.

Two problems stand between a `logger.info()` call and a log line that a reader
can line up against the surrounding `UVM_INFO` messages.

**Ordering.** Python and the simulator share a process but not an output
buffer. SystemVerilog `$display` goes through C stdio, which block-buffers when
stdout is a pipe (i.e. whenever pytest captures it); Python's logging writes
through its own `io` layer. Whole kilobytes of simulator output can therefore
land in the pipe long after the Python lines that were emitted between them.
`SimTimeHandler` fixes this by draining C stdio immediately before each record,
so the two streams are written in the order they were produced.

**Timestamps.** Simulation time is the only clock both sides agree on -- wall
time is meaningless here, since a millisecond of simulation takes seconds to
run. `SimTimeFilter` stamps every record with the current simulation time, in
the same `@ <n>ns` form UVM uses, so the merged log sorts naturally by eye.

Both halves are needed: the timestamps make the interleaving *checkable*, and
the flushing makes it *correct*.

Usage, once, at import time of a test runner::

    import sim_logging
    logger = sim_logging.configure(lambda: SimClock.inst().now_ns())

The time source is called per record and may fail or return None before the
simulator is up; it is reported as ``@ ?ns`` rather than being allowed to
raise. Note the caller passes a lambda, not a value -- the point is that it is
re-read for every line.

For the merged stream to reach one file the caller must also redirect the
child's stderr into its stdout (see `run_sim` in tests/conftest.py); flushing
alone cannot order writes that go to two different pipes.
"""

import ctypes
import logging
import sys
import typing

__all__ = ["configure", "SimTimeFilter", "SimTimeHandler", "FORMAT"]

# UVM prints "UVM_INFO <file>(<line>) @ <t>ns: <scope> [<id>] <msg>". Mirroring
# the severity-then-time shape keeps both sources scannable in one column, and
# the PY_ prefix makes it obvious which side emitted a line.
FORMAT = "PY_%(levelname)s %(sim_time)s: [%(name)s] %(message)s"


def _c_flush() -> typing.Optional[typing.Callable]:
    """Return libc's fflush, or None where it cannot be reached.

    Only the simulator's stdio needs draining; CPython does not write stdout
    through C stdio, so `fflush(NULL)` here costs a syscall on the simulator's
    buffer and nothing else.
    """
    try:
        return ctypes.CDLL(None).fflush
    except (OSError, AttributeError):  # non-glibc, or no dlopen(NULL)
        return None


_fflush = _c_flush()


class SimTimeFilter(logging.Filter):
    """Attach the current simulation time to every record as `sim_time`."""

    def __init__(self, time_source: typing.Optional[typing.Callable[[], int]]):
        super().__init__()
        self._time_source = time_source

    def filter(self, record: logging.LogRecord) -> bool:
        record.sim_time = self._format_time()
        return True

    def _format_time(self) -> str:
        if self._time_source is None:
            return "@ ?ns"
        try:
            now = self._time_source()
        except Exception:
            # Logging must never take down the run it is reporting on. This is
            # the normal case before the time service is wired up, and the
            # normal case again after $finish.
            return "@ ?ns"
        return "@ ?ns" if now is None else f"@ {int(now)}ns"


class SimTimeHandler(logging.StreamHandler):
    """StreamHandler that drains the simulator's stdio before each record."""

    def emit(self, record: logging.LogRecord) -> None:
        if _fflush is not None:
            _fflush(None)  # NULL => flush every open C stream
        try:
            sys.stdout.flush()  # anything the Python side print()ed
        except (ValueError, OSError):
            pass
        super().emit(record)  # StreamHandler.emit flushes self.stream


def configure(
    time_source: typing.Optional[typing.Callable[[], int]] = None,
    *,
    level: int = logging.INFO,
    stream: typing.Optional[typing.TextIO] = None,
    name: typing.Optional[str] = None,
) -> logging.Logger:
    """Install sim-time logging on the root logger and return a logger.

    Replaces any handlers already on the root logger, so this is the one and
    only logging setup a test runner should perform -- calling
    `logging.basicConfig` as well would reintroduce an unflushed handler and
    with it the interleaving problem.

    `stream` defaults to stderr, which Python keeps line-buffered even when
    redirected; sending records to a block-buffered stdout would undo the
    flushing this module exists to provide.
    """
    handler = SimTimeHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(logging.Formatter(FORMAT))
    handler.addFilter(SimTimeFilter(time_source))

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    return logging.getLogger(name) if name else root
