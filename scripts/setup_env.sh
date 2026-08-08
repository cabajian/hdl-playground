#!/usr/bin/env bash
#
# Set up everything needed to run this repo's testbenches.
#
#   ./scripts/setup_env.sh          # venv + python deps + verilator patch + UVM
#   ./scripts/setup_env.sh --check  # verify only, change nothing
#
# Idempotent: safe to re-run, and re-running after `pip install` is how you
# recover the Verilator patch (see step 3).
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${VENV:-$REPO_ROOT/.venv}"
UVM_HOME="${UVM_HOME:-$HOME/tools/uvm-1.2}"
UVM_REPO="${UVM_REPO:-https://github.com/gchinna/uvm-1.2}"

CHECK_ONLY=0
[[ "${1:-}" == "--check" ]] && CHECK_ONLY=1

say()  { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 1. Virtualenv
# ---------------------------------------------------------------------------
if [[ ! -d "$VENV" ]]; then
    [[ $CHECK_ONLY == 1 ]] && die "no venv at $VENV (run without --check)"
    say "Creating virtualenv at $VENV"
    python3 -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"

# ---------------------------------------------------------------------------
# 2. Python dependencies
#
# requirements.txt pins the toolchain as well as the libraries: fusesoc drives
# the build, and verilator/verible are consumed as wheels rather than system
# packages (distro Verilator is too old -- see step 3).
# ---------------------------------------------------------------------------
if [[ $CHECK_ONLY == 0 ]]; then
    say "Installing Python dependencies"
    pip install --quiet --upgrade pip
    pip install --quiet -r "$REPO_ROOT/requirements.txt"
fi

for tool in fusesoc verible-verilog-format verible-verilog-lint; do
    command -v "$tool" >/dev/null || die "$tool not on PATH after install"
done

# ---------------------------------------------------------------------------
# 3. Patch the Verilator wheel's verilated.mk
#
# The wheel ships with its compiler-configuration variables empty, because they
# are normally filled in by Verilator's ./configure. Without them the C++ stage
# fails in confusing ways:
#
#   CFG_CXXFLAGS_STD / _COROUTINES  missing -> "'coroutine_handle' in namespace
#                                   'std' does not name a template type"
#   CFG_CXXFLAGS_PCH_I              missing -> "<prefix>__pch.h.fast: linker
#                                   input file not found"
#
# A plain `pip install` silently reverts this, so re-run this script after any
# dependency change. Values match a stock Verilator configure on GCC.
# ---------------------------------------------------------------------------
VERILATOR_ROOT="$(python3 -c 'import verilator, pathlib; print(pathlib.Path(verilator.__file__).parent)')"
VERILATED_MK="$VERILATOR_ROOT/include/verilated.mk"
[[ -f "$VERILATED_MK" ]] || die "verilated.mk not found at $VERILATED_MK"

set +e
python3 - "$VERILATED_MK" "$CHECK_ONLY" <<'PY'
import re, sys
path, check_only = sys.argv[1], sys.argv[2] == "1"
want = {
    "CFG_CXXFLAGS_STD": "-std=gnu++20",
    "CFG_CXXFLAGS_STD_NEWEST": "-std=gnu++20",
    "CFG_CXXFLAGS_COROUTINES": "-fcoroutines",
    "CFG_CXXFLAGS_PCH_I": "-include",
    "CFG_CXXFLAGS_NO_UNUSED": (
        "-faligned-new -fcf-protection=none -Wno-bool-operation "
        "-Wno-overloaded-virtual -Wno-shadow -Wno-sign-compare -Wno-uninitialized "
        "-Wno-unused-but-set-parameter -Wno-unused-but-set-variable "
        "-Wno-unused-parameter -Wno-unused-variable"),
    "CFG_CXXFLAGS_WEXTRA": "-Wextra -Wfloat-conversion -Wlogical-op",
}
text = open(path).read()
missing, changed = [], []
for var, val in want.items():
    m = re.search(rf"^{var}\s*=(.*)$", text, re.MULTILINE)
    if not m:
        missing.append(var)
        continue
    if m.group(1).strip():
        continue                       # already populated
    missing.append(var)
    if not check_only:
        text = text[:m.start()] + f"{var} = {val}" + text[m.end():]
        changed.append(var)
if check_only:
    print(f"    verilated.mk: {len(missing)} unset var(s)" if missing
          else "    verilated.mk: OK")
    sys.exit(1 if missing else 0)
if changed:
    open(path, "w").write(text)
    print(f"    patched: {', '.join(changed)}")
else:
    print("    verilated.mk: already patched")
PY
mk_status=$?
set -e
if [[ $CHECK_ONLY == 1 && $mk_status != 0 ]]; then
    die "verilated.mk has unset compiler-config vars; re-run without --check"
fi

# ---------------------------------------------------------------------------
# 4. UVM 1.2
#
# Required by the UVM testbenches. Note the version matters: pyhdl-if's Python
# packer model tracks UVM 1.2, not 1800.2-2020 (see best_practices.md 4.4), and
# UVM 1.2 in turn needs Verilator >= 5.4x to preprocess.
# ---------------------------------------------------------------------------
if [[ ! -f "$UVM_HOME/src/uvm_pkg.sv" ]]; then
    [[ $CHECK_ONLY == 1 ]] && die "UVM 1.2 not found at $UVM_HOME/src"
    say "Fetching UVM 1.2 into $UVM_HOME"
    tmp="$(mktemp -d)"
    git clone --quiet --depth 1 "$UVM_REPO" "$tmp/uvm"
    mkdir -p "$UVM_HOME"
    cp -r "$tmp/uvm/src" "$UVM_HOME/src"
    rm -rf "$tmp"
fi
grep -q "UVM_MAJOR_REV 1" "$UVM_HOME/src/macros/uvm_version_defines.svh" \
    || warn "$UVM_HOME does not look like UVM 1.2 -- the TCP testbench expects 1.2"

# ---------------------------------------------------------------------------
# 5. Git hooks
# ---------------------------------------------------------------------------
[[ $CHECK_ONLY == 0 ]] && git -C "$REPO_ROOT" config core.hooksPath .githooks

# ---------------------------------------------------------------------------
# 6. Report
# ---------------------------------------------------------------------------
say "Environment ready"
printf '    %-12s %s\n' \
    "verilator"  "$("$VERILATOR_ROOT/bin/verilator" --version 2>&1 | head -1)" \
    "fusesoc"    "$(fusesoc --version 2>&1 | head -1)" \
    "verible"    "$(verible-verilog-lint --version 2>&1 | head -1 | tr -d '\t')" \
    "UVM_HOME"   "$UVM_HOME"
cat <<EOF

Add to your shell (direnv does this automatically via .envrc):

    source $VENV/bin/activate
    export VERILATOR_ROOT=$VERILATOR_ROOT
    export PATH=\$VERILATOR_ROOT/bin:\$PATH
    export UVM_HOME=$UVM_HOME

Then:  pytest
EOF
