#!/usr/bin/env python
"""Compare extras from scorecard downloads against ball-by-ball commentary CSVs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from commentary_common import parse_match_csv_metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report PASS/FAIL for extras parity between scorecards and ball-by-ball CSVs."
    )
    parser.add_argument(
        "--commentary-dir",
        type=Path,
        default=Path("ball-by-ball"),
        help="Directory containing per-match commentary CSVs",
    )
    parser.add_argument(
        "--scorecards-dir",
        type=Path,
        default=Path("scorecards"),
        help="Directory containing downloaded scorecard JSON files",
    )
    return parser.parse_args()


def load_commentary_extras(
    commentary_dir: Path,
) -> tuple[dict[tuple[str, str], int], dict[str, str]]:
    extras_by_innings: dict[tuple[str, str], int] = {}
    match_labels: dict[str, str] = {}
    for csv_path in sorted(
        commentary_dir.glob("Ball-by-Ball Commentary & Live Score_*.csv")
    ):
        frame = pd.read_csv(csv_path)
        if frame.empty:
            continue
        metadata = parse_match_csv_metadata(csv_path)
        match_label = metadata["match_label"]
        frame["extras"] = pd.to_numeric(frame.get("extras", 0), errors="coerce").fillna(0)
        frame["extra_type"] = frame.get("extra_type", "").fillna("")
        frame = frame[~frame["extra_type"].isin(["bye", "leg_bye"])].copy()
        grouped = (
            frame.groupby(["match_title", "batting_team"], dropna=False)["extras"].sum().reset_index()
        )
        for row in grouped.itertuples(index=False):
            extras_by_innings[(row.match_title, row.batting_team)] = int(row.extras)
            match_labels[row.match_title] = match_label
    return extras_by_innings, match_labels


def scorecard_extras_without_byes(innings: dict) -> int:
    extras = innings.get("extras", {}) or {}
    extra_rows = extras.get("data", []) or []
    included_codes = {"WD", "NB"}
    total = 0
    for row in extra_rows:
        if str(row.get("type_code", "")).upper() not in included_codes:
            continue
        total += int(row.get("extra_type_run", 0) or 0)
        total += int(row.get("extra_run", 0) or 0)
    return total


def iter_scorecard_innings(scorecards_dir: Path) -> list[tuple[str, str, int]]:
    innings_rows: list[tuple[str, str, int]] = []
    for json_path in sorted(scorecards_dir.glob("*.json")):
        if json_path.name in {"past_matches.json", "tournament_points_table.json"}:
            continue
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        for innings in payload.get("innings", []):
            innings_rows.append(
                (
                    payload["match_title"],
                    innings.get("teamName", ""),
                    scorecard_extras_without_byes(innings),
                )
            )
    return innings_rows


def main() -> int:
    args = parse_args()
    commentary_extras, match_labels = load_commentary_extras(args.commentary_dir)
    failures = 0

    for match_title, batting_team, scorecard_extras in iter_scorecard_innings(args.scorecards_dir):
        match_label = match_labels.get(match_title, match_title)
        commentary_total = commentary_extras.get((match_title, batting_team))
        if commentary_total is None:
            print(f"FAIL {match_label} | {batting_team} | missing commentary CSV")
            failures += 1
            continue
        if commentary_total == scorecard_extras:
            print(f"PASS {match_label} | {batting_team}")
            continue
        print(
            f"FAIL {match_label} | {batting_team} | scorecard={scorecard_extras} commentary={commentary_total}"
        )
        failures += 1

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
