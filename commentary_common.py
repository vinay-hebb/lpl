#!/usr/bin/env python
"""Shared filename and match metadata helpers for CricHeroes commentary assets."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path


LEGACY_PDF_NAME_RE = re.compile(
    r"^(?P<batting_team>.+?)_Ball-by-Ball Commentary & Live Score_ "
    r"(?P<team_1>.+?) vs (?P<team_2>.+?)"
    r"(?: _ (?P<match_date>\d{4}-\d{2}-\d{2}))? _ CricHeroes$"
)
PDF_NAME_RE = re.compile(r"^(?P<batting_team>.+)_(?P<match_date>\d{4}-\d{2}-\d{2})$")
MATCH_CSV_RE = re.compile(
    r"^Ball-by-Ball Commentary & Live Score_ "
    r"(?P<team_1>.+?) vs (?P<team_2>.+?) _ (?P<match_date>\d{4}-\d{2}-\d{2}) _ CricHeroes$"
)
MATCH_DATE_RE = re.compile(r"Match Date\s+(?P<date>\d{2}/\d{2}/\d{4})")
HEADER_DATE_RE = re.compile(r",\s*(?P<date>\d{2}-[A-Za-z]{3}-\d{2})")


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def build_match_key(team_1: str, team_2: str, match_date: str) -> str:
    return slugify(f"{match_date}_{team_1}_vs_{team_2}")


def build_match_label(team_1: str, team_2: str, match_date: str) -> str:
    return f"{team_1} vs {team_2} ({match_date})"


def extract_match_date(text: str) -> str:
    squashed = " ".join(text.split())
    match = MATCH_DATE_RE.search(squashed)
    if match is not None:
        return datetime.strptime(match.group("date"), "%d/%m/%Y").strftime("%Y-%m-%d")

    header_match = HEADER_DATE_RE.search(squashed)
    if header_match is not None:
        return datetime.strptime(header_match.group("date"), "%d-%b-%y").strftime("%Y-%m-%d")

    raise ValueError("Could not find match date in commentary text")


def parse_pdf_metadata(pdf_path: Path) -> dict[str, str]:
    match = PDF_NAME_RE.match(pdf_path.stem)
    if match is not None:
        batting_team = match.group("batting_team").strip()
        match_date = match.group("match_date").strip()
        return {
            "source_pdf": pdf_path.name,
            "batting_team_from_filename": batting_team,
            "match_date": match_date,
        }

    legacy_match = LEGACY_PDF_NAME_RE.match(pdf_path.stem)
    if legacy_match is None:
        raise ValueError(f"Could not parse PDF metadata from {pdf_path.name}")

    team_1 = legacy_match.group("team_1").strip()
    team_2 = legacy_match.group("team_2").strip()
    match_date = (legacy_match.group("match_date") or "").strip()
    metadata = {
        "source_pdf": pdf_path.name,
        "batting_team_from_filename": legacy_match.group("batting_team").strip(),
        "match_title": f"{team_1} vs {team_2}",
        "team_1": team_1,
        "team_2": team_2,
        "match_date": match_date,
    }
    if match_date:
        metadata["match_key"] = build_match_key(team_1, team_2, match_date)
        metadata["match_label"] = build_match_label(team_1, team_2, match_date)
    return metadata


def parse_match_csv_metadata(csv_path: Path) -> dict[str, str]:
    match = MATCH_CSV_RE.match(csv_path.stem)
    if match is None:
        raise ValueError(f"Could not parse match CSV metadata from {csv_path.name}")

    team_1 = match.group("team_1").strip()
    team_2 = match.group("team_2").strip()
    match_date = match.group("match_date").strip()
    return {
        "source_csv": csv_path.name,
        "match_title": f"{team_1} vs {team_2}",
        "team_1": team_1,
        "team_2": team_2,
        "match_date": match_date,
        "match_key": build_match_key(team_1, team_2, match_date),
        "match_label": build_match_label(team_1, team_2, match_date),
    }


def build_dated_pdf_name(batting_team: str, match_date: str) -> str:
    return f"{batting_team}_{match_date}.pdf"


def build_match_csv_name(team_1: str, team_2: str, match_date: str) -> str:
    return (
        f"Ball-by-Ball Commentary & Live Score_ "
        f"{team_1} vs {team_2} _ {match_date} _ CricHeroes.csv"
    )
