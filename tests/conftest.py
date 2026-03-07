"""Shared fixtures for HDL simulation tests."""

import os
import subprocess
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# CLI options
# ---------------------------------------------------------------------------

def pytest_addoption(parser):
    parser.addoption(
        "--waves",
        action="store_true",
        default=False,
        help="Enable VCD waveform dumping during simulation.",
    )


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RTL_DIR = PROJECT_ROOT / "src" / "rtl"
VERIF_DIR = PROJECT_ROOT / "src" / "verif"
BUILD_BASE = PROJECT_ROOT / "build"

VENV_SITE_PACKAGES = Path(os.environ['VIRTUAL_ENV']) / "lib" / "python3.12" / "site-packages"
PYHDL_IF_BIN = Path(os.environ['VIRTUAL_ENV']) / "bin" / "pyhdl-if"


def _run(cmd: list[str], *, env=None, cwd=None, log_path: Path | None = None) -> subprocess.CompletedProcess:
    """Run a command, optionally tee-ing output to *log_path*."""
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
    )
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(result.stdout + result.stderr)
    return result


def _pyhdl_if_query(flag: str) -> str:
    """Call ``pyhdl-if <flag>`` and return stripped output."""
    return subprocess.check_output(
        [str(PYHDL_IF_BIN), flag], text=True
    ).strip()


# ---------------------------------------------------------------------------
# Per-variant configuration
# ---------------------------------------------------------------------------

def _build_config(test_name: str, *, waves: bool = False):
    """Return (verilator_flags, srcs, env_extras) for *test_name*."""

    build_dir = BUILD_BASE / test_name
    top_module_map = {"basic": "tb_counter", "uvm": "tb_counter_uvm", "pyhdl": "tb_counter_pyhdl"}
    top = top_module_map[test_name]

    flags = ["--binary", "-Wall", "-j", "0"]
    srcs = []
    python_path = str(VENV_SITE_PACKAGES)

    if waves:
        vcd_path = str(build_dir / "waves.vcd")
        flags += ["--trace", f"+define+WAVES", f'+define+VCD_FILE="{vcd_path}"']

    # -- variant-specific flags/sources --
    if test_name == "uvm":
        uvm_root = os.environ.get("UVM_ROOT", f"{str(Path.home())}/tools/uvm-1.2/src")
        flags += [
            f"+incdir+{uvm_root}",
            "+define+UVM_NO_DPI",
            "-Wno-fatal", "-Wno-DECLFILENAME", "-Wno-IMPORTSTAR",
            "-Wno-WIDTHTRUNC", "-Wno-UNUSEDSIGNAL", "-Wno-UNSIGNED",
            "-Wno-LITENDIAN", "-Wno-VARHIDDEN", "-Wno-TIMESCALEMOD",
        ]
        srcs.append(f"{uvm_root}/uvm_pkg.sv")
        verif_sub = VERIF_DIR / "uvm"
        flags.append(f"+incdir+{verif_sub}")
        srcs.append(str(verif_sub / "counter_verif_pkg.sv"))

    elif test_name == "pyhdl":
        verif_sub = VERIF_DIR / "pyhdl"
        python_path = f"{VENV_SITE_PACKAGES}:{verif_sub}"

        pyhdl_share = _pyhdl_if_query("share")
        pyhdl_libs = _pyhdl_if_query("libs")
        pyhdl_libs_dir = str(Path(pyhdl_libs).parent)

        flags += [
            "-Wno-fatal", "-Wno-UNUSEDSIGNAL",
            f"+incdir+{pyhdl_share}/dpi",
            "+define+HAVE_PYHDL_IF",
            "-LDFLAGS",
            f"-L{pyhdl_libs_dir} -lpyhdl_if -Wl,-rpath,{pyhdl_libs_dir} -Wl,--export-dynamic",
        ]
        srcs.append(f"{pyhdl_share}/dpi/pyhdl_if.sv")

        # API-gen package
        api_pkg = build_dir / f"{top}_api_pkg.sv"
        srcs.append(str(api_pkg))
        flags.append(f"+incdir+{build_dir}")

        # Extra verif sources
        srcs.append(str(verif_sub / "counter_if.sv"))
        srcs.append(str(verif_sub / "counter_test_pkg.sv"))

    else:  # basic
        verif_sub = VERIF_DIR / "basic"
        flags += ["-Wno-fatal", "-Wno-UNUSEDSIGNAL"]

    # Common sources (always last)
    srcs.append(str(RTL_DIR / "counter.sv"))
    srcs.append(str(VERIF_DIR / test_name / f"{top}.sv"))

    return {
        "top": top,
        "build_dir": build_dir,
        "flags": flags,
        "srcs": srcs,
        "python_path": python_path,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def waves(request):
    return request.config.getoption("--waves")


def _pyhdl_api_gen(cfg):
    """Run pyhdl-if api-gen-sv for the pyhdl test variant."""
    build_dir = cfg["build_dir"]
    top = cfg["top"]
    build_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["PYTHONPATH"] = cfg["python_path"]

    api_pkg_path = build_dir / f"{top}_api_pkg.sv"
    result = _run(
        [
            str(PYHDL_IF_BIN), "api-gen-sv",
            "-m", "simple_print",
            "-p", f"{top}_api_pkg",
            "-o", str(api_pkg_path),
        ],
        env=env,
        cwd=str(PROJECT_ROOT),
    )
    if result.returncode != 0:
        raise RuntimeError(f"pyhdl-if api-gen-sv failed:\n{result.stderr}")


def compile_sim(test_name: str, *, waves: bool = False):
    """Compile the simulation for *test_name*. Returns (cfg, compile_result)."""
    cfg = _build_config(test_name, waves=waves)
    build_dir = cfg["build_dir"]
    build_dir.mkdir(parents=True, exist_ok=True)

    # API gen for pyhdl
    if test_name == "pyhdl":
        _pyhdl_api_gen(cfg)

    cmd = [
        "verilator", *cfg["flags"],
        "--Mdir", str(build_dir / "obj_dir"),
        "--top-module", cfg["top"],
        *cfg["srcs"],
        f"-I{RTL_DIR}",
        f"-I{build_dir}",
    ]

    result = _run(cmd, log_path=build_dir / "build.log", cwd=str(PROJECT_ROOT))
    return cfg, result


def run_sim(cfg):
    """Run the compiled simulation. Returns subprocess result."""
    build_dir = cfg["build_dir"]
    exe = build_dir / "obj_dir" / f"V{cfg['top']}"

    env = os.environ.copy()
    env["PYTHONPATH"] = cfg["python_path"]

    result = _run([str(exe)], env=env, log_path=build_dir / "sim.log", cwd=str(PROJECT_ROOT))
    return result
