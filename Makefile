VERILATOR = verilator
VERILATOR_FLAGS = --binary --trace -Wall -j 0
ifdef WAVES
	VERILATOR_FLAGS += +define+WAVES +define+VCD_FILE=\"$(BUILD_DIR)/waves.vcd\"
endif
MISC_DIR = ./src/misc
RTL_DIR = ./src/rtl
VERIF_DIR = ./src/verif
BUILD_BASE = build
BUILD_DIR = $(BUILD_BASE)/$(TEST)

# Tools
PYTHON = ./bin/python3
PYHDL_IF = ./bin/pyhdl-if
VENV_SITE_PACKAGES := /home/abaji/projects/hardware/hdl-playground/lib/python3.12/site-packages
PYTHON_PATH := $(VENV_SITE_PACKAGES)

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
else ifeq ($(TEST),pyhdl)
	TOP_MODULE = tb_counter_pyhdl
	VERIF_SUBDIR = pyhdl
	PYTHON_PATH := $(PYTHON_PATH):$(VERIF_DIR)/$(VERIF_SUBDIR)

	VERILATOR_FLAGS += -Wno-fatal -Wno-UNUSEDSIGNAL

	# Python dependencies
	PYHDL_IF_SHARE = $(shell ./bin/pyhdl-if share)
	PYHDL_IF_LIBS = $(shell ./bin/pyhdl-if libs)
	PYHDL_IF_LIBS_DIR = $(shell dirname $(PYHDL_IF_LIBS))
	VERILATOR_FLAGS += +incdir+$(PYHDL_IF_SHARE)/dpi
	VERILATOR_FLAGS += +define+HAVE_PYHDL_IF
	VERILATOR_FLAGS += -LDFLAGS "-L$(PYHDL_IF_LIBS_DIR) -lpyhdl_if -Wl,-rpath,$(PYHDL_IF_LIBS_DIR) -Wl,--export-dynamic"
	SRCS += $(PYHDL_IF_SHARE)/dpi/pyhdl_if.sv
	
	# API Gen SV Package
	PY_API_PKG = $(BUILD_DIR)/$(TOP_MODULE)_api_pkg.sv
	SRCS += $(PY_API_PKG)
	VERILATOR_FLAGS += +incdir+$(BUILD_DIR)

	# New Verif Files
	SRCS += $(VERIF_DIR)/$(VERIF_SUBDIR)/counter_if.sv
	SRCS += $(VERIF_DIR)/$(VERIF_SUBDIR)/counter_test_pkg.sv

	# Python modules
	PY_MODULES = simple_print
else
	TOP_MODULE = tb_counter
	VERIF_SUBDIR = basic
	VERILATOR_FLAGS += -Wno-fatal -Wno-UNUSEDSIGNAL
endif

SRCS += $(RTL_DIR)/counter.sv $(VERIF_DIR)/$(VERIF_SUBDIR)/$(TOP_MODULE).sv

SIM_EXE = ./$(BUILD_DIR)/obj_dir/V$(TOP_MODULE)

.PHONY: all compile sim clean format lint pyhdl_api_gen

all: compile sim

TESTS = basic uvm pyhdl

test_all:
	@for test in $(TESTS); do \
		echo "========================================"; \
		echo "Running test: $$test"; \
		echo "========================================"; \
		$(MAKE) clean TEST=$$test; \
		$(MAKE) sim TEST=$$test || exit 1; \
	done

pyhdl_api_gen:
ifeq ($(TEST),pyhdl)
	mkdir -p $(BUILD_DIR)
	PYTHONPATH=$(PYTHON_PATH) $(PYHDL_IF) api-gen-sv -m $(PY_MODULES) -p $(TOP_MODULE)_api_pkg -o $(PY_API_PKG)
endif

compile: pyhdl_api_gen $(SRCS)
	mkdir -p $(BUILD_DIR)
	$(VERILATOR) $(VERILATOR_FLAGS) --Mdir $(BUILD_DIR)/obj_dir --top-module $(TOP_MODULE) $(SRCS) -I$(RTL_DIR) -I$(BUILD_DIR) 2>&1 | tee $(BUILD_DIR)/build.log

sim: compile
	PYTHONPATH=$(PYTHON_PATH) $(SIM_EXE) 2>&1 | tee $(BUILD_DIR)/sim.log

format:
	verible-verilog-format --flagfile $(MISC_DIR)/verible/.verible-verilog-format.flags --inplace $(SRCS)

lint:
	verible-verilog-lint --rules_config $(MISC_DIR)/verible/.rules.verible_lint $(SRCS)

clean:
	rm -rf $(BUILD_DIR)

clean_all:
	rm -rf $(BUILD_BASE)
