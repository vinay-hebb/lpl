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
from extract_commentary_csv import enrich_rows, extract_text, parse_commentary_rows


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


def load_innings_order(scorecards_dir: Path) -> dict[tuple[str, str], int]:
    innings_order: dict[tuple[str, str], int] = {}
    for json_path in sorted(scorecards_dir.glob("*.json")):
        if json_path.name in {"past_matches.json", "tournament_points_table.json"}:
            continue
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        match_title = payload.get("match_title", "")
        for index, innings in enumerate(payload.get("innings", []), start=1):
            team_name = (innings.get("teamName") or "").strip()
            if team_name:
                innings_order[(match_title, team_name)] = index
    return innings_order


def list_commentary_pdfs(input_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in input_dir.glob("*Ball-by-Ball Commentary*CricHeroes.pdf")
        if path.is_file()
    )


def rename_pdf_with_date(pdf_path: Path) -> Path:
    metadata = parse_pdf_metadata(pdf_path)
    if metadata.get("match_date"):
        return pdf_path

    text = extract_text(pdf_path)
    match_date = extract_match_date(text)
    new_name = build_dated_pdf_name(
        metadata["batting_team_from_filename"],
        metadata["team_1"],
        metadata["team_2"],
        match_date,
    )
    target_path = pdf_path.with_name(new_name)
    if target_path.exists():
        pdf_path.unlink()
        return target_path
    pdf_path.rename(target_path)
    return target_path


def collect_match_rows(
    pdf_paths: list[Path], innings_order: dict[tuple[str, str], int]
) -> dict[str, list[dict[str, str]]]:
    grouped_rows: dict[str, list[dict[str, str]]] = defaultdict(list)

    for pdf_path in pdf_paths:
        resolved_pdf_path = rename_pdf_with_date(pdf_path)
        metadata = parse_pdf_metadata(resolved_pdf_path)
        text = extract_text(resolved_pdf_path)
        batting_team, parsed_rows = parse_commentary_rows(text)
        enriched_rows = enrich_rows(parsed_rows, batting_team)
        innings_number = innings_order.get((metadata["match_title"], batting_team))
        if innings_number is None:
            innings_number = 1 if batting_team == metadata["team_1"] else 2

        match_key = build_match_key(
            metadata["team_1"], metadata["team_2"], metadata["match_date"]
        )
        match_label = build_match_label(
            metadata["team_1"], metadata["team_2"], metadata["match_date"]
        )

        for row in enriched_rows:
            row["innings_number"] = str(innings_number)
            row["match_key"] = match_key
            row["match_title"] = metadata["match_title"]
            row["match_date"] = metadata["match_date"]
            row["match_label"] = match_label
            row["team_1"] = metadata["team_1"]
            row["team_2"] = metadata["team_2"]
            row["source_pdf"] = resolved_pdf_path.name
            row["source_csv"] = build_match_csv_name(
                metadata["team_1"], metadata["team_2"], metadata["match_date"]
            )
            row["batting_team_from_filename"] = metadata["batting_team_from_filename"]
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
    innings_order = load_innings_order(args.scorecards_dir)
    pdf_paths = list_commentary_pdfs(args.input_dir)
    grouped_rows = collect_match_rows(pdf_paths, innings_order)

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
