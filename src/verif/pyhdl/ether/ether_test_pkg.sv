package ether_test_pkg;
   import pyhdl_if::*;
   import tb_ether_pyhdl_api_pkg::*;

   class pyhdl_ether_test implements TestAPI_imp_if;

      virtual ether_if.tb vif;
      TestAPI_imp_impl #(pyhdl_ether_test) api;
      TestRunnerAPI_exp_if py_runner;

      protected
      function new(virtual ether_if.tb vif, TestRunnerAPI_exp_if py_runner);
         this.vif = vif;
         this.py_runner = py_runner;
         this.api = new(this);
      endfunction

      static function pyhdl_ether_test mk(virtual ether_if.tb vif, TestRunnerAPI_exp_if py_runner);
         pyhdl_ether_test t = new(vif, py_runner);

         // Fork a background process to monitor outputs
         fork
            t.monitor();
         join_none

         return t;
      endfunction

      // Task to monitor the o_valid output independently of driving inputs
      virtual task monitor();

         forever begin
            // Wait for a valid output
            @(vif.cb);
            if (vif.cb.o_valid == 1'b1) begin
               int payload_len = int'(vif.cb.o_payload_bytes);
               py_object py_list;
               byte unsigned b;
               $display("[%0t] SV Monitor: Captured valid packet with payload_len=%0d", $time,
                        payload_len);

               // Initialize a new empty Python list to pack fields back into byte representation
               py_list = py_object::mk(PyList_New(0));

               // Header
               /* 1. Pack DST MAC (6 bytes, MSB first) */
               for (int i = 5; i >= 0; i--) begin
                  b = vif.cb.o_dst_mac[i*8+:8];
                  void'(PyList_Append(py_list.borrow(), PyLong_FromLong(b)));
               end

               /* 2. Pack SRC MAC (6 bytes, MSB first) */
               for (int i = 5; i >= 0; i--) begin
                  b = vif.cb.o_src_mac[i*8+:8];
                  void'(PyList_Append(py_list.borrow(), PyLong_FromLong(b)));
               end

               /* 3. Pack EtherType (2 bytes, MSB first) */
               for (int i = 1; i >= 0; i--) begin
                  b = vif.cb.o_ethertype[i*8+:8];
                  void'(PyList_Append(py_list.borrow(), PyLong_FromLong(b)));
               end

               /* 
                    * 4. Pack Payload.
                    * o_payload[ (1499-0)*8 +: 8 ] is payload_mem[0] (first byte)
                    */
               for (int i = 0; i < payload_len; i++) begin
                  b = vif.cb.o_payload[(1499-i)*8+:8];
                  void'(PyList_Append(py_list.borrow(), PyLong_FromLong(b)));
               end

               // Call python function with full extracted byte array!
               $display("[%0t] SV Monitor: Forwarding reconstructed packet back to Python", $time);
               py_runner.set_sim_time(longint'($time));
               py_runner.check_packet(py_list.borrow());
               py_list.dispose();  // Release python handle
            end
         end
      endtask

      // The python-facing API call to push an entire packet representation in
      virtual task send_packet(PyObject packet);
         int num_bytes;
         py_object item;
         byte unsigned data_byte;

         num_bytes = int'(PyList_Size(packet));

         $display("[%0t] SV Driver: Starting to drive packet of size %0d bytes", $time, num_bytes);

         // Initial resets
         vif.cb.rst <= 0;
         vif.cb.start <= 0;
         vif.cb.valid <= 0;
         vif.cb.num_bytes <= 0;
         vif.cb.data <= 0;
         @(vif.cb);

         // Drive each byte synchronously
         for (int i = 0; i < num_bytes; i++) begin
            // Only needed for explicit memory management of new refs, but GetItem is borrowed, 
            // so we don't dispose the actual underlying Python object. mk() just wraps it safely.
            item = py_object::mk(PyList_GetItem(packet, i));  // Borrowed reference from List
            data_byte = item.as_int();

            vif.cb.valid <= 1'b1;
            vif.cb.start <= (i == 0) ? 1'b1 : 1'b0;
            vif.cb.num_bytes <= num_bytes;
            vif.cb.data <= data_byte;

            @(vif.cb);
         end

         // Drop valid after transmission
         vif.cb.valid <= 1'b0;
         vif.cb.start <= 1'b0;
         @(vif.cb);  // Advance to give module time to settle o_valid

         // Give monitor thread time to catch it and wait a bit after
         repeat (5) @(vif.cb);
         $display("[%0t] SV Driver: Finished packet transmission sequence.", $time);
         py_runner.set_sim_time(longint'($time));
      endtask

   endclass
endpackage
