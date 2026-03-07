# PyHDL-IF Best Practices and Limitations

This document captures key learnings and best practices for integrating Python and SystemVerilog using the `pyhdl-if` library, specifically in a Verilator-based environment.

## 1. Python Method Mapping: `async def` vs `def`

The mapping from Python to SystemVerilog depends on how the Python method is defined:

- **`def`** -> **SystemVerilog `function`**:
    - Executes instantaneously in simulation time.
    - **Best for**: Printing, logging, reading/writing configuration registers, or any operation that does not need to wait for simulator events.
    - **Example**: `@hif.exp def log_message(self, msg): ...`

- **`async def`** -> **SystemVerilog `task`**:
    - Can consume simulation time (blocking).
    - **Best for**: Stimulus sequences that wait for clocks, resets, or specific bus events.
    - **Requirement**: Must eventually `await` something (e.g., a simulator event or another async call).
    - **Warning**: Calling an `async` method (SV task) at **simulation time 0** can lead to deadlocks in Verilator. The library's internal polling mechanism might not yield correctly at the start of the simulation.

## 2. Managing Verilator Convergence (`%Error-DIDNOTCONVERGE`)

Verilator is highly sensitive to combinational loops and NBA (Non-Blocking Assignment) oscillations. When using virtual interfaces:

- **Use Clocking Blocks**: Always define a `clocking` block in your `interface`. Drive and sample signals through the clocking block (`vif.cb.signal <= value;`) to ensure well-defined synchronization points.
- **Specify Modports**: In your verification classes, declare the virtual interface with a specific modport (e.g., `virtual my_if.tb vif;`). This provides explicit directionality to Verilator.
- **Avoid zero-delay loops**: Connecting DUT outputs directly to `logic` signals in an interface monitored by a testbench can sometimes trigger convergence errors. Using `wire` for DUT outputs in the interface or sampling via a clocking block resolves this.

## 3. Output Visibility and Flushing

Python's `stdout` can be buffered when running inside a simulator process. To ensure logs appear in real-time or in the correct order:

- Use `flush=True` in `print()` calls: `print("Message", flush=True)`.
- Or call `sys.stdout.flush()` explicitly.

## 4. Environment and Build

- **PYTHONPATH**: Ensure `PYTHONPATH` includes both the `site-packages` of your virtual environment and the project's Python source directory.
- **Timescales**: Ensure all files (especially generated packages) have consistent timescales, or use Verilator flags to suppress warnings related to missing timescales.

## 5. Async Integration Bridge

When Python needs to call back into SystemVerilog (e.g., Python `await api.run_test()` calling an SV task):

- An implementation class in SV must implement the generated `_imp_if` interface.
- A proxy object (`TestAPI_imp_impl`) must be instantiated and its `m_obj` passed to the Python side.
- The SV task being called is executed in a forked process by the `pyhdl-if` library.
