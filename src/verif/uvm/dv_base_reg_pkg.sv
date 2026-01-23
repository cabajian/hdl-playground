
`timescale 1ns/1ps
package dv_base_reg_pkg;
  import uvm_pkg::*;

  // Mock csr_excl_item
  class csr_excl_item extends uvm_object;
    `uvm_object_utils(csr_excl_item)
    function new(string name = "csr_excl_item");
      super.new(name);
    endfunction
  endclass

  // Mock dv_base_reg_field
  class dv_base_reg_field extends uvm_reg_field;
    `uvm_object_utils(dv_base_reg_field)
    function new(string name = "dv_base_reg_field");
      super.new(name);
    endfunction
    
    function void configure(uvm_reg        parent,
                           int            size,
                           int            lsb_pos,
                           string         access,
                           bit            volatile,
                           uvm_reg_data_t reset,
                           bit            has_reset,
                           bit            is_rand,
                           bit            individually_accessible,
                           string         mubi_access = "NONE"); // OpenTitan extra arg
       super.configure(parent, size, lsb_pos, access, volatile, reset, 
                       has_reset, is_rand, individually_accessible);
    endfunction

    function void set_original_access(string access);
      // Stub
    endfunction
  endclass

  // Mock dv_base_reg
  class dv_base_reg extends uvm_reg;
    `uvm_object_utils(dv_base_reg)
    function new(string name = "dv_base_reg", int unsigned n_bits = 32, int has_coverage = UVM_NO_COVERAGE);
      super.new(name, n_bits, has_coverage);
    endfunction
    virtual function void build(csr_excl_item csr_excl = null);
      // To be overridden
    endfunction
  endclass

  // Mock dv_base_reg_block
  class dv_base_reg_block extends uvm_reg_block;
    bit en_dv_reg_cov = 0; // Stub for generated code
    csr_excl_item csr_excl; // Restore name
    
    `uvm_object_utils(dv_base_reg_block)
    function new(string name = "dv_base_reg_block", int has_coverage = UVM_NO_COVERAGE);
      super.new(name, has_coverage);
    endfunction
    
    virtual function void build(uvm_reg_addr_t base_addr, csr_excl_item csr_excl = null);
      // To be overridden
    endfunction

    function void set_hdl_path_root(string path, string kind = "BkdrRegPathRtl");
      // Stub
    endfunction
    
    function void create_cov();
      // Stub
    endfunction
  endclass

endpackage
