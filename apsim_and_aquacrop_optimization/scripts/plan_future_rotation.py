#!/usr/bin/env python3
"""Prepare or optimize future crop maps for selected objectives with NSGA-II/III."""
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agro.planning.future_scenarios import main


if __name__=='__main__':
    raise SystemExit(main())
