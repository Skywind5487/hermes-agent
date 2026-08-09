#!/usr/bin/env python3
"""Clone-and-run entry point for the session-lineage research harness.

Standard library only. By default this runs a quick smoke. Use --full for
full local graveyard + final-duel measurements. VM production-gate behavior
is intentionally not implied by either mode.
"""
from __future__ import annotations
import argparse, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

def run(cmd: list[str]) -> None:
    print('+', ' '.join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=HERE)

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('--full', action='store_true', help='run full local synthetic matrix')
    p.add_argument('--output-dir', default=str(HERE / 'out'))
    p.add_argument('--filler', type=int, default=20_000)
    p.add_argument('--budget', type=int, default=10_000)
    args = p.parse_args()
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    grave = [sys.executable, str(HERE/'benchmark_v3_graveyard.py'),
             '--output-dir', str(out/'graveyard'), '--filler', str(args.filler),
             '--budget', str(args.budget)]
    duel = [sys.executable, str(HERE/'final_duel.py'),
            '--out', str(out/'final-duel'), '--filler', str(args.filler),
            '--budget', str(args.budget)]
    if not args.full:
        grave += ['--quick']
        duel += ['--warm','3','--cold','2',
                 '--regex','modern_roots_k3|blocked_50_lineages_depth5_k3|five_lineages_k10_unreachable']
    run(grave)
    run(duel)
    print(f'outputs: {out}')

if __name__ == '__main__':
    main()
