VERILATOR = verilator
VERILATOR_FLAGS = --binary --trace -Wall -j 16
MISC_DIR = ./src/misc
RTL_DIR = ./src/rtl
VERIF_DIR = ./src/verif
BUILD_DIR = build

# Tools
PYTHON = ./bin/python3

# Select test (default to basic)
TEST ?= basic

ifeq ($(TEST),uvm)
    TOP_MODULE = tb_counter_uvm
    VERIF_SUBDIR = uvm

    # UVM package
    UVM_ROOT ?= /home/abaji/tools/uvm-1.2/src
    VERILATOR_FLAGS += +incdir+$(UVM_ROOT) +define+UVM_NO_DPI
    VERILATOR_FLAGS += -Wno-fatal -Wno-DECLFILENAME -Wno-IMPORTSTAR -Wno-WIDTHTRUNC -Wno-UNUSEDSIGNAL -Wno-UNSIGNED -Wno-LITENDIAN -Wno-VARHIDDEN -Wno-TIMESCALEMOD
    SRCS += $(UVM_ROOT)/uvm_pkg.sv

    # Verif packages
    VERILATOR_FLAGS += +incdir+$(VERIF_DIR)/$(VERIF_SUBDIR)
    SRCS += $(VERIF_DIR)/$(VERIF_SUBDIR)/counter_verif_pkg.sv
else
    TOP_MODULE = tb_counter
    VERIF_SUBDIR = basic
    VERILATOR_FLAGS += -Wno-fatal -Wno-UNUSEDSIGNAL
endif

SRCS += $(RTL_DIR)/counter.sv $(VERIF_DIR)/$(VERIF_SUBDIR)/$(TOP_MODULE).sv

SIM_EXE = ./$(BUILD_DIR)/obj_dir/V$(TOP_MODULE)

.PHONY: all compile sim clean format lint

all: compile sim

compile: $(SRCS)
	mkdir -p $(BUILD_DIR)
	$(VERILATOR) $(VERILATOR_FLAGS) --Mdir $(BUILD_DIR)/obj_dir --top-module $(TOP_MODULE) $(SRCS) -I$(RTL_DIR) -I$(BUILD_DIR) 2>&1 | tee $(BUILD_DIR)/build.log

sim: compile
	$(SIM_EXE) 2>&1 | tee $(BUILD_DIR)/sim.log

format:
	verible-verilog-format --flagfile $(MISC_DIR)/verible/.verible-verilog-format.flags --inplace $(SRCS)

lint:
	verible-verilog-lint --rules_config $(MISC_DIR)/verible/.rules.verible_lint $(SRCS)

clean:
	rm -rf $(BUILD_DIR)
