#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="python"

read -r -p "Have you downloaded the new match commentary PDFs in the required format? Type yes to continue: " CONFIRMATION
if [[ "$CONFIRMATION" != "yes" ]]; then
    echo "Aborting refresh. Download the commentary PDFs in the required format first."
    exit 1
fi

echo "Refreshing scorecards and points table..."
"$PYTHON_BIN" -Bu download_scorecards.py

echo "Rebuilding commentary CSVs..."
"$PYTHON_BIN" -Bu build_match_commentary_csvs.py --input-dir "ball-by-ball" --scorecards-dir "scorecards"

echo "Validating commentary extras against scorecards..."
"$PYTHON_BIN" -Bu test/test_scorecard_extras.py --commentary-dir "ball-by-ball" --scorecards-dir "scorecards"

echo "Pipeline refresh complete."
