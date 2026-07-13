"""Render a ground-truth netlist JSON to an SVG connectivity diagram via
`npx netlistsvg`.

Input is the GT netlist format used by check-netlist/regenerate
({"nets": [{"name", "pins": ["Ref:Num", ...]}], ...}); only nets[].name and
nets[].pins are consumed. Every component reference becomes a generic labeled
box (one port per pin, keyed by pin number); every net with 2+ pins becomes a
wire carrying its GT name. Connectivity diagram, not a real schematic.
"""
import json
import os
import subprocess
import tempfile


def build_yosys_netlist(gt_nets):
    """GT nets ([{"name", "pins"}]) -> Yosys-style netlist JSON for netlistsvg.

    Netnames keyed by GT net name (duplicates uniquified with _2, _3, ...);
    single-pin nets contribute ports but no netname (dangling stub).

    Visible-text quirks of netlistsvg (verified empirically): a cell's TYPE
    string is what renders as its box label ("generic" would label every box
    "generic"), so type = the component ref; "netnames" entries are NOT drawn
    as text, so each named net is also exposed as a module port, which does
    render its name.
    """
    cells = {}  # ref -> {number -> None}
    for net in gt_nets:
        for pid in net["pins"]:
            ref, num = pid.split(":", 1)
            cells.setdefault(ref, {})[num] = None

    netnames = {}
    bit_by_pid = {}
    next_bit = 1
    for net in gt_nets:
        if len(net["pins"]) < 2:
            continue
        name = net["name"]
        suffix = 2
        while name in netnames:
            name = f'{net["name"]}_{suffix}'
            suffix += 1
        bit = next_bit
        next_bit += 1
        netnames[name] = {"bits": [bit]}
        for pid in net["pins"]:
            bit_by_pid[pid] = bit

    cell_json = {}
    for ref, pins in cells.items():
        port_directions = {}
        connections = {}
        for num in pins:
            # netlistsvg's JSON schema only allows "input"/"output"; direction
            # isn't tracked in GT netlists, so "input" uniformly.
            port_directions[num] = "input"
            pid = f"{ref}:{num}"
            connections[num] = [bit_by_pid[pid]] if pid in bit_by_pid else []
        cell_json[ref] = {
            "type": ref,
            "port_directions": port_directions,
            "connections": connections,
        }

    module_ports = {name: {"direction": "input", "bits": nn["bits"]}
                    for name, nn in netnames.items()}

    return {
        "modules": {
            "top": {
                "ports": module_ports,
                "cells": cell_json,
                "netnames": netnames,
            }
        }
    }


def render_netlist_svg(netlist_path, output_path):
    """Load a GT netlist JSON and shell out to `npx netlistsvg`.

    Errors propagate as-is (CalledProcessError / FileNotFoundError /
    KeyError on malformed JSON) — no message wrapping.
    """
    with open(netlist_path, encoding="utf-8") as f:
        gt_nets = json.load(f)["nets"]
    netlist = build_yosys_netlist(gt_nets)

    fd, tmp_path = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(netlist, f)
        subprocess.run(["npx", "--yes", "netlistsvg", tmp_path, "-o", output_path], check=True)
    finally:
        os.remove(tmp_path)
