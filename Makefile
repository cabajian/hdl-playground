MISC_DIR = ./src/misc
RTL_DIR = ./src/rtl
VERIF_DIR = ./src/verif
BUILD_BASE = build

# Tools
PYTHON = ./bin/python3

# Collect all SV sources for format/lint
SRCS = $(shell find $(RTL_DIR) $(VERIF_DIR) -name '*.sv')

.PHONY: test format lint clean clean_all

# ---------------------------------------------------------------------------
# Test (delegates to pytest)
# ---------------------------------------------------------------------------

test:
	$(PYTHON) -m pytest $(PYTEST_ARGS)

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

format:
	verible-verilog-format --flagfile $(MISC_DIR)/verible/.verible-verilog-format.flags --inplace $(SRCS)

lint:
	verible-verilog-lint --rules_config $(MISC_DIR)/verible/.rules.verible_lint $(SRCS)

clean:
	rm -rf $(BUILD_BASE)
