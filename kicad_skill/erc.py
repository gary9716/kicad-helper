"""KiCad ERC wrapper — the authoritative connectivity gate.

The hand-rolled netlist model (netlist_eval.py) is self-consistent but not
KiCad-faithful: it validated its own mistakes (e.g. it reported a clean netlist
for a schematic KiCad's ERC flagged with 18 errors). KiCad's own ERC engine is
the ground truth, so generated schematics are gated on `kicad-cli sch erc`.

KiCad decides connectivity by EXACT coordinate coincidence (no tolerance): a
wire/label/pin endpoint connects only to another item at precisely the same
point. This module surfaces the four failure modes that matter for generation:
shorts, opens, wire_dangling, and label_dangling.
"""
import json
import os
import shutil
import subprocess
import tempfile

# Common install locations for the KiCad CLI, in addition to PATH.
_CANDIDATE_PATHS = [
    "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli",
    "/usr/bin/kicad-cli",
    "/usr/local/bin/kicad-cli",
]


def find_kicad_cli():
    """Return a path to the kicad-cli executable, or None if unavailable."""
    on_path = shutil.which("kicad-cli")
    if on_path:
        return on_path
    for p in _CANDIDATE_PATHS:
        if os.path.exists(p) and os.access(p, os.X_OK):
            return p
    return None


# ERC violation types that mean the netlist is electrically wrong.
DANGLING_TYPES = {"wire_dangling", "label_dangling"}


def run_erc(sch_path, severity="error"):
    """Run KiCad ERC on a schematic and return a structured report.

    Returns a dict:
      {
        "violations": [ {"type", "severity", "description", "items": [{"x","y","description"}]} ],
        "error_count": int,
        "ok": bool,            # True iff zero error-severity violations
      }

    Raises RuntimeError if kicad-cli is unavailable (callers that want a soft
    fallback should check find_kicad_cli() first).
    """
    cli = find_kicad_cli()
    if cli is None:
        raise RuntimeError("kicad-cli not found; cannot run ERC")

    sev_flag = {
        "error": "--severity-error",
        "warning": "--severity-warning",
        "all": "--severity-all",
    }.get(severity, "--severity-error")

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        out_json = tf.name
    try:
        subprocess.run(
            [cli, "sch", "erc", "--format", "json", "--output", out_json,
             sev_flag, sch_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        with open(out_json, "r", encoding="utf-8") as f:
            data = json.load(f)
    finally:
        if os.path.exists(out_json):
            os.remove(out_json)

    violations = []
    for sheet in data.get("sheets", []):
        for v in sheet.get("violations", []):
            items = [
                {
                    "x": it.get("pos", {}).get("x"),
                    "y": it.get("pos", {}).get("y"),
                    "description": it.get("description", ""),
                }
                for it in v.get("items", [])
            ]
            violations.append({
                "type": v.get("type"),
                "severity": v.get("severity"),
                "description": v.get("description", ""),
                "items": items,
            })

    error_count = sum(1 for v in violations if v["severity"] == "error")
    return {
        "violations": violations,
        "error_count": error_count,
        "ok": error_count == 0,
    }


def violations_by_type(report, *types):
    """Filter a run_erc report's violations to the given type(s)."""
    wanted = set(types)
    return [v for v in report["violations"] if v["type"] in wanted]
