VERILATOR = verilator
VERILATOR_FLAGS = --binary --trace -Wall --cc

MISC_DIR = ./src/misc
RTL_DIR = ./src/rtl
VERIF_DIR = ./src/verif
BUILD_DIR = build

TOP_MODULE = tb_counter
SRCS = $(RTL_DIR)/counter.sv $(VERIF_DIR)/tb_counter.sv

SIM_EXE = ./$(BUILD_DIR)/obj_dir/V$(TOP_MODULE)

.PHONY: all compile sim clean format lint

all: compile sim

compile: $(SRCS)
	mkdir -p $(BUILD_DIR)
	$(VERILATOR) $(VERILATOR_FLAGS) --Mdir $(BUILD_DIR)/obj_dir --top-module $(TOP_MODULE) $(SRCS) -I$(RTL_DIR)

sim: compile
	$(SIM_EXE)

format:
	verible-verilog-format --flagfile $(MISC_DIR)/verible/.verible-verilog-format.flags --inplace $(SRCS)

lint:
	verible-verilog-lint --rules_config $(MISC_DIR)/verible/.rules.verible_lint $(SRCS)

clean:
	rm -rf $(BUILD_DIR)
