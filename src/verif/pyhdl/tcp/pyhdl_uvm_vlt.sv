// Entry point for pyhdl-if's shipped UVM layer, adapted so that it builds
// under the simulator used by this repo. (No comment line here may begin
// with that simulator's name -- it would be parsed as a lint pragma.)
//
// pyhdl-if's `pyhdl_uvm_type_utils` macro builds each wrapper class with
//
//     function new(uvm_object obj);
//         uvm_w_t impl = new(obj);   // declaration whose initializer is a ctor call
//         super.new(impl);
//     endfunction
//
// which 5.48 rejects (SUPERNFIRST): the initializer counts as a statement
// preceding super.new. Suppressing the diagnostic does not help -- the
// generated C++ then references `impl` before declaring it. The macro's
// `ifdef VCS variant, `super.new(uvm_w_t::new(obj))`, is a VCS-ism that
// fails to parse here.
//
// A static factory keeps super.new the first statement and compiles cleanly
// on all three, so this file re-defines the macro with that shape before
// pulling in the library. The include guard in pyhdl_uvm_macros.svh makes the
// redefinition stick: pyhdl_uvm.sv's own include of it becomes a no-op.
//
// Everything else in the library is used verbatim. Drop this file and list
// $PYHDL_IF_SHARE/uvm/pyhdl_uvm.sv directly once upstream carries the fix.

`include "uvm_macros.svh"
`include "pyhdl_if_macros.svh"
`include "pyhdl_uvm_macros.svh"

// Name is fixed by upstream pyhdl-if: this must shadow their macro exactly.
// verilog_lint: waive-start macro-name-style
`undef pyhdl_uvm_type_utils
`define pyhdl_uvm_type_utils(uvm_t, uvm_w_t, base_t, base_w_t) \
    class uvm_w_t``_w extends uvm_t``_imp_impl #(uvm_w_t) implements pyhdl_uvm_object_if; \
    \
        static function uvm_w_t __vlt_mk_impl(uvm_object obj); \
            uvm_w_t impl = new(obj); \
            return impl; \
        endfunction \
    \
        function new(uvm_object obj); \
            super.new(__vlt_mk_impl(obj)); \
        endfunction \
    \
        virtual function uvm_object get_object(); \
            return m_impl.m_uvm_obj; \
        endfunction \
    \
        virtual function PyObject get_pyobject(); \
            return m_obj; \
        endfunction \
    \
    endclass \
    \
    static pyhdl_uvm_object_type_rgy __type_``uvm_t``_``base_t`` = pyhdl_uvm_object_type_rgy_p #( \
        uvm_t, \
        uvm_w_t``_w, \
        base_t, \
        base_w_t``_w)::inst(`"uvm_t`", `"base_t`");

// verilog_lint: waive-stop macro-name-style

`include "pyhdl_uvm.sv"
