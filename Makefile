VERILATOR = verilator
VERILATOR_FLAGS = --binary --trace -Wall -j 8

MISC_DIR = ./src/misc
RTL_DIR = ./src/rtl
VERIF_DIR = ./src/verif
BUILD_DIR = build

# Tools
PYTHON = ./bin/python3
REGTOOL = /home/abaji/tools/opentitan/util/regtool.py
HJSON = src/data/counter.hjson
RAL_PKG = $(BUILD_DIR)/counter_ral_pkg.sv

# Select test (default to basic)
TEST ?= basic

ifeq ($(TEST),uvm)
    TOP_MODULE = tb_counter_uvm
    VERIF_SUBDIR = uvm
    # Verilator UVM flags (manual setup)
    UVM_ROOT ?= /home/abaji/tools/uvm-1.2/src
    VERILATOR_FLAGS += +incdir+$(UVM_ROOT) $(UVM_ROOT)/uvm_pkg.sv +define+UVM_NO_DPI
    
    # OpenTitan base packages for RAL (using local mock)
    VERIF_UVM_DIR = ./src/verif/uvm
    
    VERILATOR_FLAGS += +incdir+$(VERIF_UVM_DIR)
    
    SRCS += $(VERIF_UVM_DIR)/dv_base_reg_pkg.sv
    SRCS += $(VERIF_UVM_DIR)/dv_lib_pkg.sv
    
    # Suppress UVM-related warnings that are common with Verilator
    VERILATOR_FLAGS += -Wno-fatal -Wno-DECLFILENAME -Wno-IMPORTSTAR -Wno-WIDTHTRUNC -Wno-UNUSEDSIGNAL -Wno-UNSIGNED -Wno-LITENDIAN -Wno-VARHIDDEN -Wno-TIMESCALEMOD
    # Include RAL package in sources only for UVM test
    SRCS += $(RAL_PKG)
    SRCS += $(VERIF_UVM_DIR)/counter_verif_pkg.sv
else
    TOP_MODULE = tb_counter
    VERIF_SUBDIR = basic
    # Suppress warnings that are common with Verilator to match UVM flow
    VERILATOR_FLAGS += -Wno-fatal -Wno-UNUSEDSIGNAL
endif

SRCS += $(RTL_DIR)/counter.sv $(VERIF_DIR)/$(VERIF_SUBDIR)/$(TOP_MODULE).sv

SIM_EXE = ./$(BUILD_DIR)/obj_dir/V$(TOP_MODULE)

.PHONY: all compile sim clean format lint

all: compile sim

$(RAL_PKG): $(HJSON)
	mkdir -p $(BUILD_DIR)
	$(PYTHON) $(REGTOOL) -s -t $(BUILD_DIR) $<
	sed -i '1i `timescale 1ns/1ps' $(RAL_PKG)
	rm -f $(BUILD_DIR)/counter_ral_pkg.core

compile: $(RAL_PKG) $(SRCS)
	mkdir -p $(BUILD_DIR)
	$(VERILATOR) $(VERILATOR_FLAGS) --Mdir $(BUILD_DIR)/obj_dir --top-module $(TOP_MODULE) $(SRCS) -I$(RTL_DIR) -I$(BUILD_DIR)

sim: compile
	$(SIM_EXE) 2>&1 | tee $(BUILD_DIR)/sim.log

format:
	verible-verilog-format --flagfile $(MISC_DIR)/verible/.verible-verilog-format.flags --inplace $(SRCS)

lint:
	verible-verilog-lint --rules_config $(MISC_DIR)/verible/.rules.verible_lint $(SRCS)

clean:
	rm -rf $(BUILD_DIR)
