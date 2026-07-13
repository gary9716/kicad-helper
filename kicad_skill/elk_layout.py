"""ELK (elkjs) based schematic auto-layout.

Pipeline: parse (reuse resolve_layout/_netlist_eval) -> classify nets
(power & high-fanout -> labels, 2-3 pin signals -> ELK edges) -> build ELK
JSON (layered, FIXED_POS ports, orthogonal edges) -> node tools/elk_runner.js
-> snap to KiCad grid -> write symbols/wires/labels/junctions back.

Spec: docs/superpowers/specs/2026-07-13-elk-layout-design.md
"""
import json
import os
import subprocess

from .regenerate import _is_power

GRID = 1.27  # KiCad wire/pin grid (mm)


def classify_for_elk(nets, fanout_threshold=4):
    """Split named nets into (edge_nets, label_nets).

    nets: iterable of (name, set_of_pin_ids). Power-named nets and nets with
    fanout >= threshold become labels; 2..threshold-1 pin signal nets become
    ELK edges; singletons are dropped (nothing to draw).
    """
    edge_nets, label_nets = [], []
    for name, pins in nets:
        if len(pins) < 2:
            continue
        if _is_power(name) or len(pins) >= fanout_threshold:
            label_nets.append((name, pins))
        else:
            edge_nets.append((name, pins))
    return edge_nets, label_nets


def name_nets(nets, pin_positions, labels_at):
    """Attach a name to each anonymous pin-set net.

    A net whose any pin position carries an existing label uses that label's
    text; otherwise the name is synthesized from the first pin id (sorted),
    e.g. NET_U2_1. Returns list of (name, pin_set).
    """
    named = []
    for net in nets:
        name = None
        for pid in sorted(net):
            pos = pin_positions.get(pid)
            if pos is not None and pos in labels_at:
                name = labels_at[pos]
                break
        if name is None:
            name = "NET_" + sorted(net)[0].replace(":", "_")
        named.append((name, net))
    return named


def collect_labels_at(sch_sexpr):
    """{(x, y): label_text} for every label/global_label in the sheet."""
    out = {}
    for child in sch_sexpr[1:]:
        if isinstance(child, list) and child and child[0] in ("label", "global_label"):
            at = next((s for s in child[1:]
                       if isinstance(s, list) and s[0] == "at" and len(s) > 2), None)
            if at is not None:
                out[(float(at[1]), float(at[2]))] = child[1]
    return out
