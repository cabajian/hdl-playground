package ether_test_pkg;
   import pyhdl_if::*;
   import tb_ether_pyhdl_api_pkg::*;

   typedef byte unsigned byte_q_t[$];

      interface class serializable_object; pure virtual
      function byte_q_t to_bytes()
      ; pure virtual
      function void from_bytes(byte_q_t data)
      ;
      endclass

   // -----------------------------------------------------------------------
   // ether_object: Pure SV data class for Ethernet frames.
   // Provides to_bytes()/from_bytes() using a byte queue (no Python dependency).
   // The serialized form is the on-the-wire frame body -- no preamble, no FCS --
   // so it lines up byte-for-byte with scapy's bytes(pkt).
   // -----------------------------------------------------------------------
   class ether_object implements serializable_object;

      bit           [47:0] dst_mac;
      bit           [47:0] src_mac;
      bit           [15:0] ethertype;
      bit                  vlan_valid;
      bit           [15:0] vlan_tci;
      byte unsigned        payload    [$];

      // Serialize fields into a byte queue in network order:
      //   dst_mac[6] + src_mac[6] + [0x8100 + tci[2]] + ethertype[2] + payload[N]
      virtual function byte_q_t to_bytes();
         byte_q_t data;
         // DST MAC (MSB first)
         for (int i = 5; i >= 0; i--) data.push_back(dst_mac[i*8+:8]);
         // SRC MAC (MSB first)
         for (int i = 5; i >= 0; i--) data.push_back(src_mac[i*8+:8]);
         // Optional 802.1Q tag: TPID then TCI
         if (vlan_valid) begin
            data.push_back(8'h81);
            data.push_back(8'h00);
            data.push_back(vlan_tci[15:8]);
            data.push_back(vlan_tci[7:0]);
         end
         // EtherType (MSB first)
         for (int i = 1; i >= 0; i--) data.push_back(ethertype[i*8+:8]);
         // Payload
         foreach (payload[i]) data.push_back(payload[i]);
         return data;
      endfunction

      // Populate fields from a byte queue in network order.
      virtual function void from_bytes(byte_q_t data);
         int idx = 0;
         bit [15:0] tpid;
         // DST MAC (6 bytes, MSB first)
         for (int i = 5; i >= 0; i--) dst_mac[i*8+:8] = data[idx++];
         // SRC MAC (6 bytes, MSB first)
         for (int i = 5; i >= 0; i--) src_mac[i*8+:8] = data[idx++];
         // Peek the next 2 bytes: an 0x8100 TPID means a VLAN tag follows
         tpid = {data[idx], data[idx+1]};
         vlan_valid = (tpid == 16'h8100);
         if (vlan_valid) begin
            idx += 2;
            vlan_tci = {data[idx], data[idx+1]};
            idx += 2;
         end else begin
            vlan_tci = '0;
         end
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

      // Unpack a Python list of ints into a byte queue.
      static function byte_q_t py_list_to_bytes(PyObject py_list_obj);
         int num_bytes;
         byte_q_t data;
         py_object item;

         num_bytes = int'(PyList_Size(py_list_obj));
         for (int i = 0; i < num_bytes; i++) begin
            item = py_object::mk(PyList_GetItem(py_list_obj, i));
            data.push_back(item.as_int() [7:0]);
         end
         return data;
      endfunction

      // Convert serializable_object fields to a PyObject (Python list of ints).
      // Caller is responsible for disposing the returned py_object.
      function py_object to_bytes();
         byte unsigned data[$];
         py_list lst;
         PyObject py_long;

         data = obj.to_bytes();
         lst  = new();
         // PyList_Append increments the element's refcount, so we must drop
         // our own reference from PyLong_FromLong to avoid a slow leak.
         foreach (data[i]) begin
            py_long = PyLong_FromLong(longint'(data[i]));
            lst.append_obj(py_long);
            Py_DecRef(py_long);
         end

         return lst;
      endfunction

      // Populate serializable_object from a PyObject (Python list of ints).
      function void from_bytes(PyObject py_list_obj);
         obj.from_bytes(py_list_to_bytes(py_list_obj));
      endfunction

   endclass

   // -----------------------------------------------------------------------
   // pyhdl_ether_test: Test harness that streams raw bytes at the DUT and
   // forwards every extracted frame back to Python for comparison.
   // -----------------------------------------------------------------------
   class pyhdl_ether_test implements TestAPI_imp_if;

      virtual ether_if.tb vif;
      TestAPI_imp_impl #(pyhdl_ether_test) api;
      TestRunnerAPI_exp_if py_runner;
      SimClockAPI_exp_if py_clock;

      protected bit did_reset = 1'b0;

      protected
      function new(virtual ether_if.tb vif, TestRunnerAPI_exp_if py_runner,
                   SimClockAPI_exp_if py_clock);
         this.vif = vif;
         this.py_runner = py_runner;
         this.py_clock = py_clock;
         this.api = new(this);
      endfunction

      static function pyhdl_ether_test mk(virtual ether_if.tb vif, TestRunnerAPI_exp_if py_runner,
                                          SimClockAPI_exp_if py_clock);
         pyhdl_ether_test t = new(vif, py_runner, py_clock);

         // Fork a background process to monitor outputs
         fork
            t.monitor();
         join_none

         return t;
      endfunction

      // Monitor the payload stream and hand each completed frame to Python.
      virtual task monitor();
         byte unsigned pl[$];

         forever begin
            @(vif.cb);

            if (vif.cb.o_pl_valid === 1'b1) pl.push_back(vif.cb.o_pl_data);

            if (vif.cb.o_valid === 1'b1) begin
               py_serial_object s = new();
               ether_object eth = new();
               py_object py_data;

               $display("[%0t] SV Monitor: Extracted frame payload_bytes=%0d vlan=%0b fcs_ok=%0b",
                        $time, vif.cb.o_payload_bytes, vif.cb.o_vlan_valid, vif.cb.o_fcs_ok);

               if (vif.cb.o_fcs_ok !== 1'b1 || vif.cb.o_err_oversize === 1'b1) begin
                  $display("[%0t] SV Monitor: BAD FRAME fcs_ok=%0b oversize=%0b", $time,
                           vif.cb.o_fcs_ok, vif.cb.o_err_oversize);
               end

               // Rebuild the frame exactly as it appeared on the wire
               eth.dst_mac    = vif.cb.o_dst_mac;
               eth.src_mac    = vif.cb.o_src_mac;
               eth.ethertype  = vif.cb.o_ethertype;
               eth.vlan_valid = vif.cb.o_vlan_valid;
               eth.vlan_tci   = vif.cb.o_vlan_tci;
               eth.payload    = pl;

               s.obj          = eth;
               py_data        = s.to_bytes();

               $display("[%0t] SV Monitor: Forwarding reconstructed frame back to Python", $time);
               py_clock.advance_to(longint'($time));
               // The generated wrapper does PyTuple_SetItem(args, 0, ...) which
               // steals a ref. Hand it an owned ref via steal() so our own
               // dispose() below doesn't free the object while the (internally
               // retained) args tuple still references it.
               py_runner.check_packet(py_data.steal());
               py_data.dispose();

               pl.delete();
            end
         end
      endtask

      // Hold the DUT in reset for a few cycles, once, before the first stream.
      protected task reset();
         vif.cb.rst   <= 1'b1;
         vif.cb.valid <= 1'b0;
         vif.cb.data  <= 8'h00;
         repeat (4) @(vif.cb);
         vif.cb.rst <= 1'b0;
         @(vif.cb);
         did_reset = 1'b1;
      endtask

      // Python-facing API: stream an arbitrary run of bytes at the DUT. The
      // stream carries preamble/SFD, the frame, its FCS, and whatever
      // inter-frame filler Python decided to put around it.
      virtual task drive(PyObject packet);
         byte unsigned stream[$];

         if (!did_reset) reset();

         stream = py_serial_object::py_list_to_bytes(packet);

         $display("[%0t] SV Driver: Streaming %0d bytes", $time, stream.size());

         foreach (stream[i]) begin
            vif.cb.valid <= 1'b1;
            vif.cb.data  <= stream[i];
            @(vif.cb);
         end

         // Idle the stream and let the monitor retire the frame
         vif.cb.valid <= 1'b0;
         vif.cb.data  <= 8'h00;
         repeat (5) @(vif.cb);

         $display("[%0t] SV Driver: Finished streaming.", $time);
         py_clock.advance_to(longint'($time));
      endtask

   endclass
endpackage
