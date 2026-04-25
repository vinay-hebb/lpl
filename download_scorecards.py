#!/usr/bin/env python
"""Download tournament scorecards and points table from CricHeroes."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(?P<payload>.+?)</script>'
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download CricHeroes scorecards and points table into a local directory."
    )
    parser.add_argument(
        "--tournament-url",
        default="https://cricheroes.com/tournament/1957866/lifesignals-premier-league,-2026/matches/past-matches",
        help="Tournament past-matches URL",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("scorecards"),
        help="Directory for downloaded scorecards and points table",
    )
    return parser.parse_args()


def fetch_text(url: str) -> str:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


def read_cached_text(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def fetch_text_with_cache(url: str, cache_path: Path) -> str:
    try:
        text = fetch_text(url)
    except (HTTPError, URLError, TimeoutError):
        cached_text = read_cached_text(cache_path)
        if cached_text is not None:
            return cached_text
        raise
    cache_path.write_text(text, encoding="utf-8")
    return text


def extract_next_data(html_text: str) -> dict:
    match = NEXT_DATA_RE.search(html_text)
    if match is None:
        raise ValueError("Could not find __NEXT_DATA__ payload")
    return json.loads(match.group("payload"))


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def parse_tournament_context(tournament_url: str) -> tuple[str, str]:
    parts = [part for part in urlparse(tournament_url).path.split("/") if part]
    if len(parts) < 3 or parts[0] != "tournament":
        raise ValueError(f"Unexpected tournament URL: {tournament_url}")
    return parts[1], parts[2]


def normalize_scorecard_payload(match: dict, page_payload: dict, scorecard_url: str) -> dict:
    page_props = page_payload["props"]["pageProps"]
    scorecard_rows = page_props.get("scorecard", [])
    innings_payload = []
    for row in scorecard_rows:
        extras = row.get("extras", {})
        innings = row.get("inning", {})
        innings_payload.append(
            {
                "teamName": row.get("teamName", ""),
                "inning": innings.get("inning"),
                "total_run": innings.get("total_run"),
                "total_wicket": innings.get("total_wicket"),
                "total_extra": innings.get("total_extra"),
                "overs_played": innings.get("overs_played"),
                "extras": extras,
            }
        )

    return {
        "match_id": match["match_id"],
        "match_title": f"{match['team_a']} vs {match['team_b']}",
        "tournament_name": match["tournament_name"],
        "scorecard_url": scorecard_url,
        "result": match["match_summary"]["summary"],
        "innings": innings_payload,
    }


def download_points_table(tournament_id: str, tournament_slug: str, output_dir: Path) -> None:
    points_url = (
        f"https://cricheroes.com/tournament/{tournament_id}/{tournament_slug}/points-table"
    )
    html_path = output_dir / "tournament_points_table.html"
    json_path = output_dir / "tournament_points_table.json"

    html_text = fetch_text_with_cache(points_url, html_path)
    payload = extract_next_data(html_text)
    details = payload["props"]["pageProps"]["tournamentDetails"]["data"]
    standings = json.loads(details.get("standing_data", "[]"))

    json_path.write_text(
        json.dumps(
            {
                "tournament_id": tournament_id,
                "tournament_slug": tournament_slug,
                "points_table_url": points_url,
                "standings": standings,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def download_scorecards(tournament_url: str, output_dir: Path) -> int:
    tournament_id, tournament_slug = parse_tournament_context(tournament_url)
    output_dir.mkdir(parents=True, exist_ok=True)

    past_matches_html_path = output_dir / "past_matches.html"
    past_matches_json_path = output_dir / "past_matches.json"

    tournament_html = fetch_text_with_cache(tournament_url, past_matches_html_path)
    tournament_payload = extract_next_data(tournament_html)
    matches = tournament_payload["props"]["pageProps"]["matchResponse"]["data"]
    past_matches_json_path.write_text(json.dumps(matches, indent=2), encoding="utf-8")

    download_points_table(tournament_id, tournament_slug, output_dir)

    downloaded = 0
    skipped = 0
    for match in matches:
        match_slug = f"{slugify(match['team_a'])}-vs-{slugify(match['team_b'])}"
        scorecard_url = (
            f"https://cricheroes.com/scorecard/{match['match_id']}/"
            f"{tournament_slug}/{match_slug}/scorecard"
        )
        prefix = f"{match['match_id']}_{match_slug}"
        html_path = output_dir / f"{prefix}.html"
        json_path = output_dir / f"{prefix}.json"
        if html_path.exists() and json_path.exists():
            skipped += 1
            continue

        html_text = fetch_text(scorecard_url)
        payload = extract_next_data(html_text)
        normalized = normalize_scorecard_payload(match, payload, scorecard_url)

        html_path.write_text(html_text, encoding="utf-8")
        json_path.write_text(json.dumps(normalized, indent=2), encoding="utf-8")
        downloaded += 1

    print(f"Downloaded {downloaded} scorecards into {output_dir} (skipped {skipped})")
    return 0


def main() -> int:
    args = parse_args()
    try:
        return download_scorecards(args.tournament_url, args.output_dir)
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        print(f"{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
