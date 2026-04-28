#!/usr/bin/env python
"""Rename commentary PDFs with match dates and build one CSV per match."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

from commentary_common import (
    build_dated_pdf_name,
    build_match_csv_name,
    build_match_key,
    build_match_label,
    extract_match_date,
    parse_pdf_metadata,
)
from extract_commentary_csv import enrich_rows, extract_text, find_batting_team, parse_commentary_rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rename commentary PDFs with dates and emit one commentary CSV per match."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("ball-by-ball"),
        help="Directory containing CricHeroes ball-by-ball commentary PDFs",
    )
    parser.add_argument(
        "--scorecards-dir",
        type=Path,
        default=Path("scorecards"),
        help="Directory containing normalized scorecard JSON files",
    )
    return parser.parse_args()


def split_match_title(match_title: str) -> tuple[str, str]:
    if " vs " not in match_title:
        raise ValueError(f"Could not split match title: {match_title}")
    team_1, team_2 = match_title.split(" vs ", 1)
    return team_1.strip(), team_2.strip()


def load_match_context(
    scorecards_dir: Path,
) -> tuple[dict[tuple[str, str], dict[str, str]], dict[tuple[str, str], int]]:
    match_lookup: dict[tuple[str, str], dict[str, str]] = {}
    innings_order: dict[tuple[str, str], int] = {}
    past_matches_path = scorecards_dir / "past_matches.json"
    past_matches = []
    if past_matches_path.exists():
        past_matches = json.loads(past_matches_path.read_text(encoding="utf-8"))
    match_info_by_id = {}
    for match in past_matches:
        match_id = match.get("match_id")
        if match_id is None:
            continue
        match_info_by_id[int(match_id)] = {
            "match_date": str(match.get("match_start_time", ""))[:10],
            "team_1": str(match.get("team_a", "")).strip(),
            "team_2": str(match.get("team_b", "")).strip(),
        }

    for json_path in sorted(scorecards_dir.glob("*.json")):
        if json_path.name in {"past_matches.json", "tournament_points_table.json"}:
            continue
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        match_title = str(payload.get("match_title", "")).strip()
        match_id = payload.get("match_id")
        team_1 = ""
        team_2 = ""
        match_date = ""
        if match_id is not None and int(match_id) in match_info_by_id:
            info = match_info_by_id[int(match_id)]
            team_1 = info["team_1"]
            team_2 = info["team_2"]
            match_date = info["match_date"]
        elif match_title:
            team_1, team_2 = split_match_title(match_title)
        for index, innings in enumerate(payload.get("innings", []), start=1):
            team_name = (innings.get("teamName") or "").strip()
            if team_name:
                innings_order[(match_title, team_name)] = index
                if match_date and team_1 and team_2:
                    match_lookup[(match_date, team_name)] = {
                        "match_title": f"{team_1} vs {team_2}",
                        "team_1": team_1,
                        "team_2": team_2,
                        "match_date": match_date,
                    }
    return match_lookup, innings_order


def list_commentary_pdfs(input_dir: Path) -> list[Path]:
    return sorted(path for path in input_dir.glob("*.pdf") if path.is_file())


def rename_pdf_with_date(pdf_path: Path) -> Path:
    text = extract_text(pdf_path)
    batting_team = find_batting_team(text.splitlines())
    if not batting_team:
        raise ValueError(f"Could not find batting team in {pdf_path.name}")
    match_date = extract_match_date(text)
    new_name = build_dated_pdf_name(batting_team, match_date)
    target_path = pdf_path.with_name(new_name)
    if target_path == pdf_path:
        return target_path
    if target_path.exists():
        pdf_path.unlink()
        return target_path
    pdf_path.rename(target_path)
    return target_path


def collect_match_rows(
    pdf_paths: list[Path],
    match_lookup: dict[tuple[str, str], dict[str, str]],
    innings_order: dict[tuple[str, str], int],
) -> dict[str, list[dict[str, str]]]:
    grouped_rows: dict[str, list[dict[str, str]]] = defaultdict(list)

    for pdf_path in pdf_paths:
        resolved_pdf_path = rename_pdf_with_date(pdf_path)
        metadata = parse_pdf_metadata(resolved_pdf_path)
        text = extract_text(resolved_pdf_path)
        batting_team, parsed_rows = parse_commentary_rows(text)
        if not batting_team:
            batting_team = metadata["batting_team_from_filename"]
        match_metadata = match_lookup.get((metadata["match_date"], batting_team))
        if match_metadata is None:
            raise ValueError(
                f"Could not resolve match metadata for batting team {batting_team} on {metadata['match_date']}"
            )
        enriched_rows = enrich_rows(parsed_rows, batting_team)
        innings_number = innings_order.get((match_metadata["match_title"], batting_team))
        if innings_number is None:
            innings_number = 1 if batting_team == match_metadata["team_1"] else 2

        match_key = build_match_key(
            match_metadata["team_1"], match_metadata["team_2"], match_metadata["match_date"]
        )
        match_label = build_match_label(
            match_metadata["team_1"], match_metadata["team_2"], match_metadata["match_date"]
        )

        for row in enriched_rows:
            row["innings_number"] = str(innings_number)
            row["match_key"] = match_key
            row["match_title"] = match_metadata["match_title"]
            row["match_date"] = match_metadata["match_date"]
            row["match_label"] = match_label
            row["team_1"] = match_metadata["team_1"]
            row["team_2"] = match_metadata["team_2"]
            row["source_pdf"] = resolved_pdf_path.name
            row["source_csv"] = build_match_csv_name(
                match_metadata["team_1"], match_metadata["team_2"], match_metadata["match_date"]
            )
            row["batting_team_from_filename"] = batting_team
            grouped_rows[match_key].append(row)

    return grouped_rows


def sort_match_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    sorted_rows = sorted(
        rows,
        key=lambda row: (
            int(row["innings_number"] or 0),
            int(row["sequence"] or 0),
        ),
    )
    for row_index, row in enumerate(sorted_rows, start=1):
        row["match_row_id"] = f"{row['match_key']}__{row_index}"
    return sorted_rows


def write_match_csv(output_path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = [
        "match_row_id",
        "match_key",
        "match_title",
        "match_date",
        "match_label",
        "team_1",
        "team_2",
        "source_pdf",
        "source_csv",
        "batting_team_from_filename",
        "sequence",
        "innings_number",
        "batting_team",
        "ball",
        "over_number",
        "ball_in_over",
        "bowler",
        "batsman",
        "detail",
        "commentary",
        "total_runs",
        "batsman_runs",
        "extras",
        "extra_type",
        "is_boundary",
        "boundary_type",
        "is_wicket",
        "dismissal_kind",
        "is_legal_ball",
    ]
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    match_lookup, innings_order = load_match_context(args.scorecards_dir)
    pdf_paths = list_commentary_pdfs(args.input_dir)
    grouped_rows = collect_match_rows(pdf_paths, match_lookup, innings_order)

    for csv_path in args.input_dir.glob("*.csv"):
        csv_path.unlink()

    written = 0
    for match_rows in grouped_rows.values():
        sorted_rows = sort_match_rows(match_rows)
        first_row = sorted_rows[0]
        output_path = args.input_dir / first_row["source_csv"]
        write_match_csv(output_path, sorted_rows)
        written += 1

    print(f"Wrote {written} match CSV files into {args.input_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
