"""Shared fixtures for HDL simulation tests."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


def pytest_addoption(parser):
    parser.addoption(
        "--waves",
        action="store_true",
        default=False,
        help="Enable VCD waveform dumping during simulation.",
    )


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BUILD_BASE = PROJECT_ROOT / "build"

VENV_SITE_PACKAGES = (
    Path(os.environ.get("VIRTUAL_ENV", sys.prefix))
    / "lib"
    / f"python{sys.version_info.major}.{sys.version_info.minor}"
    / "site-packages"
)
PYHDL_IF_BIN = Path(os.environ.get("VIRTUAL_ENV", sys.prefix)) / "bin" / "pyhdl-if"

# ---------------------------------------------------------------------------
# FuseSoC discovery
# ---------------------------------------------------------------------------

_core_cache: dict[str, dict] = {}


def _fusesoc(*args) -> str:
    """Run a fusesoc command rooted at PROJECT_ROOT and return stdout."""
    cmd = ["fusesoc", "--cores-root", str(PROJECT_ROOT), *args]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(PROJECT_ROOT))
    if result.returncode != 0:
        raise RuntimeError(f"fusesoc {' '.join(args)} failed:\n{result.stderr}")
    return result.stdout


def list_tb_cores() -> list[str]:
    """Return the short core names (e.g. 'tb_ether_pyhdl') for all tb_* cores."""
    names = []
    for line in _fusesoc("list-cores").splitlines():
        first = line.split()[0] if line.split() else ""
        if first.startswith("::tb_"):
            # "::tb_ether_pyhdl:0" → "tb_ether_pyhdl"
            names.append(first.lstrip(":").rsplit(":", 1)[0])
    return names


def query_core(core_name: str) -> dict:
    """Query fusesoc for *core_name* and return {vlnv, core_root, targets, toplevel}.

    Results are cached so repeated calls within a session are free.
    """
    if core_name in _core_cache:
        return _core_cache[core_name]

    vlnv = f"::{core_name}:0"
    info_text = _fusesoc("core-info", vlnv)

    core_root = None
    core_file = None
    targets: list[str] = []
    in_targets = False

    for line in info_text.splitlines():
        stripped = line.strip()
        if stripped == "Targets:":
            in_targets = True
            continue
        if in_targets:
            if not stripped:
                in_targets = False
                continue
            targets.append(stripped.split(":")[0].strip())
        elif line.startswith("Core root:"):
            core_root = Path(line.split("Core root:", 1)[1].strip())
        elif line.startswith("Core file:"):
            core_file = line.split("Core file:", 1)[1].strip()

    core_path = core_root / core_file
    data = yaml.safe_load(core_path.read_text())
    toplevel = data.get("targets", {}).get("sim", {}).get("toplevel", "")

    result = {
        "vlnv": vlnv,
        "core_root": core_root,
        "targets": targets,
        "toplevel": toplevel,
    }
    _core_cache[core_name] = result
    return result


# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------

def _build_env(core_name: str) -> dict:
    """Return os.environ copy with variables needed by the core files."""
    env = os.environ.copy()
    env["PROJECT_ROOT"] = str(PROJECT_ROOT)
    env["VENV_SITE_PACKAGES"] = str(VENV_SITE_PACKAGES)

    if "pyhdl" in core_name:
        pyhdl_share = subprocess.check_output(
            [str(PYHDL_IF_BIN), "share"], text=True
        ).strip()
        pyhdl_libs = subprocess.check_output(
            [str(PYHDL_IF_BIN), "libs"], text=True
        ).strip()
        env["PYHDL_IF_SHARE"] = pyhdl_share
        env["PYHDL_IF_LIBS_DIR"] = str(Path(pyhdl_libs).parent)

    if "uvm" in core_name:
        env.setdefault("UVM_ROOT", str(Path.home() / "tools" / "uvm-1.2" / "src"))

    return env


def _python_path(core_name: str, core_root: Path) -> str:
    """Return the PYTHONPATH string needed to run the simulation binary."""
    if "pyhdl" in core_name:
        # core_root is src/verif/pyhdl/<variant>/; parent is the shared pyhdl dir
        return ":".join([
            str(VENV_SITE_PACKAGES),
            str(core_root.parent),
            str(core_root),
        ])
    return str(VENV_SITE_PACKAGES)


# ---------------------------------------------------------------------------
# Public API used by tests
# ---------------------------------------------------------------------------

@pytest.fixture
def waves(request):
    return request.config.getoption("--waves")


def compile_sim(core_name: str, *, waves: bool = False) -> tuple[dict, subprocess.CompletedProcess]:
    """Compile the simulation for *core_name* (e.g. 'tb_ether_pyhdl').

    Returns (cfg, compile_result) where cfg is passed to run_sim().
    """
    info = query_core(core_name)
    core_root: Path = info["core_root"]

    target = "sim_waves" if (waves and "sim_waves" in info["targets"]) else "sim"

    build_dir = BUILD_BASE / core_name
    build_dir.mkdir(parents=True, exist_ok=True)

    env = _build_env(core_name)

    cmd = [
        "fusesoc",
        "--cores-root", str(PROJECT_ROOT),
        "run",
        "--no-export",
        "--resolve-env-vars-early",
        "--target", target,
        "--work-root", str(build_dir),
        "--build",
        info["vlnv"],
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True, env=env, cwd=str(PROJECT_ROOT)
    )

    cfg = {
        "top": info["toplevel"],
        "build_dir": build_dir,
        "python_path": _python_path(core_name, core_root),
        "waves": waves,
    }
    (build_dir / "build.log").write_text(result.stdout + result.stderr)
    return cfg, result


def run_sim(cfg: dict, plusargs: list[str] | None = None) -> subprocess.CompletedProcess:
    """Run the compiled simulation binary. Returns the subprocess result.

    `plusargs` are appended verbatim (e.g. ["+UVM_TESTNAME=foo", "+num_msgs=8"]).

    The child's stderr is folded into its stdout so that the SystemVerilog and
    Python halves of the run land in one pipe and stay in the order they were
    written. Capturing them separately and concatenating afterwards would
    produce a file with every UVM line before every Python line regardless of
    when each was emitted. `result.stderr` is therefore always None; everything
    is in `result.stdout`.
    """
    build_dir: Path = cfg["build_dir"]
    exe = build_dir / f"V{cfg['top']}"

    env = os.environ.copy()
    env["PYTHONPATH"] = cfg["python_path"]

    args = [str(exe)]
    if cfg.get("waves"):
        args.append(f"+waves_vcd={build_dir / 'waves.vcd'}")
    args.extend(plusargs or [])

    result = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        cwd=str(PROJECT_ROOT),
    )
    (build_dir / "sim.log").write_text(result.stdout)
    return result
