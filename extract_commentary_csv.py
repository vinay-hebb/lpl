#!/usr/bin/env python
"""Extract CricHeroes ball-by-ball commentary from a PDF into CSV."""

from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
from pathlib import Path


BALL_RE = re.compile(r"^\d+\.\d+$")
COMMENTARY_RE = re.compile(r"^(?P<bowler>.+?) to (?P<batsman>.+?), (?P<detail>.+)$")
RUNS_RE = re.compile(r"(?<!# )\b(\d+) run(?:s)?\b", re.IGNORECASE)

NOISE_PREFIXES = (
    "END OF OVER",
    "Score all your matches for FREE!",
    "Unlock CricInsights",
    "About ",
    "Summary ",
    "Past",
    "Match",
    "Officials",
    "Match details",
    "Series Name",
    "Match Date",
    "Location",
    "Last Updated",
    "Toss:",
)
NOISE_EXACT = {
    "cricheroes",
    "TM",
    "Your cricket matters",
    "NEXT",
    "RHB",
    "LHB",
    "Start",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract ball-by-ball commentary from a CricHeroes PDF into CSV."
    )
    parser.add_argument("pdf_path", type=Path, help="Input CricHeroes PDF path")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("ball_by_ball_commentary.csv"),
        help="Output CSV path",
    )
    return parser.parse_args()


def extract_text(pdf_path: Path) -> str:
    cmd = ["pdftotext", "-raw", str(pdf_path), "-"]
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return result.stdout.replace("\f", "\n")


def normalize_lines(text: str) -> list[str]:
    return [line.strip(" \t,") for line in text.splitlines() if line.strip()]


def is_noise_line(line: str) -> bool:
    if not line:
        return True
    if line in NOISE_EXACT:
        return True
    if line.endswith(" Full Commentary"):
        return True
    if any(line.startswith(prefix) for prefix in NOISE_PREFIXES):
        return True
    if "comes into the attack from over the wicket" in line:
        return True
    if "comes into the attack from round the wicket" in line:
        return True
    if "Become a CricHeroes Pro." in line:
        return True
    if re.search(r"\bMAT:\b", line):
        return True
    if re.search(r"\bAVG:\b", line):
        return True
    if re.search(r"\bECO:\b", line):
        return True
    if re.search(r"\bBEST:\b", line):
        return True
    if re.search(r"\b\d+\s\(\d+\)\b", line):
        return True
    if re.search(r"^\d+\sRuns\s\d+\sWkts$", line):
        return True
    if re.search(r"^\d+\s-\s\d+\s-\s\d+\s-\s\d+$", line):
        return True
    return False


def is_probable_card_label(current_line: str, next_line: str) -> bool:
    if not re.fullmatch(r"[A-Za-z .]+", current_line):
        return False
    if next_line in {"RHB", "LHB", "NEXT"}:
        return True
    if "Right-arm" in next_line or "Left-arm" in next_line:
        return True
    if re.search(r"\bMAT:\b", next_line):
        return True
    return False


def find_batting_team(lines: list[str]) -> str:
    for line in lines:
        if line.endswith(" Full Commentary"):
            return line[: -len(" Full Commentary")].strip()
    return ""


def collect_continuation(lines: list[str], start_index: int) -> tuple[str, int]:
    extra_parts: list[str] = []
    index = start_index
    while index < len(lines):
        line = lines[index]
        next_line = lines[index + 1] if index + 1 < len(lines) else ""
        if BALL_RE.match(line) or is_noise_line(line) or COMMENTARY_RE.match(line):
            break
        if is_probable_card_label(line, next_line):
            break
        extra_parts.append(line)
        index += 1
    return " ".join(extra_parts).strip(), index


def parse_commentary_rows(text: str) -> tuple[str, list[dict[str, str]]]:
    lines = normalize_lines(text)
    batting_team = find_batting_team(lines)
    rows: list[dict[str, str]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not BALL_RE.match(line):
            index += 1
            continue

        if index + 1 >= len(lines):
            index += 1
            continue

        commentary_line = lines[index + 1]
        if not COMMENTARY_RE.match(commentary_line):
            index += 1
            continue

        continuation, next_index = collect_continuation(lines, index + 2)
        full_commentary = commentary_line
        if continuation:
            full_commentary = f"{full_commentary} {continuation}"

        match = COMMENTARY_RE.match(commentary_line)
        assert match is not None
        row = {
            "ball": line,
            "bowler": match.group("bowler").strip(),
            "batsman": match.group("batsman").strip(),
            "detail": match.group("detail").strip(),
            "commentary": full_commentary.strip(),
            "batting_team": batting_team,
        }
        rows.append(row)
        index = next_index

    rows.reverse()
    for order, row in enumerate(rows, start=1):
        row["sequence"] = str(order)
    return batting_team, rows


def parse_detail(detail: str) -> dict[str, str]:
    lowered = detail.lower()
    total_runs = ""
    batsman_runs = ""
    extras = ""
    extra_type = ""
    is_boundary = "0"
    boundary_type = ""
    is_wicket = "0"
    dismissal_kind = ""
    has_no_ball = "no ball" in lowered
    has_leg_bye = "leg bye" in lowered
    has_bye = "bye" in lowered

    detail_segments = [segment.strip(" ()") for segment in lowered.split(",")]

    if any(segment == "wide" for segment in detail_segments):
        extra_type = "wide"
        total_runs = "1"
        batsman_runs = "0"
    elif has_no_ball:
        extra_type = "no_ball"
        total_runs = "1"
        batsman_runs = "0"
    elif has_leg_bye:
        extra_type = "leg_bye"
    elif has_bye:
        extra_type = "bye"

    if "four" in lowered:
        is_boundary = "1"
        boundary_type = "FOUR"
        total_runs = "4"
        if not extra_type:
            batsman_runs = "4"

    if "six" in lowered:
        is_boundary = "1"
        boundary_type = "SIX"
        total_runs = "6"
        if not extra_type:
            batsman_runs = "6"

    runs_match = RUNS_RE.search(detail)
    if runs_match:
        parsed_runs = int(runs_match.group(1))
        if extra_type == "wide":
            total_runs = str(parsed_runs + 1)
            batsman_runs = "0"
        elif extra_type == "no_ball":
            total_runs = str(parsed_runs + 1)
            batsman_runs = "0"
        else:
            total_runs = str(parsed_runs)
        if extra_type in {"bye", "leg_bye"}:
            batsman_runs = "0"
        elif extra_type == "no_ball" and parsed_runs >= 1:
            batsman_runs = "0"
        elif not batsman_runs:
            batsman_runs = str(parsed_runs)

    if "no run" in lowered and not total_runs:
        total_runs = "0"
        batsman_runs = "0"

    if "retired hurt" in lowered:
        is_wicket = "1"
        dismissal_kind = "retired_hurt"
    elif "run out" in lowered:
        is_wicket = "1"
        dismissal_kind = "run_out"
    elif "out" in lowered:
        is_wicket = "1"
        dismissal_kind = "out"

    if total_runs and batsman_runs:
        extras = str(int(total_runs) - int(batsman_runs))

    return {
        "total_runs": total_runs,
        "batsman_runs": batsman_runs,
        "extras": extras,
        "extra_type": extra_type,
        "is_boundary": is_boundary,
        "boundary_type": boundary_type,
        "is_wicket": is_wicket,
        "dismissal_kind": dismissal_kind,
        "is_legal_ball": "0" if extra_type in {"wide", "no_ball"} else "1",
    }


def should_ignore_detail(detail: str) -> bool:
    lowered = detail.lower()
    return "retired hurt" in lowered or "retired out" in lowered


def enrich_rows(rows: list[dict[str, str]], batting_team: str) -> list[dict[str, str]]:
    enriched: list[dict[str, str]] = []
    for row in rows:
        if should_ignore_detail(row["detail"]):
            continue
        over_str, ball_in_over = row["ball"].split(".")
        detail_fields = parse_detail(row["detail"])
        enriched.append(
            {
                "sequence": row["sequence"],
                "innings_number": "",
                "batting_team": batting_team,
                "ball": row["ball"],
                "over_number": over_str,
                "ball_in_over": ball_in_over,
                "bowler": row["bowler"],
                "batsman": row["batsman"],
                "detail": row["detail"],
                "commentary": row["commentary"],
                **detail_fields,
            }
        )
    return enriched


def write_csv(rows: list[dict[str, str]], output_path: Path) -> None:
    fieldnames = [
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
    text = extract_text(args.pdf_path)
    batting_team, parsed_rows = parse_commentary_rows(text)
    enriched_rows = enrich_rows(parsed_rows, batting_team)
    write_csv(enriched_rows, args.output)
    print(f"Wrote {len(enriched_rows)} deliveries to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
