#!/usr/bin/env python
"""Regression tests for commentary detail parsing."""

from __future__ import annotations

from extract_commentary_csv import enrich_rows, parse_detail, should_ignore_detail


def test_parse_detail_does_not_treat_wide_long_on_as_extra() -> None:
    parsed = parse_detail("FOUR, to Wide long-on")

    assert parsed["total_runs"] == "4"
    assert parsed["batsman_runs"] == "4"
    assert parsed["extras"] == "0"
    assert parsed["extra_type"] == ""
    assert parsed["is_boundary"] == "1"
    assert parsed["boundary_type"] == "FOUR"
    assert parsed["is_legal_ball"] == "1"


def test_parse_detail_keeps_actual_wide_extra() -> None:
    parsed = parse_detail("wide, 1 run")

    assert parsed["total_runs"] == "2"
    assert parsed["batsman_runs"] == "0"
    assert parsed["extras"] == "2"
    assert parsed["extra_type"] == "wide"
    assert parsed["is_legal_ball"] == "0"


def test_parse_detail_counts_additional_runs_after_wide_as_wides() -> None:
    parsed = parse_detail("wide, 4 runs")

    assert parsed["total_runs"] == "5"
    assert parsed["batsman_runs"] == "0"
    assert parsed["extras"] == "5"
    assert parsed["extra_type"] == "wide"
    assert parsed["is_legal_ball"] == "0"


def test_parse_detail_treats_no_ball_bye_as_no_ball_and_not_legal() -> None:
    parsed = parse_detail("(no ball) bye, 1 run")

    assert parsed["total_runs"] == "2"
    assert parsed["batsman_runs"] == "0"
    assert parsed["extras"] == "2"
    assert parsed["extra_type"] == "no_ball"
    assert parsed["is_legal_ball"] == "0"


def test_should_ignore_detail_for_retired_events() -> None:
    assert should_ignore_detail("no run, RETIRED HURT") is True
    assert should_ignore_detail("OUT Retired out") is True
    assert should_ignore_detail("OUT Caught out") is False


def test_enrich_rows_skips_retired_events() -> None:
    rows = [
        {
            "sequence": "1",
            "ball": "1.1",
            "bowler": "Bowler A",
            "batsman": "Batter A",
            "detail": "no run, RETIRED HURT",
            "commentary": "Bowler A to Batter A, no run, RETIRED HURT",
        },
        {
            "sequence": "2",
            "ball": "1.2",
            "bowler": "Bowler A",
            "batsman": "Batter B",
            "detail": "1 run",
            "commentary": "Bowler A to Batter B, 1 run",
        },
        {
            "sequence": "3",
            "ball": "1.3",
            "bowler": "Bowler A",
            "batsman": "Batter C",
            "detail": "OUT Retired out",
            "commentary": "Bowler A to Batter C, OUT Retired out",
        },
    ]

    enriched = enrich_rows(rows, "Team A")

    assert len(enriched) == 1
    assert enriched[0]["ball"] == "1.2"
