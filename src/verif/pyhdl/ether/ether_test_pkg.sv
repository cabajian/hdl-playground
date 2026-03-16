package ether_test_pkg;
   import pyhdl_if::*;
   import tb_ether_pyhdl_api_pkg::*;

   typedef byte unsigned byte_q_t[$];

   interface class serializable_object;
      pure virtual function byte_q_t to_bytes();
      pure virtual function void from_bytes(byte_q_t data);
   endclass

   // -----------------------------------------------------------------------
   // ether_object: Pure SV data class for Ethernet frames.
   // Provides to_bytes()/from_bytes() using a byte queue (no Python dependency).
   // -----------------------------------------------------------------------
   class ether_object implements serializable_object;

      bit [47:0]    dst_mac;
      bit [47:0]    src_mac;
      bit [15:0]    ethertype;
      byte unsigned payload[$];

      // Serialize fields into a byte queue in network order:
      //   dst_mac[6] + src_mac[6] + ethertype[2] + payload[N]
      virtual function byte_q_t to_bytes();
         byte_q_t data;
         // DST MAC (MSB first)
         for (int i = 5; i >= 0; i--) data.push_back(dst_mac[i*8+:8]);
         // SRC MAC (MSB first)
         for (int i = 5; i >= 0; i--) data.push_back(src_mac[i*8+:8]);
         // EtherType (MSB first)
         for (int i = 1; i >= 0; i--) data.push_back(ethertype[i*8+:8]);
         // Payload
         foreach (payload[i]) data.push_back(payload[i]);
      endfunction

      // Populate fields from a byte queue in network order.
      virtual function void from_bytes(byte_q_t data);
         int idx = 0;
         // DST MAC (6 bytes, MSB first)
         for (int i = 5; i >= 0; i--) dst_mac[i*8+:8] = data[idx++];
         // SRC MAC (6 bytes, MSB first)
         for (int i = 5; i >= 0; i--) src_mac[i*8+:8] = data[idx++];
         // EtherType (2 bytes, MSB first)
         for (int i = 1; i >= 0; i--) ethertype[i*8+:8] = data[idx++];
         // Payload (remaining bytes)
         payload.delete();
         while (idx < data.size()) payload.push_back(data[idx++]);
      endfunction

   endclass

   // -----------------------------------------------------------------------
   // py_serial_object: Bridges serializable_object <-> Python lists via pyhdl-if.
   // to_bytes()  : serializable_object -> byte queue -> PyObject (Python list)
   // from_bytes(): PyObject (Python list) -> byte queue -> serializable_object
   // -----------------------------------------------------------------------
   class py_serial_object;

      serializable_object obj;

      // Convert serializable_object fields to a PyObject (Python list of ints).
      // Caller is responsible for disposing the returned py_object.
      function py_object to_bytes();
         byte unsigned data[$];
         py_list lst;

         data = obj.to_bytes();
         lst  = new();
         foreach (data[i]) lst.append_obj(PyLong_FromLong(longint'(data[i])));

         return lst;
      endfunction

      // Populate serializable_object from a PyObject (Python list of ints).
      function void from_bytes(PyObject py_list_obj);
         int num_bytes;
         byte unsigned data[$];
         py_object item;

         num_bytes = int'(PyList_Size(py_list_obj));

         for (int i = 0; i < num_bytes; i++) begin
            item = py_object::mk(PyList_GetItem(py_list_obj, i));
            data.push_back(item.as_int() [7:0]);
         end

         obj.from_bytes(data);
      endfunction

   endclass

   // -----------------------------------------------------------------------
   // pyhdl_ether_test: Test harness that monitors DUT outputs and drives
   // packets via the virtual interface.
   // -----------------------------------------------------------------------
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
               py_serial_object s = new();
               ether_object eth = new();
               py_object py_data;

               $display("[%0t] SV Monitor: Captured valid packet with payload_len=%0d", $time,
                        payload_len);

               // Populate ether_object from interface signals
               eth.dst_mac   = vif.cb.o_dst_mac;
               eth.src_mac   = vif.cb.o_src_mac;
               eth.ethertype = vif.cb.o_ethertype;
               for (int i = 0; i < payload_len; i++) begin
                  eth.payload.push_back(vif.cb.o_payload[(1499-i)*8+:8]);
               end

               // Serialize and send to Python
               s.obj   = eth;
               py_data = s.to_bytes();

               $display("[%0t] SV Monitor: Forwarding reconstructed packet back to Python", $time);
               py_runner.set_sim_time(longint'($time));
               py_runner.check_packet(py_data.borrow());
               py_data.dispose();
            end
         end
      endtask

      // The python-facing API call to drive an entire packet representation in
      virtual task drive(PyObject packet);
         py_serial_object s = new();
         ether_object eth = new();
         byte unsigned data[$];
         int num_bytes;

         s.obj = eth;
         s.from_bytes(packet);
         data = eth.to_bytes();
         num_bytes = data.size();

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
            vif.cb.valid <= 1'b1;
            vif.cb.start <= (i == 0) ? 1'b1 : 1'b0;
            vif.cb.num_bytes <= num_bytes;
            vif.cb.data <= data[i];

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
