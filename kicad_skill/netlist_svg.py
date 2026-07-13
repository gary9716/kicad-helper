"""Convert a schematic's flattened connectivity into netlistsvg's Yosys-style
netlist JSON, then render it to SVG via `npx netlistsvg`.

Every component reference becomes a generic labeled box (one port per pin,
keyed by pin number); every electrical net with 2+ pins becomes a wire. This
is a connectivity diagram, not a real schematic — no R/C/U symbol art.
"""
import json
import os
import subprocess
import tempfile

from .netlist_eval import extract_actual_netlist


def build_yosys_netlist(schematic_path, table_path=None):
    """Build the Yosys-style netlist JSON netlistsvg expects.

    Derives both cells (ref -> its pins) and nets directly from
    extract_actual_netlist()'s flattened output: every pin in the design
    already appears in some net there, singleton sets included for
    unconnected pins, so no separate pin enumeration pass is needed.
    """
    actual_nets = extract_actual_netlist(schematic_path, table_path)

    cells = {}  # ref -> {number -> None}, insertion order preserves discovery
    for net in actual_nets:
        for pid in net:
            ref, num = pid.split(":", 1)
            cells.setdefault(ref, {})[num] = None

    netnames = {}
    bit_by_pid = {}
    next_bit = 1
    for net in sorted(actual_nets, key=lambda s: sorted(s)[0]):
        if len(net) < 2:
            continue
        bit = next_bit
        next_bit += 1
        name = f"net{bit}"
        netnames[name] = {"bits": [bit]}
        for pid in net:
            bit_by_pid[pid] = bit

    cell_json = {}
    for ref, pins in cells.items():
        port_directions = {}
        connections = {}
        for num in pins:
            port_directions[num] = "inout"
            pid = f"{ref}:{num}"
            connections[num] = [bit_by_pid[pid]] if pid in bit_by_pid else []
        cell_json[ref] = {
            "type": "generic",
            "port_directions": port_directions,
            "connections": connections,
        }

    return {
        "modules": {
            "top": {
                "ports": {},
                "cells": cell_json,
                "netnames": netnames,
            }
        }
    }


def render_netlist_svg(schematic_path, output_path, table_path=None):
    """Build the netlist JSON and shell out to `npx netlistsvg` to render it.

    Errors propagate as-is (CalledProcessError / FileNotFoundError) — no
    message wrapping, matching fetch_easyeda_component's style.
    """
    netlist = build_yosys_netlist(schematic_path, table_path)

    fd, tmp_path = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(netlist, f)
        subprocess.run(["npx", "--yes", "netlistsvg", tmp_path, "-o", output_path], check=True)
    finally:
        os.remove(tmp_path)
