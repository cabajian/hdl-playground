// tcp_time_service: SV implementation of the Python-facing time service.
// Python's SimScheduler (via the TimeMux adapter) sees simulation time through
// exactly two calls: now_ns() and wait_ns(). This file's timescale must be
// 1ns so $time and # delays are in nanoseconds.
class tcp_time_service implements TimeServiceAPI_imp_if;

   TimeServiceAPI_imp_impl #(tcp_time_service) api;

   function new();
      api = new(this);
   endfunction

   virtual function longint now_ns();
      return longint'($time);
   endfunction

   virtual task wait_ns(input longint delay_ns);
      if (delay_ns > 0) #(delay_ns);
   endtask

endclass
