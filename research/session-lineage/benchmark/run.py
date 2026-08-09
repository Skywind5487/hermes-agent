#!/usr/bin/env python3
"""Clone-and-run entry point for the current two-finalist local benchmark.

Standard library only. Default = small smoke. --full = all current synthetic
performance scenarios. This is not the e2-micro production gate by itself.
"""
from __future__ import annotations
import argparse, subprocess, sys
from pathlib import Path

HERE=Path(__file__).resolve().parent
p=argparse.ArgumentParser()
p.add_argument('--full',action='store_true')
p.add_argument('--output-dir',default=str(HERE/'out'))
p.add_argument('--filler',type=int,default=20000)
p.add_argument('--budget',type=int,default=10000)
a=p.parse_args()
cmd=[sys.executable,str(HERE/'final_duel.py'),'--out',str(Path(a.output_dir)/'final-duel'),'--filler',str(a.filler),'--budget',str(a.budget)]
if not a.full:
    cmd += ['--quick','--regex','modern_roots_k3|blocked_50_lineages_depth5_k3|five_lineages_k10_unreachable']
print('+',' '.join(cmd),flush=True)
subprocess.run(cmd,check=True,cwd=HERE)
