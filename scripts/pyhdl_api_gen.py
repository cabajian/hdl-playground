#!/usr/bin/env python3
"""FuseSoC generator: invoke pyhdl-if api-gen-sv to produce an SV API package.

FuseSoC calls this script as:
    python3 scripts/pyhdl_api_gen.py <gapi_input_file>

The gapi_input_file is YAML with:
  parameters:
    shared_dir:  absolute path to src/verif/pyhdl/ (shared Python modules)
    variant_dir: absolute path to the variant subdir (e.g. src/verif/pyhdl/ether/)
    package:     SV package name (e.g. tb_ether_pyhdl_api_pkg)
    pythonpath:  colon-separated PYTHONPATH for pyhdl-if

This script creates <package>.sv and <package>.core in the generator_cwd
(the directory containing the gapi file). FuseSoC picks up the .core file
and adds the generated .sv to the build automatically.
"""

import os
import subprocess
import sys
from pathlib import Path

import yaml


def main():
    gapi_file = Path(sys.argv[1])
    with gapi_file.open() as f:
        data = yaml.safe_load(f)

    params = data.get("parameters", {})
    files_root = Path(data["files_root"])
    package = params["package"]
    pythonpath = params.get("pythonpath", "")

    # shared_dir and variant_dir may be relative (to files_root) or absolute.
    shared_dir = (files_root / params["shared_dir"]).resolve()
    variant_dir = (
        (files_root / params["variant_dir"]).resolve()
        if params.get("variant_dir")
        else None
    )

    generator_cwd = gapi_file.parent
    output_sv = generator_cwd / f"{package}.sv"

    # Discover Python modules by scanning the provided directories.
    modules = []
    for d in filter(None, [shared_dir, variant_dir]):
        for py in sorted(d.glob("*.py")):
            modules.append(py.stem)

    pyhdl_share = params.get("pyhdl_share", "")

    venv = os.environ.get("VIRTUAL_ENV", sys.prefix)
    pyhdl_if_bin = Path(venv) / "bin" / "pyhdl-if"

    module_args = []
    for m in modules:
        module_args.extend(["-m", m])

    cmd = [str(pyhdl_if_bin), "api-gen-sv", *module_args, "-p", package, "-o", str(output_sv)]

    env = os.environ.copy()
    if pythonpath:
        env["PYTHONPATH"] = pythonpath

    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        sys.exit(1)

    # The generated core lists pyhdl_if.sv first so it is compiled before the
    # api package (which imports pyhdl_if::*). Both land in first_snippets via
    # position: first, preserving the order they appear in the fileset.
    pyhdl_if_entry = (
        f"      - {pyhdl_share}/dpi/pyhdl_if.sv\n" if pyhdl_share else ""
    )
    core_content = f"""CAPI=2:
name: ::generated_{package}:0
description: Generated PyHDL-IF API package ({package})

filesets:
  api:
    files:
{pyhdl_if_entry}      - {package}.sv
    file_type: systemVerilogSource

targets:
  default:
    filesets: [api]
"""
    (generator_cwd / f"{package}.core").write_text(core_content)


if __name__ == "__main__":
    main()
