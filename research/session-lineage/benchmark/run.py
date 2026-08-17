#!/usr/bin/env python3
"""Entry point for #54 benchmark package."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
group = parser.add_mutually_exclusive_group()
group.add_argument("--full", action="store_true", help="historical full synthetic TEMP-vs-Fixed matrix")
group.add_argument("--gate", action="store_true", help="broader production-shaped synthetic VM/WSL gate")
group.add_argument("--quick-gate", action="store_true", help="smoke version of --gate")
group.add_argument("--focused-gate", action="store_true", help="current #54 focused no-memo/memo decision gate")
group.add_argument("--quick-focused-gate", action="store_true", help="smoke version of --focused-gate")
parser.add_argument("--output-dir", default=str(HERE / "out"))
parser.add_argument("--filler", type=int, default=20000)
parser.add_argument("--budget", type=int, default=10000)
args = parser.parse_args()

if args.focused_gate or args.quick_focused_gate:
    cmd = [
        sys.executable,
        str(HERE / "focused_vm_gate.py"),
        "--out",
        str(Path(args.output_dir) / "focused-vm-gate"),
    ]
    if args.quick_focused_gate:
        cmd.append("--quick")
elif args.gate or args.quick_gate:
    cmd = [
        sys.executable,
        str(HERE / "vm_gate.py"),
        "--out",
        str(Path(args.output_dir) / "vm-gate"),
    ]
    if args.quick_gate:
        cmd.append("--quick")
else:
    cmd = [
        sys.executable,
        str(HERE / "final_duel.py"),
        "--out",
        str(Path(args.output_dir) / "final-duel"),
        "--filler",
        str(args.filler),
        "--budget",
        str(args.budget),
    ]
    if not args.full:
        cmd += [
            "--quick",
            "--regex",
            "modern_roots_k3|blocked_50_lineages_depth5_k3|five_lineages_k10_unreachable",
        ]

print("+", " ".join(cmd), flush=True)
subprocess.run(cmd, check=True, cwd=HERE)
