# PyHDL-IF: Practices, Pitfalls, and Debugging

Working notes for building Python↔SystemVerilog testbenches with
[`pyhdl-if`](https://github.com/fvutils/pyhdl-if) under Verilator, and for
debugging them when they misbehave.

Everything here was learned the hard way in this repo, on **Verilator 5.49**,
**UVM 1.2**, and **pyhdl-if 0.2.0**. Behaviour is version-sensitive — where a
finding depends on a specific version, that is called out.

**If you are starting a new testbench, read §1 and §2 first.** If something is
already broken, jump to §7 (symptom index).

---

## 1. The one rule that matters most

> **At most one pyhdl-if imp task may be in flight at a time.**

An *imp task* is any `async` Python method implemented in SV — it becomes a
blocking SV task and consumes simulation time. Overlapping two of them wedges
Verilator's inactive region and the simulation dies with:

```
%Error-DIDNOTCONVERGE: tb.sv:7: Inactive region did not converge after
                       '--converge-limit' of 10000 tries
```

This is not a niche corner. Two independent ways to hit it:

- Several activations of the *same* task. `asyncio.gather` of three
  `wait_ns(...)` calls wedges before any returns.
- Two *different* tasks overlapping. A UVM sequence's
  `start_item()`/`finish_item()` on one sequencer while another Python task is
  parked in `wait_ns()` — which happens the moment two agents are driven by
  event-driven Python code.

Non-blocking calls are fine. Imp **functions** (plain `def`) and exp calls in
either direction do not consume simulation time and do not participate in this.
Concurrent *sequences* are also fine, as long as no time-consuming imp task
overlaps them.

### The fix: serialize with a lock

```python
_sv_lock = None

def sv_lock():
    global _sv_lock
    if _sv_lock is None:
        _sv_lock = asyncio.Lock()      # created on the running loop
    return _sv_lock

# every SV-blocking call site
async with sv_lock():
    await self.proxy.start_item(req)
    await self.proxy.finish_item(req)
```

Whatever pumps simulation time must do so in **short quanta** while holding the
lock, or everything else starves behind it. In `tcp/test_runner.py` the pump
advances at most 20 µs per acquisition.

### Consequence: you cannot use a timer wheel directly

Anything that spawns one task per timer — `tcp_model`'s `SimScheduler`, for
example — will call the time service concurrently and wedge. Put a multiplexer
in front of it: waiters go on a deadline heap and get an `asyncio.Future`, and a
single pump loop performs the one real `wait_ns()` and resolves whatever has
come due. `TimeMux` in `tcp/test_runner.py` is a complete worked example.

A useful side effect: simulation time then advances **only** while your pump
runs, which is exactly the control a testbench wants.

---

## 2. Python method mapping

How you declare the Python method decides what it becomes in SV:

| Python | SV | Consumes sim time | Notes |
|---|---|---|---|
| `def` + `@hif.exp` | `function` implemented in Python, called from SV | no | logging, config, scoreboard hand-off |
| `async def` + `@hif.exp` | `task` implemented in Python, called from SV | yes | a Python-driven stimulus session |
| `def` + `@hif.imp` | `function` implemented in SV, called from Python | no | `now_ns()`, register peeks |
| `async def` + `@hif.imp` | `task` implemented in SV, called from Python | yes | `wait_ns()`, driving a bus — **subject to §1** |

Guidance:

- Prefer **functions** wherever the operation is instantaneous. They are exempt
  from the concurrency rule and are far easier to reason about.
- Calling an async method at **simulation time 0** can deadlock. Start the
  Python side after a small delay, inside the run phase:

  ```systemverilog
  #100ns;              // let time-0 settle before any imp task
  pyhdl_if_start();
  ```

- Keep argument types simple. Scalars (`int`, `longint`), strings, and
  `typing.List` (marshalled as a Python list) are well-trodden. Annotating a
  parameter with a class type produces a `PyObject` on the SV side.

---

## 3. Verilator convergence and interface hygiene

Verilator is sensitive to combinational loops and NBA oscillation. Beyond §1:

- **Pick one clocking discipline per signal and stick to it.** Driving through
  a clocking block while the far end samples the raw signal (or vice versa)
  across a cross-wired interface loses the first beat of every burst. In this
  repo's TCP testbench both the driver and the monitor use plain non-blocking
  assignments on `@(posedge clk)`:

  ```systemverilog
  // driver
  foreach (bytes[i]) begin
     @(posedge vif.clk);
     vif.tx_valid <= 1'b1;
     vif.tx_data  <= bytes[i];
  end

  // monitor - sees the value the driver set on the previous edge
  forever begin
     @(posedge vif.clk);
     if (vif.rx_valid === 1'b1) ...
  end
  ```

  The ether/counter testbenches use clocking blocks throughout, which is also
  fine — the failure comes from *mixing* them on the same signal.

- Use `wire` for DUT outputs inside an interface, and sample through a clocking
  block or consistently on the edge.
- Declare the virtual interface with a modport (`virtual my_if.tb vif`) where
  you can; it gives Verilator explicit directionality.

---

## 4. UVM + pyhdl-if in one Verilator binary

This combination works, but four things must be handled. All four are solved
in-tree in `src/verif/pyhdl/tcp/` — no edits to the installed pyhdl-if.

### 4.1 The shipped sequence proxy does not elaborate

`pyhdl_uvm_sequence_proxy #(REQ)` declares a self-parameterized helper
(`class helper #(REQ) extends imp_impl #(helper #(REQ,REQ))`), which triggers a
Verilator internal error:

```
%Error: Internal Error: pyhdl_uvm_sequence_proxy.svh:120:
        V3Param.cpp:523: Couldn't find pin in clone list
```

**Fix:** hand-specialize the proxy and its helper for your concrete REQ type.
See `tcp_py_seq.sv`. The Python contract is unchanged — the sequence still names
a `pyclass` whose `body()` calls `create_req()`/`start_item()`/`finish_item()`
through `self.proxy`.

### 4.2 `pyhdl_uvm_type_utils` does not compile

The macro builds every wrapper class as:

```systemverilog
function new(uvm_object obj);
    uvm_w_t impl = new(obj);   // initializer counts as a statement
    super.new(impl);
endfunction
```

Verilator rejects it (`SUPERNFIRST`) in ~11 files. **`-Wno-SUPERNFIRST` is not a
fix** — the warning is load-bearing, and suppressing it makes Verilator emit C++
that references `impl` before declaring it. The macro's `` `ifdef VCS `` variant
(`super.new(uvm_w_t::new(obj))`) does not parse either.

**Fix:** re-define just that macro with a static factory, before including the
library. The include guard in `pyhdl_uvm_macros.svh` makes the redefinition
stick, because the library's own include of it then becomes a no-op:

```systemverilog
`include "pyhdl_uvm_macros.svh"
`undef pyhdl_uvm_type_utils
`define pyhdl_uvm_type_utils(uvm_t, uvm_w_t, base_t, base_w_t) \
    class uvm_w_t``_w extends uvm_t``_imp_impl #(uvm_w_t) ... \
        static function uvm_w_t __vlt_mk_impl(uvm_object obj); \
            uvm_w_t impl = new(obj); \
            return impl; \
        endfunction \
        function new(uvm_object obj); \
            super.new(__vlt_mk_impl(obj));   /* first statement */ \
        endfunction \
    ...
`include "pyhdl_uvm.sv"
```

See `pyhdl_uvm_vlt.sv`.

### 4.3 DPI export symbols do not get linked

With a UVM-sized design, Verilator splits output and archives it into
`V<top>__ALL.a`. The DPI **export** wrappers land in `__Dpi.o` inside that
archive, and only `libpyhdl_if.so` references them — so `ld` never extracts that
member:

```
/usr/bin/ld: libpyhdl_if.so: undefined reference to `pyhdl_call_if_invoke_hdl_f'
```

(The smaller ether testbench does not hit this: its `__Dpi.o` gets pulled in for
other reasons.)

**Fix:** force each symbol to be treated as undefined so the archive member is
extracted:

```
-LDFLAGS -Wl,-u,pyhdl_call_if_invoke_hdl_f
-LDFLAGS -Wl,-u,pyhdl_call_if_invoke_hdl_t
-LDFLAGS -Wl,-u,pyhdl_call_if_response_py_t
-LDFLAGS -Wl,-u,pyhdl_pi_if_RegisterTimeCB
```

### 4.4 UVM version matters for the field transport

pyhdl-if's Python packer model tracks **UVM 1.2**'s `pack_ints`. Against UVM
1800.2-2020 (2020.3.1) `sprint()` layout discovery is still correct but the
bitstream slicing is misaligned — a freshly constructed, all-zero item packs
back as garbage (`dst_port=0xa34`, …) and `unpack()` raises
`UVM/BASE/PACKER/UNPACK/N2NN`.

**Assert the round-trip in a smoke test** so a version bump fails loudly rather
than silently corrupting stimulus:

```python
v = req.pack()
nonzero = {k: hex(x) for k, x in vars(v).items() if isinstance(x, int) and x}
assert not nonzero, f"fresh item packs non-zero: {nonzero}"
```

Also note: **UVM 1.2 needs Verilator 5.4x**. On 5.020 the `UVM_VERSION_STRING`
macro fails to preprocess and a wave of `type_id`/`randomize() with` errors
follows. This repo takes Verilator from the `verilator` PyPI wheel for that
reason, not apt.

---

## 5. Sequence items that cross the boundary

### 5.1 Field transport is read-modify-write

`req.pack()` returns a **detached snapshot**. Mutating it does nothing until
`req.unpack(v)`. Never unpack a freshly constructed snapshot — it zeroes every
field you did not set.

```python
req = self.proxy.create_req()
v = req.pack()          # snapshot of current values
v.seq_num = 1234        # mutate
req.unpack(v)           # write all fields back
```

### 5.2 Queues need the packer's metadata

UVM's `uvm_pack_arrayN` emits a queue's 32-bit element count **only when
`packer.use_metadata` is set**, while pyhdl-if's model always reads it. Without
this a queue unpacks into whatever size it already had — i.e. empty, silently:

```systemverilog
uvm_default_packer.use_metadata = 1;   // in the test's build_phase
```

Integral fields are unaffected (`uvm_pack_intN` does not consult it).

### 5.3 Queue element width is inferred — pin it

pyhdl-if supports queue fields but does not know how wide an element is:
`sprint()` reports `da(integral)` with the element *count*, never the width. It
then infers:

- packing: `size = max(abs(x) for x in v).bit_length()`
- unpacking: `size = bits_available // queue_len`

Both are content-dependent. A byte queue whose largest element is `0x3F` packs
as **6-bit** elements; an all-zero payload as **1-bit**.

Declare the width in a **mirror class** and apply it with `uvm_mirror.bind()`
(see `src/verif/pyhdl/uvm_mirror.py`):

```python
@uvm_mirror.register
@dc.dataclass
class tcp_item:
    src_port: int = 0
    payload: typing.List[int] = q(8)     # 8 bits per element
```

`bind()` writes `UvmFieldType.size` and clears `size_unknown`, so the inference
paths never run. It touches nothing inside pyhdl-if. One bind per *type* is
enough: the SV registry caches one `UvmObjectType` per SV type
(`m_type2type_m`) and attaches that same instance to every wrapped object.

### 5.4 Do not use `uvm_object::compare()` for transport checks

Under UVM 1.2 field automation compares two items by **object handle**, so a
driven item and a monitored item always miscompare. Compare the wire images
instead — which is also a more honest statement of what a transport scoreboard
checks:

```systemverilog
if (exp.to_bytes() != got.to_bytes()) ...
```

### 5.5 When the item is too awkward to mirror, send raw bytes

Everything above assumes the item's fields can be described to the packer.
Some cannot: nested objects, unions, fields whose width the mirror has no way
to express. Rather than fight the mirror, send the item's **wire image** as a
single byte queue and rebuild it in SystemVerilog.

`pyhdl_raw.sv` + `raw_mirror.py` implement this, and **nothing about it is
per-item-type**. Python assigns one field:

```python
await raw_mirror.send_raw(self.proxy, bytes(scapy_packet))
```

An item opts in by extending `seq_item_serializable` (itself a
`uvm_sequence_item`) — no new class. The codec it asks for is not extra work: a
driver serializing, a monitor reassembling and a scoreboard comparing wire
images all need exactly these two methods, so they belong on the item:

```systemverilog
class tcp_item extends seq_item_serializable;
   virtual function byte_q_t to_bytes();            // already existed
   virtual function bit      from_bytes(byte_q_t b); // already existed
```

The carrier Python fills in, `bytes_item`, is a `seq_item_serializable` too —
its codec is the identity, so it works anywhere a serializable item is
expected.

An `interface class` would be tidier here — the item could keep whatever base
it had — and **Verilator compiles one without complaint**. It does not work:
`$cast` to an interface-class handle returns 0 at run time even for an object
whose class declares `implements`, so every decode fails with "does not
implement". This is a compile-clean, run-time-only failure, so it will not show
up until stimulus flows. Use a virtual base class.

and the test names the type with a **string**:

```systemverilog
pyhdl_raw_seq seq = pyhdl_raw_seq::type_id::create("seq");
seq.pyclass   = "my_runner::MySeq";
seq.item_type = "tcp_item";      // UVM factory name
seq.start(any_sequencer);
```

#### Why one sequence serves every item type

The instinct is `pyhdl_raw_seq #(type REQ)`, and it is worth understanding why
that is both impossible *and* unnecessary here:

- **Impossible.** A parameterized proxy needs the self-referential helper
  `helper #(REQ) extends imp_impl #(helper #(REQ))`, the shape that makes
  V3Param abort (§4.1) — the bug behind `tcp_py_seq.sv`.
- **Unnecessary.** `uvm_sequence`'s `REQ` defaults to `uvm_sequence_item`,
  `start()` takes a `uvm_sequencer_base`, and
  `uvm_sequencer_param_base::send_request()` `$cast`s the item **at run time**.
  So an unparameterized sequence drives a `uvm_sequencer #(tcp_item)` correctly,
  provided the object it sends really is a `tcp_item` — which
  `create_object_by_name()` guarantees.

Do not let the first bullet imply per-type code, which is the mistake this
section was originally written around. Parameterization being blocked is not a
reason to hand-write anything; runtime dispatch replaces it entirely.

One gotcha when wiring an item up: the argument type must be *the same typedef*
as the interface's, not merely the same shape, or the override will not match.
This repo does `typedef pyhdl_raw_byte_q_t tcp_byte_q_t;`.

Three things are worth knowing before reaching for it:

- **It fixes shape, not size.** The same total bytes still cross in one
  `pack_ints()`, so `UVM_MAX_STREAMBITS` (4096 bits) still bounds the item.
- **`start_item`/`finish_item` must name the same handle.** Python only holds
  the raw item, so the decoded one is cached between the two calls. Decoding
  twice hands UVM a different object than the one it arbitrated for.
- **You lose field-level checking at the boundary, not end to end.** Nothing on
  the Python side names a field, but the driver re-serializes the reconstructed
  item, so a field that decoded wrong still shows up as a byte mismatch
  downstream. Keep a transport scoreboard if you use this path.

Use a **virtual codec class, not a `#(type REQ)` type parameter.** A
parameterized proxy needs a self-referential helper declaration, which is the
shape that makes Verilator's V3Param abort (§4.1) — the same bug that forced
`tcp_py_seq.sv` to be hand-specialized. Runtime polymorphism sidesteps it
entirely, compiles to one copy, and lets the codec be chosen per sequence
instance.

---

## 6. Debugging effectively

### 6.1 Logging, not `print`

Use `logging` rather than `print` — you get levels, a prefix that tests can
grep, and a single place to fix the two problems below. Do **not** call
`logging.basicConfig` directly; use `sim_logging.configure`, which returns a
logger and installs the handler that solves both:

```python
import sim_logging
logger = sim_logging.configure(lambda: SimClock.inst().now_ns(), name="ether")
```

Then a pytest assertion can be as simple as `assert "PY_ERROR" not in output`.

**Problem 1: the two halves of the log do not interleave.** Python and the
simulator share a process but not an output buffer. SystemVerilog `$display`
goes through C stdio, which block-buffers as soon as stdout is a pipe — which
is always, under pytest. Python's `logging` writes through its own `io` layer.
Neither knows about the other, so kilobytes of UVM output can land in the pipe
long after the Python lines that were emitted between them. `logging` flushing
*its own* handler does not help; the stale data is in the simulator's buffer.

The fix is to drain C stdio immediately before each Python record. Because both
languages live in one process, `ctypes` can reach libc directly:

```python
_fflush = ctypes.CDLL(None).fflush
...
def emit(self, record):
    _fflush(None)          # NULL => flush every open C stream
    super().emit(record)
```

Ordering is then exact: SV writes A to its buffer, Python drains A and appends
its own line, SV writes B, and so on.

Two things have to be true for this to hold. Log to **stderr**, which Python
keeps line-buffered even when redirected — routing records to a block-buffered
stdout reintroduces the problem one layer up. And have the *harness* fold the
child's stderr into its stdout so both land in one pipe:

```python
subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
```

Capturing the two streams separately and concatenating them afterwards — the
obvious thing to write — produces a file with every SV line before every Python
line no matter when each was emitted. Flushing cannot order writes that go to
two different pipes.

**Problem 2: there is no shared clock.** Wall-clock timestamps are meaningless
here, since a millisecond of simulation takes seconds to run. Simulation time is
the only clock both sides agree on, so stamp every record with it, in the same
`@ <n>ns` form UVM uses:

```
UVM_INFO tcp_test.sv(198) @ 202835ns: uvm_test_top [uvm_test_top] handshake segments: 2 A->B, 1 B->A
PY_INFO @ 200835ns: [tcp] after handshake: A=ESTABLISHED B=ESTABLISHED
```

`sim_logging` does this with a `logging.Filter` that calls a time source you
supply, so it is re-read per record rather than baked in at call sites. Pass a
*callable*, not a value. The source is allowed to fail or return `None` — before
the time service is wired up, and again after `$finish` — and is reported as
`@ ?ns` rather than being allowed to raise. Logging must never take down the run
it is reporting on.

### 6.2 Never use bare `assert` in a runner

A raised `AssertionError` crossing the DPI boundary tends to surface as a silent
hang or truncated log. Count and log instead, and return the count to SV:

```python
def _err(msg):
    _State.errors += 1
    logger.error(msg)

@hif.exp
def report(self) -> int:
    return _State.errors            # SV turns non-zero into a `uvm_error
```

Wrap every sequence body in `try/except` and log the exception — otherwise a
Python-side error looks exactly like a hang:

```python
async def body(self):
    try:
        ...
    except Exception as e:
        _err(f"{type(self).__name__} raised: {type(e).__name__}: {e}")
```

### 6.3 Make the invariants self-checking

The failures in §4.4 and §5.3 are silent-corruption failures — the simulation
runs and gives wrong answers. Put an explicit probe in the smoke test for each:

- fresh item packs all-zero (field transport is aligned)
- an all-zero payload survives a queue round-trip (element width is pinned)
- `wait_ns(100)` advances `now_ns()` by exactly 100

Each is three lines and turns a week of confusion into an immediate failure.

### 6.4 Bisect the stack

When a co-simulation misbehaves, decide *which layer* first — they fail very
differently:

| Layer | How to test it | Typical symptom |
|---|---|---|
| Python model alone | plain `pytest`, no simulator | ordinary Python traceback |
| Boundary marshalling | round-trip a value and compare | wrong values, no error |
| Time service | `wait_ns` then read `now_ns` | hangs, or time does not move |
| SV transport | drive canned data, check the monitor | scoreboard mismatch |
| Full stack | the real test | any of the above |

This repo's TCP testbench is layered that way on purpose: `tcp_smoke_test`
(boundary + time), `tcp_transport_test` (SV transport, no engines),
`tcp_handshake_test` (engines, minimal traffic), then the data tests.

### 6.5 Reading a Verilator failure

- `%Error-DIDNOTCONVERGE` → almost always §1. Look for overlapping imp tasks
  before suspecting your RTL.
- `%Error: Internal Error: ... V3Param.cpp` → a parameterized-class construct
  Verilator cannot elaborate; specialize it by hand.
- `undefined reference to pyhdl_*` at link → §4.3.
- A `-Wno-` flag that silences an error but produces broken C++ → the warning
  was load-bearing. Fix the source construct instead.

### 6.6 Waves and verbosity

```bash
pytest --waves              # VCD from every testbench that supports it
+UVM_VERBOSITY=UVM_HIGH     # per-run, on the simulation binary
+UVM_TESTNAME=<test>        # select a UVM test from a shared binary
```

Driver and monitor log every transaction at `UVM_HIGH`, so raising verbosity
gives a transaction trace without a rebuild. `+pyhdl.debug=1` enables
pyhdl-if's own tracing.

### 6.7 Build once, run many

Compiling a UVM + pyhdl-if binary takes minutes. Select tests with
`+UVM_TESTNAME` and stimulus with plusargs, and compile once per pytest module:

```python
@pytest.fixture(scope="module")
def tcp_cfg(pytestconfig):
    cfg, comp = compile_sim(CORE, waves=pytestconfig.getoption("--waves"))
    assert comp.returncode == 0
    return cfg
```

Five TCP tests share one compile: 3m34s total instead of ~20 minutes.

---

## 7. Symptom index

| Symptom | Cause | Section |
|---|---|---|
| `%Error-DIDNOTCONVERGE` | two imp tasks in flight | §1 |
| Hang at time 0 | async imp call at t=0 | §2 |
| First byte of every burst lost | mixed clocking-block / raw sampling | §3 |
| `V3Param: Couldn't find pin in clone list` | shipped sequence proxy | §4.1 |
| `SUPERNFIRST` in pyhdl_uvm files | `pyhdl_uvm_type_utils` macro | §4.2 |
| `undefined reference to pyhdl_call_if_*` | DPI exports in an archive | §4.3 |
| Fresh item packs non-zero | UVM version vs packer model | §4.4 |
| `UVM/BASE/PACKER/UNPACK/N2NN` | same | §4.4 |
| Queue always unpacks empty | `use_metadata` not set | §5.2 |
| Queue values corrupt / width varies | element width inferred | §5.3 |
| Item's shape cannot be mirrored at all | — use the raw-bytes path | §5.5 |
| Scoreboard always miscompares | `uvm_object::compare()` on handles | §5.4 |
| Silent hang, no output | bare `assert` or unhandled exception | §6.2 |
| All Python log lines trail all SV lines | streams captured separately, C stdio unflushed | §6.1 |

---

## 8. Environment and build

- **PYTHONPATH** must cover the venv's `site-packages`, the shared
  `src/verif/pyhdl/` directory, and the testbench's own directory.
  `tests/conftest.py` assembles this for any core whose name contains `pyhdl`.
- Compile and link the pyhdl-if DPI library into every image that uses it
  (`-lpyhdl_if`, `-Wl,-rpath,...`, `-Wl,--export-dynamic`).
- A core whose name contains both `uvm` and `pyhdl` gets `UVM_ROOT` *and* the
  pyhdl environment from `conftest.py` with no extra wiring — hence
  `tb_tcp_uvm_pyhdl`.
- Keep timescales consistent. The time service assumes `1ns` units so `$time`
  and `#delay` are nanoseconds.

## 9. Upstream issues worth reporting

Three of the workarounds above exist because of bugs in dependencies, not in
this repo. They are worked around in-tree, which means **they will drift if the
dependency is upgraded** — re-check each on a version bump. None has been filed
upstream yet.

| # | Project | Issue | Worked around in |
|---|---|---|---|
| 1 | pyhdl-if | `pyhdl_uvm_sequence_proxy #(REQ)`'s self-parameterized helper does not elaborate under Verilator (`V3Param: Couldn't find pin in clone list`) | `tcp/tcp_py_seq.sv` (§4.1) |
| 2 | pyhdl-if | `pyhdl_uvm_type_utils` emits `T impl = new(obj); super.new(impl);`, which Verilator rejects; the `` `ifdef VCS `` alternative does not parse either | `tcp/pyhdl_uvm_vlt.sv` (§4.2) |
| 3 | pyhdl-if | Python packer model tracks UVM 1.2's `pack_ints`; misaligned against 1800.2-2020 | asserted, not worked around (§4.4) |

A fourth is arguably a Verilator packaging issue rather than a bug: the
`verilator` PyPI wheel ships `verilated.mk` with its compiler-configuration
variables empty. `scripts/setup_env.sh` repairs it.

Queue element-width inference (§5.3) is a *design* choice upstream rather than a
bug, but it is worth raising: inferring a width from data means the same field
serializes differently from one transaction to the next.

## 10. Reference implementations in this repo

| Want to see… | Look at |
|---|---|
| Minimal Call API, no UVM | `src/verif/pyhdl/counter/` |
| Byte-stream stimulus from Python | `src/verif/pyhdl/ether/` |
| UVM proxy sequences, two agents | `src/verif/pyhdl/tcp/` |
| Serializing SV calls, time pump | `tcp/test_runner.py` (`sv_lock`, `TimeMux`) |
| Verilator workarounds | `tcp/pyhdl_uvm_vlt.sv`, `tcp/tcp_py_seq.sv` |
| Queue element widths | `src/verif/pyhdl/uvm_mirror.py` |
| Logs that interleave with SV | `src/verif/pyhdl/sim_logging.py` |
| Raw-bytes item transport | `src/verif/pyhdl/pyhdl_raw.sv`, `raw_mirror.py` |
