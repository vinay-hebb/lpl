#!/usr/bin/env python

from __future__ import annotations

import json
from pathlib import Path
from urllib.error import URLError

import download_scorecards


def build_past_matches_html(match_id: int, team_a: str, team_b: str) -> str:
    payload = {
        "props": {
            "pageProps": {
                "matchResponse": {
                    "data": [
                        {
                            "match_id": match_id,
                            "team_a": team_a,
                            "team_b": team_b,
                            "tournament_name": "LifeSignals Premier League, 2026",
                            "match_summary": {"summary": f"{team_a} vs {team_b} done"},
                        }
                    ]
                }
            }
        }
    }
    return (
        '<script id="__NEXT_DATA__" type="application/json">'
        f"{json.dumps(payload)}"
        "</script>"
    )


def build_points_table_html() -> str:
    payload = {
        "props": {
            "pageProps": {
                "tournamentDetails": {
                    "data": {"standing_data": json.dumps([{"team_name": "Minions"}])}
                }
            }
        }
    }
    return (
        '<script id="__NEXT_DATA__" type="application/json">'
        f"{json.dumps(payload)}"
        "</script>"
    )


def build_scorecard_html() -> str:
    payload = {
        "props": {
            "pageProps": {
                "scorecard": [
                    {
                        "teamName": "Minions",
                        "inning": {
                            "inning": 1,
                            "total_run": 10,
                            "total_wicket": 1,
                            "total_extra": 0,
                            "overs_played": "2.0",
                        },
                        "extras": {},
                    }
                ]
            }
        }
    }
    return (
        '<script id="__NEXT_DATA__" type="application/json">'
        f"{json.dumps(payload)}"
        "</script>"
    )


def test_download_scorecards_refreshes_cached_match_index(monkeypatch, tmp_path: Path) -> None:
    old_html = build_past_matches_html(1, "Old Team", "Stale Team")
    fresh_html = build_past_matches_html(2, "Fresh Team", "New Team")
    points_html = build_points_table_html()
    scorecard_html = build_scorecard_html()
    output_dir = tmp_path / "scorecards"
    output_dir.mkdir()
    (output_dir / "past_matches.html").write_text(old_html, encoding="utf-8")
    (output_dir / "past_matches.json").write_text("[]", encoding="utf-8")

    def fake_fetch(url: str) -> str:
        if "points-table" in url:
            return points_html
        if "/scorecard/" in url:
            return scorecard_html
        return fresh_html

    monkeypatch.setattr(download_scorecards, "fetch_text", fake_fetch)

    result = download_scorecards.download_scorecards(
        "https://cricheroes.com/tournament/1957866/lifesignals-premier-league,-2026/matches/past-matches",
        output_dir,
    )

    assert result == 0
    matches = json.loads((output_dir / "past_matches.json").read_text(encoding="utf-8"))
    assert [match["match_id"] for match in matches] == [2]
    assert (output_dir / "2_fresh-team-vs-new-team.json").exists()


def test_fetch_text_with_cache_falls_back_to_existing_html(monkeypatch, tmp_path: Path) -> None:
    cache_path = tmp_path / "past_matches.html"
    cache_path.write_text("cached html", encoding="utf-8")

    def fake_fetch(_: str) -> str:
        raise URLError("offline")

    monkeypatch.setattr(download_scorecards, "fetch_text", fake_fetch)

    assert (
        download_scorecards.fetch_text_with_cache("https://example.com/past-matches", cache_path)
        == "cached html"
    )
