#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="python"

"$PYTHON_BIN" -Bu build_match_commentary_csvs.py --input-dir "ball-by-ball" --scorecards-dir "scorecards"
