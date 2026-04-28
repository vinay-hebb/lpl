#!/usr/bin/env python

from __future__ import annotations

import json
from pathlib import Path

import build_match_commentary_csvs
from commentary_common import build_dated_pdf_name, parse_pdf_metadata


def test_build_dated_pdf_name_uses_batting_team_and_date_only() -> None:
    assert build_dated_pdf_name("Akatsuki Corps", "2026-04-10") == "Akatsuki Corps_2026-04-10.pdf"


def test_parse_pdf_metadata_supports_new_pdf_name() -> None:
    metadata = parse_pdf_metadata(Path("Akatsuki Corps_2026-04-10.pdf"))

    assert metadata == {
        "source_pdf": "Akatsuki Corps_2026-04-10.pdf",
        "batting_team_from_filename": "Akatsuki Corps",
        "match_date": "2026-04-10",
    }


def test_rename_pdf_with_date_reads_batting_team_from_pdf_text(monkeypatch, tmp_path: Path) -> None:
    pdf_path = tmp_path / "legacy_name.pdf"
    pdf_path.write_text("", encoding="utf-8")

    monkeypatch.setattr(
        build_match_commentary_csvs,
        "extract_text",
        lambda _: "Akatsuki Corps Full Commentary\nMatch Date 10/04/2026\n",
    )

    renamed_path = build_match_commentary_csvs.rename_pdf_with_date(pdf_path)

    assert renamed_path.name == "Akatsuki Corps_2026-04-10.pdf"
    assert renamed_path.exists()


def test_load_match_context_maps_date_and_batting_team_to_match(tmp_path: Path) -> None:
    scorecards_dir = tmp_path / "scorecards"
    scorecards_dir.mkdir()
    (scorecards_dir / "past_matches.json").write_text(
        json.dumps(
            [
                {
                    "match_id": 1,
                    "team_a": "Illuminati",
                    "team_b": "Minions",
                    "match_start_time": "2026-04-27T13:23:25.000Z",
                }
            ]
        ),
        encoding="utf-8",
    )
    (scorecards_dir / "1_illuminati-vs-minions.json").write_text(
        json.dumps(
            {
                "match_id": 1,
                "match_title": "Illuminati vs Minions",
                "innings": [
                    {"teamName": "Illuminati"},
                    {"teamName": "Minions"},
                ],
            }
        ),
        encoding="utf-8",
    )

    match_lookup, innings_order = build_match_commentary_csvs.load_match_context(scorecards_dir)

    assert match_lookup[("2026-04-27", "Illuminati")] == {
        "match_title": "Illuminati vs Minions",
        "team_1": "Illuminati",
        "team_2": "Minions",
        "match_date": "2026-04-27",
    }
    assert innings_order[("Illuminati vs Minions", "Minions")] == 2
