// tcp_scoreboard: transport-level check. Every item driven on one side must
// appear byte-exact, in order, at the far side's monitor. Two independent
// compare streams (A->B and B->A) built from analysis FIFOs.
class tcp_scoreboard extends uvm_component;
   `uvm_component_utils(tcp_scoreboard)

   uvm_tlm_analysis_fifo #(tcp_item) drv_a_fifo;
   uvm_tlm_analysis_fifo #(tcp_item) mon_b_fifo;
   uvm_tlm_analysis_fifo #(tcp_item) drv_b_fifo;
   uvm_tlm_analysis_fifo #(tcp_item) mon_a_fifo;

   int unsigned n_checked_ab;
   int unsigned n_checked_ba;
   int unsigned n_errors;

   function new(string name, uvm_component parent);
      super.new(name, parent);
   endfunction

   virtual function void build_phase(uvm_phase phase);
      super.build_phase(phase);
      drv_a_fifo = new("drv_a_fifo", this);
      mon_b_fifo = new("mon_b_fifo", this);
      drv_b_fifo = new("drv_b_fifo", this);
      mon_a_fifo = new("mon_a_fifo", this);
   endfunction

   task run_phase(uvm_phase phase);
      fork
         compare_stream(drv_a_fifo, mon_b_fifo, "A->B", n_checked_ab);
         compare_stream(drv_b_fifo, mon_a_fifo, "B->A", n_checked_ba);
      join
   endtask

   task automatic compare_stream(uvm_tlm_analysis_fifo #(tcp_item) exp_fifo,
                                 uvm_tlm_analysis_fifo #(tcp_item) got_fifo, string tag,
                                 ref int unsigned n_checked);
      tcp_item exp, got;
      forever begin
         exp_fifo.get(exp);
         got_fifo.get(got);
         if (!got.compare(exp)) begin
            n_errors++;
            `uvm_error(get_name(), $sformatf("[%s] mismatch:\n  drove:    %s\n  observed: %s", tag,
                                             exp.convert2string(), got.convert2string()))
         end
         n_checked++;
      end
   endtask

   virtual function void report_phase(uvm_phase phase);
      super.report_phase(phase);
      `uvm_info(get_name(), $sformatf("transport checked: %0d A->B, %0d B->A, %0d errors",
                                      n_checked_ab, n_checked_ba, n_errors), UVM_LOW)
   endfunction
endclass
