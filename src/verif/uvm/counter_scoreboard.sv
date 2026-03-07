
   class counter_scoreboard extends uvm_scoreboard;
      `uvm_component_utils(counter_scoreboard)

      uvm_analysis_imp#(counter_transaction, counter_scoreboard) item_collected_export;
      
      logic [3:0] expected_count = 0;
      bit first_active = 0;

      function new(string name, uvm_component parent);
         super.new(name, parent);
         item_collected_export = new("item_collected_export", this);
      endfunction

      virtual function void write(counter_transaction tr);
         if (!tr.rst_n) begin
            expected_count = 0;
            first_active = 0;
         end else begin
             // Check count against expectation
             if (first_active && tr.count !== expected_count) begin
                `uvm_error("SB", $sformatf("Mismatch! Seen: %0h, Expected: %0h", tr.count, expected_count))
             end else begin
                `uvm_info("SB", $sformatf("Match: %0h", tr.count), UVM_HIGH)
             end

             // Calculate NEXT expectation
             if (tr.wr_en) begin
                 expected_count = tr.data;
             end else begin
                 expected_count = (tr.count + 1) & 4'hF;
             end

             first_active = 1;
         end
      endfunction
   endclass
