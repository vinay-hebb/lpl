#!/usr/bin/env bash
set -euo pipefail

read -r -p "Have you downloaded the new match commentary PDFs in the required format? Type yes to continue: " CONFIRMATION
if [[ "$CONFIRMATION" != "yes" ]]; then
    echo "Aborting refresh. Download the commentary PDFs in the required format first."
    exit 1
fi

echo "Refreshing scorecards and points table..."
python -Bu download_scorecards.py

echo "Rebuilding commentary CSVs..."
python -Bu build_match_commentary_csvs.py --input-dir "ball-by-ball" --scorecards-dir "scorecards"

echo "Validating commentary extras against scorecards..."
python -Bu test/test_scorecard_extras.py --commentary-dir "ball-by-ball" --scorecards-dir "scorecards"

echo "Pipeline refresh complete."
