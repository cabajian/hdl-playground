// sim_clock.svh
//
// Generic helpers for syncing SV `$time` into the Python-side simpy clock.
// Requires the per-testbench api-gen package (imports SimClockAPI_exp_if / _exp_impl).
//
// Usage in a testbench initial block, after pyhdl_if_start() and constructing
// a SimClockAPI_exp_impl handle:
//
//     automatic SimClockAPI_exp_impl py_clock = new();
//     `SIM_CLOCK_START(py_clock, 1000)   // default poll every 1000 sim units
//
// The poll interval may be overridden at runtime via +sim_clock.poll_ns=N.
// Pass 0 (either as default or plusarg) to disable the polling thread and
// rely solely on per-interaction advance_to calls.

`ifndef SIM_CLOCK_SVH
`define SIM_CLOCK_SVH

`define SIM_CLOCK_START(py_clock, default_poll_ns)                    \
   fork                                                               \
      begin                                                           \
         longint __sim_clock_poll_ns = default_poll_ns;               \
         void'($value$plusargs("sim_clock.poll_ns=%d",                \
                               __sim_clock_poll_ns));                 \
         if (__sim_clock_poll_ns > 0) begin                           \
            forever begin                                             \
               #(__sim_clock_poll_ns);                                \
               py_clock.advance_to(longint'($time));                  \
            end                                                       \
         end                                                          \
      end                                                             \
   join_none

`endif
