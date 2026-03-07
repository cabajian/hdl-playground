package counter_test_pkg;
    import tb_counter_pyhdl_api_pkg::*;

    class counter_test implements TestAPI_imp_if;

        virtual counter_if.tb vif;
        TestAPI_imp_impl #(counter_test) api;

        function new(virtual counter_if.tb vif);
            this.vif = vif;
            this.api = new(this);
        endfunction

        virtual task write_count(bit [3:0] count);
            vif.cb.wr_en  <= 1;
            vif.cb.data_i <= count;
            @(vif.cb);
            vif.cb.wr_en  <= 0;
        endtask

        virtual task read_count(output bit [3:0] count);
            vif.cb.wr_en <= 0;
            count = vif.cb.data_o;
        endtask

        virtual task check_count(bit [3:0] exp, bit [3:0] act);
            if (act != exp) $error("...check failed! Expected %h, got %h", exp, act);
        endtask

        virtual task run_test(byte unsigned starting_count);
          // Initialize inputs (using cb for synchronous signals)
          vif.cb.rst_n  <= 0;
          vif.cb.wr_en  <= 0;
          vif.cb.data_i <= 0;
          repeat (2) @(vif.cb);
          vif.cb.rst_n <= 1;
          
          // Seed initial count
          write_count(starting_count[3:0]);
          
          // 1. Verify simple counting
          $display("[%0t] Testing simple counting...", $time);
          for (int i = 0; i < 16; i++) begin
            check_count(4'(starting_count[3:0]+i), vif.cb.count);
            @(vif.cb);
          end

          // 2. Register Read Test
          write_count(0);
          $display("[%0t] Testing counter reading...", $time);
          for (int i = 0; i < 16; i++) begin
            bit [3:0] exp = 4'(i), act;
            read_count(act);
            check_count(exp, act);
            @(vif.cb);
          end

          $display("[%0t] Test finished.", $time);
        endtask

    endclass
endpackage