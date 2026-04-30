#!/usr/bin/env python
"""Dash app for CricHeroes ball-by-ball analytics and tournament points table."""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

import dash
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import yaml
from dash import Input, Output, dash_table, dcc, html

from commentary_common import parse_match_csv_metadata


DROP_RE = re.compile(r"dropped by\s+(?P<fielder>[^,#]+)", re.IGNORECASE)
NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(?P<payload>.+?)</script>'
)
APP_VERSION = "0.4.0"
LAST_UPDATED = "2026-04-28 19:45 IST"
VERSION_LOG_HREF = "/assets/version_log.md"
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a Dash app for commentary analytics and tournament points table."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("ball-by-ball"),
        help="Directory containing per-match ball-by-ball CSVs",
    )
    parser.add_argument(
        "--points-table",
        type=Path,
        default=Path("scorecards/tournament_points_table.json"),
        help="Tournament points table JSON exported by download_scorecards.py",
    )
    parser.add_argument(
        "--figure-config",
        type=Path,
        default=Path("dashboard_figures.yaml"),
        help="YAML file controlling which figures/tables are visible",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Dash host")
    parser.add_argument("--port", type=int, default=8050, help="Dash port")
    parser.add_argument(
        "--no-reload",
        action="store_true",
        help="Disable Dash hot reload during development",
    )
    return parser.parse_args()


def list_commentary_csvs(input_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in input_dir.glob("Ball-by-Ball Commentary & Live Score_*.csv")
        if path.is_file()
    )


def load_commentary_data(input_dir: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for csv_path in list_commentary_csvs(input_dir):
        frame = pd.read_csv(csv_path)
        if frame.empty:
            continue
        if "match_key" not in frame.columns or "match_date" not in frame.columns:
            metadata = parse_match_csv_metadata(csv_path)
            for key, value in metadata.items():
                frame[key] = value
        frame["source_csv"] = csv_path.name
        frames.append(frame)

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    numeric_columns = [
        "extras",
        "total_runs",
        "batsman_runs",
        "is_boundary",
        "is_wicket",
        "is_legal_ball",
        "over_number",
        "ball_in_over",
    ]
    for column in numeric_columns:
        df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0)

    text_columns = [
        "extra_type",
        "commentary",
        "detail",
        "batting_team",
        "bowler",
        "batsman",
        "match_label",
    ]
    for column in text_columns:
        df[column] = df[column].fillna("")

    df["extra_type"] = df["extra_type"].replace("", "none")
    df["over_label"] = df["over_number"].astype(int).astype(str)
    df["bowling_team"] = df.apply(resolve_bowling_team, axis=1)
    df["boundary_runs"] = df.apply(compute_boundary_runs, axis=1)
    df["non_boundary_bat_runs"] = (df["batsman_runs"] - df["boundary_runs"]).clip(lower=0)
    df["wide_runs"] = df.apply(lambda row: row["extras"] if row["extra_type"] == "wide" else 0, axis=1)
    df["no_ball_runs"] = df.apply(
        lambda row: row["extras"] if row["extra_type"] == "no_ball" else 0, axis=1
    )
    df["bye_runs"] = df.apply(lambda row: row["extras"] if row["extra_type"] == "bye" else 0, axis=1)
    df["leg_bye_runs"] = df.apply(
        lambda row: row["extras"] if row["extra_type"] == "leg_bye" else 0, axis=1
    )
    df["wide_events"] = (df["extra_type"] == "wide").astype(int)
    df["no_ball_events"] = (df["extra_type"] == "no_ball").astype(int)
    df["drop_fielder"] = df["commentary"].apply(extract_drop_fielder)
    df["is_dropped_catch"] = df["drop_fielder"].ne("")
    df["wicket_type"] = df.apply(resolve_wicket_type, axis=1)
    df = repair_commentary_match_metadata(df, Path("scorecards") / "past_matches.json")
    return df


def load_points_table(points_table_path: Path) -> pd.DataFrame:
    if not points_table_path.exists():
        return pd.DataFrame()

    payload = json.loads(points_table_path.read_text(encoding="utf-8"))
    standings = payload.get("standings", [])
    if not standings:
        return pd.DataFrame()

    points_df = pd.DataFrame(standings)
    numeric_columns = ["Matches", "Won", "Lost", "Points", "ForRuns", "AgainstRuns"]
    for column in numeric_columns:
        if column in points_df.columns:
            points_df[column] = pd.to_numeric(points_df[column], errors="coerce").fillna(0)

    if "Net RR" in points_df.columns:
        points_df["Net RR"] = pd.to_numeric(points_df["Net RR"], errors="coerce").fillna(0.0)

    points_df = apply_scorecard_points_overrides(points_df, points_table_path.parent)
    return points_df.sort_values(["Points", "Net RR"], ascending=[False, False])


def parse_overs_to_balls(overs_value: str | float | int) -> int | None:
    text = str(overs_value).strip()
    if not text or text.lower() == "nan":
        return None
    if "." not in text:
        try:
            return int(text) * 6
        except ValueError:
            return None
    overs_text, balls_text = text.split(".", 1)
    try:
        overs = int(overs_text)
        balls = int(balls_text)
    except ValueError:
        return None
    if balls < 0 or balls > 5:
        return None
    return overs * 6 + balls


def format_balls_as_overs(balls: int) -> str:
    overs = balls // 6
    remaining_balls = balls % 6
    if remaining_balls == 0:
        return f"{overs}"
    return f"{overs}.{remaining_balls}"


def classify_result_code(match_payload: dict, team_name: str) -> str | None:
    winning_team = str(match_payload.get("winning_team", "")).strip()
    match_result = str(match_payload.get("match_result", "")).strip().lower()
    summary = str(match_payload.get("match_summary", {}).get("summary", "")).strip().lower()

    if match_result == "resulted" and winning_team:
        return "W" if team_name == winning_team else "L"
    if "tie" in summary:
        return "T"
    if "draw" in summary:
        return "D"
    if "no result" in summary or match_result in {"abandoned", "cancelled"}:
        return "N"
    return None


def load_scorecard_derivations(scorecards_dir: Path) -> dict[str, dict[str, object]]:
    past_matches_path = scorecards_dir / "past_matches.json"
    if not past_matches_path.exists():
        return {}

    try:
        past_matches = json.loads(past_matches_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}

    scorecard_payload_by_match_id: dict[int, dict] = {}
    for scorecard_path in sorted(scorecards_dir.glob("*.json")):
        if scorecard_path.name in {"past_matches.json", "tournament_points_table.json"}:
            continue
        try:
            payload = json.loads(scorecard_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        match_id = payload.get("match_id")
        if match_id is None:
            continue
        try:
            scorecard_payload_by_match_id[int(match_id)] = payload
        except (TypeError, ValueError):
            continue

    team_rows: dict[str, dict[str, object]] = {}
    team_results: dict[str, list[tuple[str, str]]] = defaultdict(list)
    team_run_rate_totals: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "for_runs": 0,
            "for_balls": 0,
            "against_runs": 0,
            "against_balls": 0,
        }
    )

    for match in past_matches:
        team_names = [str(match.get("team_a", "")).strip(), str(match.get("team_b", "")).strip()]
        if any(not team_name for team_name in team_names):
            continue

        match_id = match.get("match_id")
        try:
            match_id_int = int(match_id)
        except (TypeError, ValueError):
            continue
        scorecard_payload = scorecard_payload_by_match_id.get(match_id_int)
        if scorecard_payload is None:
            continue

        innings_by_team = {
            str(innings.get("teamName", "")).strip(): innings
            for innings in scorecard_payload.get("innings", [])
            if str(innings.get("teamName", "")).strip()
        }
        scheduled_overs = match.get("overs")
        scheduled_balls = match.get("balls", 0)
        try:
            scheduled_total_balls = int(scheduled_overs) * 6 + int(scheduled_balls)
        except (TypeError, ValueError):
            scheduled_total_balls = None

        match_time = str(match.get("match_end_time") or match.get("match_start_time") or "")

        for team_name in team_names:
            team_row = team_rows.setdefault(
                team_name,
                {"Matches": 0, "Won": 0, "Lost": 0, "_can_derive_points": True},
            )
            team_row["Matches"] += 1
            result_code = classify_result_code(match, team_name)
            if result_code == "W":
                team_row["Won"] += 1
            elif result_code == "L":
                team_row["Lost"] += 1
            else:
                team_row["_can_derive_points"] = False

            if result_code is not None:
                team_results[team_name].append((match_time, result_code))

            innings = innings_by_team.get(team_name)
            opponent_name = next((name for name in team_names if name != team_name), "")
            opponent_innings = innings_by_team.get(opponent_name)
            if innings is None or opponent_innings is None:
                continue

            team_runs = innings.get("total_run")
            opponent_runs = opponent_innings.get("total_run")
            team_balls = parse_overs_to_balls(innings.get("overs_played", ""))
            opponent_balls = parse_overs_to_balls(opponent_innings.get("overs_played", ""))
            try:
                team_wickets = int(innings.get("total_wicket"))
                opponent_wickets = int(opponent_innings.get("total_wicket"))
                team_runs = int(team_runs)
                opponent_runs = int(opponent_runs)
            except (TypeError, ValueError):
                continue

            if team_balls is None or opponent_balls is None:
                continue
            if scheduled_total_balls is not None and team_wickets >= 10:
                team_balls = scheduled_total_balls
            if scheduled_total_balls is not None and opponent_wickets >= 10:
                opponent_balls = scheduled_total_balls

            team_run_rate_totals[team_name]["for_runs"] += team_runs
            team_run_rate_totals[team_name]["for_balls"] += team_balls
            team_run_rate_totals[team_name]["against_runs"] += opponent_runs
            team_run_rate_totals[team_name]["against_balls"] += opponent_balls

    derivations: dict[str, dict[str, object]] = {}
    for team_name, row in team_rows.items():
        derived_row: dict[str, object] = {
            "Matches": row["Matches"],
            "Won": row["Won"],
            "Lost": row["Lost"],
        }
        if row["_can_derive_points"]:
            derived_row["Points"] = row["Won"] * 2

        run_rate_totals = team_run_rate_totals.get(team_name)
        if run_rate_totals and run_rate_totals["for_balls"] > 0 and run_rate_totals["against_balls"] > 0:
            batting_rr = run_rate_totals["for_runs"] * 6.0 / run_rate_totals["for_balls"]
            bowling_rr = run_rate_totals["against_runs"] * 6.0 / run_rate_totals["against_balls"]
            derived_row["Net RR"] = batting_rr - bowling_rr
            derived_row["For"] = (
                f"{run_rate_totals['for_runs']}/{format_balls_as_overs(run_rate_totals['for_balls'])}"
            )
            derived_row["Against"] = (
                f"{run_rate_totals['against_runs']}/{format_balls_as_overs(run_rate_totals['against_balls'])}"
            )

        results = team_results.get(team_name, [])
        if results:
            results = sorted(results, key=lambda item: item[0], reverse=True)
            derived_row["Last 5"] = "-".join(result_code for _, result_code in results[:5])

        derivations[team_name] = derived_row

    return derivations


def apply_scorecard_points_overrides(points_df: pd.DataFrame, scorecards_dir: Path) -> pd.DataFrame:
    derivations = load_scorecard_derivations(scorecards_dir)
    if not derivations or "Team Name" not in points_df.columns:
        return points_df

    merged_df = points_df.copy()
    derived_columns: set[str] = set()

    for row_index, team_name in merged_df["Team Name"].items():
        team_derivations = derivations.get(str(team_name).strip())
        if not team_derivations:
            continue
        for column_name, derived_value in team_derivations.items():
            merged_df.at[row_index, column_name] = derived_value
            derived_columns.add(column_name)

    merged_df.attrs["derived_points_columns"] = sorted(derived_columns)
    return merged_df


def load_figure_config(config_path: Path) -> dict[str, bool]:
    default_config = {
        "team_extras_total_stack": True,
        "team_keeper_byes_efficiency_bar": True,
        "team_extras_type_stack": True,
        "team_extras_bar": True,
        "bowler_discipline_scatter": True,
        "over_heatmap": True,
        "boundary_runs_stack": True,
        "dropped_catches_team_bar": True,
        "bowler_summary_table": True,
        "boundary_dependency_table": True,
        "dropped_catches_table": True,
        "points_table_table": True,
        "top_batting_table": True,
        "top_bowling_table": True,
        "top_batter_scoring_types_chart": True,
        "top_bowler_wicket_types_chart": True,
    }
    if not config_path.exists():
        return default_config

    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    figure_flags = payload.get("figures", {})
    return {
        key: bool(figure_flags.get(key, default_value))
        for key, default_value in default_config.items()
    }


def component_style(visible: bool) -> dict[str, str]:
    return {} if visible else {"display": "none"}


def resolve_bowling_team(row: pd.Series) -> str:
    if row["batting_team"] == row["team_1"]:
        return row["team_2"]
    if row["batting_team"] == row["team_2"]:
        return row["team_1"]
    return "Unknown"


def compute_boundary_runs(row: pd.Series) -> float:
    if row["is_boundary"] != 1:
        return 0.0
    return float(row["batsman_runs"])


def extract_drop_fielder(commentary: str) -> str:
    match = DROP_RE.search(commentary)
    if match is None:
        return ""
    return match.group("fielder").strip()


def resolve_wicket_type(row: pd.Series) -> str:
    dismissal_kind = str(row.get("dismissal_kind", "")).strip().lower()
    commentary = str(row.get("commentary", "")).strip().lower()

    if dismissal_kind == "retired_hurt" or "retired hurt" in commentary:
        return "retired_hurt"
    if dismissal_kind == "run_out" or "run out" in commentary:
        return "run_out"
    if "hit wicket" in commentary or "hit wkt" in commentary:
        return "hit_wicket"
    if "stumped" in commentary:
        return "stumped"
    if "lbw" in commentary:
        return "lbw"
    if "caught out" in commentary or "caught by" in commentary:
        return "caught"
    if "bowled" in commentary:
        return "bowled"
    if dismissal_kind == "out" or "out " in commentary:
        return "out"
    return ""


def extract_scorecard_wicket_type(how_to_out: str) -> str:
    dismissal_text = str(how_to_out).strip().lower()
    if not dismissal_text or dismissal_text == "not out":
        return ""
    if dismissal_text.startswith("run out") or " retired hurt" in dismissal_text:
        return ""
    if dismissal_text.startswith("st ") and " b " in dismissal_text:
        return "stumped"
    if dismissal_text.startswith("lbw ") and " b " in dismissal_text:
        return "lbw"
    if dismissal_text.startswith("hit wkt ") and " b " in dismissal_text:
        return "hit_wicket"
    if dismissal_text.startswith("c ") and " b " in dismissal_text:
        return "caught"
    if dismissal_text.startswith("b "):
        return "bowled"
    return ""


def extract_scorecard_bowler_name(how_to_out: str) -> str:
    dismissal_text = str(how_to_out).strip()
    if not dismissal_text or dismissal_text.lower() == "not out":
        return ""
    if dismissal_text.startswith("b "):
        return dismissal_text[2:].strip()
    if " b " not in dismissal_text:
        return ""
    return dismissal_text.rsplit(" b ", 1)[-1].strip()


def load_scorecard_page_payload(scorecards_dir: Path, prefix: str) -> dict | None:
    json_path = scorecards_dir / f"{prefix}.json"
    if json_path.exists():
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            payload = {}
        scorecard_rows = payload.get("scorecard")
        if isinstance(scorecard_rows, list) and scorecard_rows:
            return payload

    html_path = scorecards_dir / f"{prefix}.html"
    if not html_path.exists():
        return None
    try:
        html_text = html_path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = NEXT_DATA_RE.search(html_text)
    if match is None:
        return None
    try:
        page_payload = json.loads(match.group("payload"))
    except json.JSONDecodeError:
        return None
    page_props = page_payload.get("props", {}).get("pageProps", {})
    scorecard_rows = page_props.get("scorecard", [])
    if not isinstance(scorecard_rows, list) or not scorecard_rows:
        return None
    summary_data = page_props.get("summaryData", {}).get("data", {})
    team_a = str(summary_data.get("team_a", {}).get("name", "")).strip()
    team_b = str(summary_data.get("team_b", {}).get("name", "")).strip()
    match_title = f"{team_a} vs {team_b}" if team_a and team_b else ""
    return {"scorecard": scorecard_rows, "match_title": match_title}


def load_scorecard_rows(scorecards_dir: Path) -> list[dict]:
    rows: list[dict] = []
    for json_path in sorted(scorecards_dir.glob("*.json")):
        if json_path.name in {"past_matches.json", "tournament_points_table.json"}:
            continue
        payload = load_scorecard_page_payload(scorecards_dir, json_path.stem)
        if payload is None:
            continue
        match_title = str(payload.get("match_title", "")).strip()
        for row in payload.get("scorecard", []):
            enriched_row = dict(row)
            if match_title:
                enriched_row["match_title"] = match_title
            rows.append(enriched_row)
    return rows


def load_scorecard_match_lookup(scorecards_dir: Path) -> dict[int, dict[str, str]]:
    past_matches_path = scorecards_dir / "past_matches.json"
    if not past_matches_path.exists():
        return {}
    try:
        matches = json.loads(past_matches_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}

    lookup: dict[int, dict[str, str]] = {}
    for match in matches:
        try:
            match_id = int(match.get("match_id"))
        except (TypeError, ValueError):
            continue
        team_a = str(match.get("team_a", "")).strip()
        team_b = str(match.get("team_b", "")).strip()
        match_date = str(match.get("match_start_time", ""))[:10]
        if not team_a or not team_b:
            continue
        title = f"{team_a} vs {team_b}"
        label = f"{title} ({match_date})" if match_date else title
        key = (
            f"{match_date.replace('-', '_')}_{team_a.lower().replace(' ', '_')}_vs_{team_b.lower().replace(' ', '_')}"
            if match_date
            else title.lower().replace(" ", "_")
        )
        lookup[match_id] = {"match_title": title, "match_label": label, "match_key": key}
    return lookup


def repair_commentary_match_metadata(df: pd.DataFrame, past_matches_path: Path) -> pd.DataFrame:
    if df.empty or not past_matches_path.exists():
        return df
    try:
        matches = json.loads(past_matches_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return df

    candidates_by_date: dict[str, list[dict]] = defaultdict(list)
    for match in matches:
        match_date = str(match.get("match_start_time", ""))[:10]
        team_a = str(match.get("team_a", "")).strip()
        team_b = str(match.get("team_b", "")).strip()
        if not match_date or not team_a or not team_b:
            continue
        candidates_by_date[match_date].append(match)

    repaired = df.copy()
    unknown_mask = repaired["bowling_team"].eq("Unknown")
    if not unknown_mask.any():
        return repaired

    for row_index, row in repaired[unknown_mask].iterrows():
        match_date = str(row.get("match_date", "")).strip()
        batting_team = str(row.get("batting_team", "")).strip()
        if not match_date or not batting_team:
            continue
        candidates = [
            match
            for match in candidates_by_date.get(match_date, [])
            if batting_team in {str(match.get("team_a", "")).strip(), str(match.get("team_b", "")).strip()}
        ]
        if len(candidates) != 1:
            continue
        match = candidates[0]
        team_a = str(match.get("team_a", "")).strip()
        team_b = str(match.get("team_b", "")).strip()
        bowling_team = team_b if batting_team == team_a else team_a
        repaired.at[row_index, "team_1"] = team_a
        repaired.at[row_index, "team_2"] = team_b
        repaired.at[row_index, "bowling_team"] = bowling_team
        repaired.at[row_index, "match_title"] = f"{team_a} vs {team_b}"
        repaired.at[row_index, "match_label"] = f"{team_a} vs {team_b} ({match_date})"
        repaired.at[row_index, "match_key"] = (
            f"{match_date.replace('-', '_')}_{team_a.lower().replace(' ', '_')}_vs_{team_b.lower().replace(' ', '_')}"
        )

    return repaired


def resolve_scorecard_bowling_team(innings_team: str, match_title: str) -> str:
    title = str(match_title).strip()
    if " vs " not in title:
        return "Unknown"
    team_1, team_2 = [part.strip() for part in title.split(" vs ", 1)]
    if innings_team == team_1:
        return team_2
    if innings_team == team_2:
        return team_1
    return "Unknown"


def aggregate_commentary_batting_stats(df: pd.DataFrame) -> pd.DataFrame:
    batting_events = df[df["extra_type"] != "wide"].copy()
    grouped = (
        batting_events.groupby(["batting_team", "batsman"], dropna=False)
        .agg(
            runs=("batsman_runs", "sum"),
            balls=("batsman", "size"),
            ones=("batsman_runs", lambda s: int((s == 1).sum())),
            twos=("batsman_runs", lambda s: int((s == 2).sum())),
            fours=("boundary_type", lambda s: int((s == "FOUR").sum())),
            sixes=("boundary_type", lambda s: int((s == "SIX").sum())),
        )
        .reset_index()
    )
    grouped = grouped[grouped["batsman"].ne("")]
    grouped["ones_runs"] = grouped["ones"]
    grouped["twos_runs"] = grouped["twos"] * 2
    grouped["fours_runs"] = grouped["fours"] * 4
    grouped["sixes_runs"] = grouped["sixes"] * 6
    grouped["strike_rate"] = grouped.apply(
        lambda row: row["runs"] / row["balls"] * 100.0 if row["balls"] > 0 else 0.0,
        axis=1,
    )
    return grouped.sort_values(["runs", "strike_rate"], ascending=[False, False])


def aggregate_scorecard_batting_leaders(scorecards_dir: Path, commentary_df: pd.DataFrame) -> pd.DataFrame:
    scorecard_rows = load_scorecard_rows(scorecards_dir)
    if not scorecard_rows:
        return aggregate_batting_leaders(commentary_df)

    batting_records: list[dict[str, object]] = []
    for innings_row in scorecard_rows:
        batting_team = str(innings_row.get("teamName", "")).strip()
        for batter in innings_row.get("batting", []) or []:
            batsman = str(batter.get("name", "")).replace("  (wk)", "").replace("  (c)", "").strip()
            if not batting_team or not batsman:
                continue
            batting_records.append(
                {
                    "batting_team": batting_team,
                    "batsman": batsman,
                    "runs": int(batter.get("runs", 0) or 0),
                    "balls": int(batter.get("balls", 0) or 0),
                    "fours": int(batter.get("4s", 0) or 0),
                    "sixes": int(batter.get("6s", 0) or 0),
                }
            )
    if not batting_records:
        return aggregate_batting_leaders(commentary_df)

    scorecard_df = pd.DataFrame(batting_records)
    grouped = (
        scorecard_df.groupby(["batting_team", "batsman"], dropna=False)
        .agg(
            runs=("runs", "sum"),
            balls=("balls", "sum"),
            fours=("fours", "sum"),
            sixes=("sixes", "sum"),
        )
        .reset_index()
    )
    grouped["strike_rate"] = grouped.apply(
        lambda row: row["runs"] / row["balls"] * 100.0 if row["balls"] > 0 else 0.0,
        axis=1,
    )
    grouped["ones"] = 0
    grouped["twos"] = 0
    grouped["ones_runs"] = 0
    grouped["twos_runs"] = 0
    grouped["fours_runs"] = grouped["fours"] * 4
    grouped["sixes_runs"] = grouped["sixes"] * 6

    if not commentary_df.empty:
        commentary_grouped = aggregate_commentary_batting_stats(commentary_df)[
            ["batting_team", "batsman", "ones", "twos", "ones_runs", "twos_runs"]
        ]
        grouped = grouped.merge(commentary_grouped, on=["batting_team", "batsman"], how="left", suffixes=("", "_commentary"))
        for column in ["ones", "twos", "ones_runs", "twos_runs"]:
            commentary_column = f"{column}_commentary"
            grouped[column] = grouped[commentary_column].fillna(grouped[column]).astype(int)
            grouped = grouped.drop(columns=[commentary_column])

    grouped.attrs["derived_columns"] = ["runs", "balls", "fours", "sixes", "strike_rate"]
    return grouped.sort_values(["runs", "strike_rate"], ascending=[False, False]).head(5)


def overs_value_to_balls(overs: object, balls: object = 0) -> int:
    overs_text = str(overs).strip()
    if not overs_text or overs_text.lower() == "nan":
        return 0
    parsed = parse_overs_to_balls(overs_text)
    if parsed is not None:
        return parsed
    try:
        return int(overs) * 6 + int(balls)
    except (TypeError, ValueError):
        return 0


def aggregate_scorecard_bowling_leaders(scorecards_dir: Path, commentary_df: pd.DataFrame) -> pd.DataFrame:
    scorecard_rows = load_scorecard_rows(scorecards_dir)
    if not scorecard_rows:
        return aggregate_bowling_leaders(commentary_df)

    bowling_records: list[dict[str, object]] = []
    wicket_type_records: list[dict[str, object]] = []
    for innings_row in scorecard_rows:
        batting_team = str(innings_row.get("teamName", "")).strip()
        for bowling_row in innings_row.get("bowling", []) or []:
            bowler = str(bowling_row.get("name", "")).replace("  (wk)", "").replace("  (c)", "").strip()
            if not bowler:
                continue
            batting_team = str(innings_row.get("teamName", "")).strip()
            bowling_records.append(
                {
                    "bowling_team": resolve_scorecard_bowling_team(
                        batting_team, str(innings_row.get("match_title", ""))
                    ),
                    "innings_team": batting_team,
                    "bowler": bowler,
                    "wickets": int(bowling_row.get("wickets", 0) or 0),
                    "legal_balls": overs_value_to_balls(bowling_row.get("overs", 0), bowling_row.get("balls", 0)),
                    "runs_conceded": int(bowling_row.get("runs", 0) or 0),
                    "extras": int(bowling_row.get("wide", 0) or 0)
                    + int(bowling_row.get("noball", 0) or 0)
                    + int(bowling_row.get("extra_run", 0) or 0)
                    + int(bowling_row.get("bonus_run", 0) or 0),
                }
            )
        for batter in innings_row.get("batting", []) or []:
            wicket_type = extract_scorecard_wicket_type(batter.get("how_to_out", ""))
            bowler = extract_scorecard_bowler_name(batter.get("how_to_out", ""))
            if not wicket_type or not bowler:
                continue
            wicket_type_records.append(
                {
                    "bowler": bowler.replace("  (wk)", "").replace("  (c)", "").strip(),
                    "innings_team": batting_team,
                    "bowling_team": resolve_scorecard_bowling_team(
                        batting_team, str(innings_row.get("match_title", ""))
                    ),
                    "wicket_type": wicket_type,
                }
            )

    if not bowling_records:
        return aggregate_bowling_leaders(commentary_df)

    bowling_df = pd.DataFrame(bowling_records)
    grouped = (
        bowling_df.groupby(["bowling_team", "bowler"], dropna=False)
        .agg(
            wickets=("wickets", "sum"),
            legal_balls=("legal_balls", "sum"),
            runs_conceded=("runs_conceded", "sum"),
            extras=("extras", "sum"),
        )
        .reset_index()
    )

    wicket_type_df = pd.DataFrame(wicket_type_records)
    for wicket_type in ["caught", "bowled", "lbw", "stumped", "hit_wicket"]:
        grouped[wicket_type] = 0
    if not wicket_type_df.empty:
        wicket_type_counts = (
            wicket_type_df.groupby(["bowling_team", "bowler", "wicket_type"], dropna=False)
            .size()
            .unstack(fill_value=0)
            .reset_index()
        )
        grouped = grouped.merge(wicket_type_counts, on=["bowling_team", "bowler"], how="left", suffixes=("", "_scorecard"))
        for wicket_type in ["caught", "bowled", "lbw", "stumped", "hit_wicket"]:
            scorecard_column = f"{wicket_type}_scorecard"
            if scorecard_column in grouped.columns:
                grouped[wicket_type] = grouped[scorecard_column].fillna(grouped[wicket_type]).astype(int)
                grouped = grouped.drop(columns=[scorecard_column])

    grouped = grouped[grouped["bowler"].ne("")]
    grouped["overs_bowled"] = grouped["legal_balls"] / 6.0
    grouped["economy"] = grouped.apply(
        lambda row: row["runs_conceded"] / row["overs_bowled"] if row["overs_bowled"] > 0 else 0.0,
        axis=1,
    )
    grouped["bowling_strike_rate"] = grouped.apply(
        lambda row: row["legal_balls"] / row["wickets"] if row["wickets"] > 0 else None,
        axis=1,
    )
    grouped["bowling_average"] = grouped.apply(
        lambda row: row["runs_conceded"] / row["wickets"] if row["wickets"] > 0 else None,
        axis=1,
    )
    grouped.attrs["derived_columns"] = [
        "wickets",
        "caught",
        "bowled",
        "stumped",
        "hit_wicket",
        "overs_bowled",
        "runs_conceded",
        "extras",
        "economy",
        "bowling_strike_rate",
        "bowling_average",
    ]
    return grouped.sort_values(["wickets", "economy", "runs_conceded"], ascending=[False, True, True]).head(5)


def aggregate_bowler_summary(df: pd.DataFrame) -> pd.DataFrame:
    grouped = (
        df.groupby(["match_key", "match_title", "match_label", "bowling_team", "bowler"], dropna=False)
        .agg(
            delivered_events=("bowler", "size"),
            legal_balls=("is_legal_ball", "sum"),
            extras_runs=("extras", "sum"),
            wide_runs=("wide_runs", "sum"),
            no_ball_runs=("no_ball_runs", "sum"),
            bye_runs=("bye_runs", "sum"),
            leg_bye_runs=("leg_bye_runs", "sum"),
            wide_events=("wide_events", "sum"),
            no_ball_events=("no_ball_events", "sum"),
        )
        .reset_index()
    )
    grouped["overs_bowled"] = grouped["legal_balls"] / 6.0
    grouped["extras_per_over"] = grouped.apply(
        lambda row: row["extras_runs"] / row["overs_bowled"]
        if row["overs_bowled"] > 0
        else 0.0,
        axis=1,
    )
    grouped["extra_event_rate"] = grouped.apply(
        lambda row: (row["wide_events"] + row["no_ball_events"]) / row["delivered_events"]
        if row["delivered_events"] > 0
        else 0.0,
        axis=1,
    )
    grouped["discipline_score"] = (
        grouped["extras_runs"] + 2 * grouped["wide_events"] + 2 * grouped["no_ball_events"]
    )
    return grouped.sort_values(
        ["extras_runs", "extra_event_rate"], ascending=[False, False]
    )


def aggregate_team_summary(df: pd.DataFrame) -> pd.DataFrame:
    grouped = (
        df.groupby(["match_key", "match_title", "match_label", "bowling_team"], dropna=False)
        .agg(
            delivered_events=("bowler", "size"),
            legal_balls=("is_legal_ball", "sum"),
            extras_runs=("extras", "sum"),
            wide_runs=("wide_runs", "sum"),
            no_ball_runs=("no_ball_runs", "sum"),
            bye_runs=("bye_runs", "sum"),
            leg_bye_runs=("leg_bye_runs", "sum"),
            wide_events=("wide_events", "sum"),
            no_ball_events=("no_ball_events", "sum"),
        )
        .reset_index()
    )
    grouped["overs_bowled"] = grouped["legal_balls"] / 6.0
    grouped["extras_per_over"] = grouped.apply(
        lambda row: row["extras_runs"] / row["overs_bowled"]
        if row["overs_bowled"] > 0
        else 0.0,
        axis=1,
    )
    grouped["extra_event_rate"] = grouped.apply(
        lambda row: (row["wide_events"] + row["no_ball_events"]) / row["delivered_events"]
        if row["delivered_events"] > 0
        else 0.0,
        axis=1,
    )
    return grouped.sort_values(["match_label", "extras_runs"], ascending=[True, False])


def aggregate_over_summary(df: pd.DataFrame) -> pd.DataFrame:
    grouped = (
        df.groupby(
            ["match_key", "match_title", "match_label", "bowling_team", "bowler", "over_label"],
            dropna=False,
        )
        .agg(
            delivered_events=("bowler", "size"),
            legal_balls=("is_legal_ball", "sum"),
            extras_runs=("extras", "sum"),
            wide_runs=("wide_runs", "sum"),
            no_ball_runs=("no_ball_runs", "sum"),
            bye_runs=("bye_runs", "sum"),
            leg_bye_runs=("leg_bye_runs", "sum"),
            wide_events=("wide_events", "sum"),
            no_ball_events=("no_ball_events", "sum"),
        )
        .reset_index()
    )
    grouped["over_extras_events"] = grouped["wide_events"] + grouped["no_ball_events"]
    return grouped.sort_values(
        ["match_label", "extras_runs", "over_extras_events"], ascending=[True, False, False]
    )


def aggregate_boundary_dependency(df: pd.DataFrame) -> pd.DataFrame:
    grouped = (
        df.groupby(["match_key", "match_title", "match_label", "batting_team"], dropna=False)
        .agg(
            innings_runs=("total_runs", "sum"),
            boundary_runs=("boundary_runs", "sum"),
            non_boundary_bat_runs=("non_boundary_bat_runs", "sum"),
            extras_runs=("extras", "sum"),
            boundaries=("is_boundary", "sum"),
        )
        .reset_index()
    )
    grouped["boundary_dependency_pct"] = grouped.apply(
        lambda row: row["boundary_runs"] / row["innings_runs"] * 100.0
        if row["innings_runs"] > 0
        else 0.0,
        axis=1,
    )
    return grouped.sort_values(
        ["boundary_dependency_pct", "innings_runs"], ascending=[False, False]
    )


def aggregate_dropped_catches(df: pd.DataFrame) -> pd.DataFrame:
    dropped = df[df["is_dropped_catch"]].copy()
    if dropped.empty:
        return pd.DataFrame(
            columns=["match_key", "match_title", "match_label", "fielding_team", "dropped_catches", "fielders_involved"]
        )

    grouped = (
        dropped.groupby(["match_key", "match_title", "match_label", "bowling_team"], dropna=False)
        .agg(
            dropped_catches=("is_dropped_catch", "sum"),
            fielders_involved=("drop_fielder", lambda s: ", ".join(sorted(set(x for x in s if x)))),
        )
        .reset_index()
        .rename(columns={"bowling_team": "fielding_team"})
    )
    return grouped.sort_values(["dropped_catches", "match_label"], ascending=[False, True])


def aggregate_fielder_drop_summary(df: pd.DataFrame) -> pd.DataFrame:
    dropped = df[df["is_dropped_catch"]].copy()
    if dropped.empty:
        return pd.DataFrame(
            columns=["match_key", "match_title", "match_label", "fielding_team", "drop_fielder", "dropped_catches"]
        )

    grouped = (
        dropped.groupby(
            ["match_key", "match_title", "match_label", "bowling_team", "drop_fielder"],
            dropna=False,
        )
        .size()
        .reset_index(name="dropped_catches")
        .rename(columns={"bowling_team": "fielding_team"})
    )
    return grouped.sort_values(["dropped_catches", "drop_fielder"], ascending=[False, True])


def aggregate_batting_leaders(df: pd.DataFrame) -> pd.DataFrame:
    batting_events = df[df["extra_type"] != "wide"].copy()
    grouped = (
        batting_events.groupby(["batting_team", "batsman"], dropna=False)
        .agg(
            runs=("batsman_runs", "sum"),
            balls=("batsman", "size"),
            ones=("batsman_runs", lambda s: int((s == 1).sum())),
            twos=("batsman_runs", lambda s: int((s == 2).sum())),
            fours=("boundary_type", lambda s: int((s == "FOUR").sum())),
            sixes=("boundary_type", lambda s: int((s == "SIX").sum())),
        )
        .reset_index()
    )
    grouped = grouped[grouped["batsman"].ne("")]
    grouped["ones_runs"] = grouped["ones"]
    grouped["twos_runs"] = grouped["twos"] * 2
    grouped["fours_runs"] = grouped["fours"] * 4
    grouped["sixes_runs"] = grouped["sixes"] * 6
    grouped["strike_rate"] = grouped.apply(
        lambda row: row["runs"] / row["balls"] * 100.0 if row["balls"] > 0 else 0.0,
        axis=1,
    )
    return grouped.sort_values(["runs", "strike_rate"], ascending=[False, False]).head(5)


def aggregate_bowling_leaders(df: pd.DataFrame) -> pd.DataFrame:
    bowling_df = df.copy()
    bowling_df["bowler_wicket"] = bowling_df.apply(
        lambda row: 1
        if row["is_wicket"] == 1 and row["dismissal_kind"] not in {"run_out", "retired_hurt"}
        else 0,
        axis=1,
    )
    bowling_df["runs_conceded"] = bowling_df.apply(
        lambda row: row["batsman_runs"] + row["extras"]
        if row["extra_type"] in {"none", "wide", "no_ball"}
        else row["batsman_runs"],
        axis=1,
    )
    grouped = (
        bowling_df.groupby(["bowling_team", "bowler"], dropna=False)
        .agg(
            wickets=("bowler_wicket", "sum"),
            legal_balls=("is_legal_ball", "sum"),
            runs_conceded=("runs_conceded", "sum"),
            extras=("extras", "sum"),
            caught=("wicket_type", lambda s: int((s == "caught").sum())),
            bowled=("wicket_type", lambda s: int((s == "bowled").sum())),
            lbw=("wicket_type", lambda s: int((s == "lbw").sum())),
            stumped=("wicket_type", lambda s: int((s == "stumped").sum())),
            hit_wicket=("wicket_type", lambda s: int((s == "hit_wicket").sum())),
        )
        .reset_index()
    )
    grouped = grouped[grouped["bowler"].ne("")]
    grouped["overs_bowled"] = grouped["legal_balls"] / 6.0
    grouped["economy"] = grouped.apply(
        lambda row: row["runs_conceded"] / row["overs_bowled"] if row["overs_bowled"] > 0 else 0.0,
        axis=1,
    )
    grouped["bowling_strike_rate"] = grouped.apply(
        lambda row: row["legal_balls"] / row["wickets"] if row["wickets"] > 0 else None,
        axis=1,
    )
    grouped["bowling_average"] = grouped.apply(
        lambda row: row["runs_conceded"] / row["wickets"] if row["wickets"] > 0 else None,
        axis=1,
    )
    return grouped.sort_values(["wickets", "economy", "runs_conceded"], ascending=[False, True, True]).head(5)


def normalize_series(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce").fillna(0.0).astype(float)
    if numeric.empty:
        return numeric
    minimum = float(numeric.min())
    maximum = float(numeric.max())
    if maximum == minimum:
        return numeric.apply(lambda value: 1.0 if value > 0 else 0.0)
    return (numeric - minimum) / (maximum - minimum)


def inverse_normalize_series(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce").fillna(0.0).astype(float)
    if numeric.empty:
        return numeric
    minimum = float(numeric.min())
    maximum = float(numeric.max())
    if maximum == minimum:
        return numeric.apply(lambda value: 1.0 if value >= 0 else 0.0)
    return 1.0 - ((numeric - minimum) / (maximum - minimum))


def build_batting_innings_stats(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(
            columns=[
                "match_key",
                "match_label",
                "batting_team",
                "batsman",
                "runs",
                "balls",
                "boundary_runs",
                "strike_rate",
                "team_runs",
                "team_balls",
                "team_strike_rate",
                "delta_strike_rate",
                "boundary_share",
            ]
        )

    batting_events = df[df["extra_type"] != "wide"].copy()
    innings_df = (
        batting_events.groupby(["match_key", "match_label", "batting_team", "batsman"], dropna=False)
        .agg(
            runs=("batsman_runs", "sum"),
            balls=("batsman", "size"),
            boundary_runs=("boundary_runs", "sum"),
        )
        .reset_index()
    )
    innings_df = innings_df[innings_df["batsman"].ne("")]
    innings_df["strike_rate"] = innings_df.apply(
        lambda row: row["runs"] / row["balls"] * 100.0 if row["balls"] > 0 else 0.0,
        axis=1,
    )

    team_innings_df = (
        batting_events.groupby(["match_key", "match_label", "batting_team"], dropna=False)
        .agg(team_runs=("batsman_runs", "sum"), team_balls=("batsman", "size"))
        .reset_index()
    )
    team_innings_df["team_strike_rate"] = team_innings_df.apply(
        lambda row: row["team_runs"] / row["team_balls"] * 100.0 if row["team_balls"] > 0 else 0.0,
        axis=1,
    )
    innings_df = innings_df.merge(
        team_innings_df,
        on=["match_key", "match_label", "batting_team"],
        how="left",
    )
    innings_df["delta_strike_rate"] = (innings_df["strike_rate"] - innings_df["team_strike_rate"]).clip(lower=0.0)
    innings_df["boundary_share"] = innings_df.apply(
        lambda row: row["boundary_runs"] / row["runs"] if row["runs"] > 0 else 0.0,
        axis=1,
    )
    return innings_df.sort_values(["match_label", "batting_team", "runs"], ascending=[True, True, False])


def build_bowling_innings_stats(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(
            columns=[
                "match_key",
                "match_label",
                "bowling_team",
                "bowler",
                "wickets",
                "legal_balls",
                "runs_conceded",
                "overs_bowled",
                "economy",
            ]
        )

    bowling_df = df.copy()
    bowling_df["bowler_wicket"] = bowling_df.apply(
        lambda row: 1
        if row["is_wicket"] == 1 and row["dismissal_kind"] not in {"run_out", "retired_hurt"}
        else 0,
        axis=1,
    )
    bowling_df["runs_conceded"] = bowling_df.apply(
        lambda row: row["batsman_runs"] + row["extras"]
        if row["extra_type"] in {"none", "wide", "no_ball"}
        else row["batsman_runs"],
        axis=1,
    )
    innings_df = (
        bowling_df.groupby(["match_key", "match_label", "bowling_team", "bowler"], dropna=False)
        .agg(
            wickets=("bowler_wicket", "sum"),
            legal_balls=("is_legal_ball", "sum"),
            runs_conceded=("runs_conceded", "sum"),
        )
        .reset_index()
    )
    innings_df = innings_df[innings_df["bowler"].ne("")]
    innings_df["overs_bowled"] = innings_df["legal_balls"] / 6.0
    innings_df["economy"] = innings_df.apply(
        lambda row: row["runs_conceded"] / row["overs_bowled"] if row["overs_bowled"] > 0 else 0.0,
        axis=1,
    )
    return innings_df.sort_values(["match_label", "bowling_team", "wickets"], ascending=[True, True, False])


def build_scorecard_batting_innings_stats(scorecards_dir: Path) -> pd.DataFrame:
    match_lookup = load_scorecard_match_lookup(scorecards_dir)
    records: list[dict[str, object]] = []

    for json_path in sorted(scorecards_dir.glob("*.json")):
        if json_path.name in {"past_matches.json", "tournament_points_table.json"}:
            continue
        try:
            raw_payload = json.loads(json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        scorecard_payload = load_scorecard_page_payload(scorecards_dir, json_path.stem)
        if scorecard_payload is None:
            continue

        try:
            match_id = int(raw_payload.get("match_id"))
        except (TypeError, ValueError):
            match_id = -1
        match_meta = match_lookup.get(match_id, {})
        match_title = str(match_meta.get("match_title") or raw_payload.get("match_title") or scorecard_payload.get("match_title", "")).strip()
        match_label = str(match_meta.get("match_label") or match_title).strip()
        match_key = str(match_meta.get("match_key") or json_path.stem).strip()

        for innings_row in scorecard_payload.get("scorecard", []) or []:
            batting_team = str(innings_row.get("teamName", "")).strip()
            batting_rows = innings_row.get("batting", []) or []
            if not batting_team or not batting_rows:
                continue

            team_batting_runs = 0
            team_batting_balls = 0
            cleaned_batters: list[dict[str, object]] = []
            for batter in batting_rows:
                batter_name = str(batter.get("name", "")).replace("  (wk)", "").replace("  (c)", "").strip()
                if not batter_name:
                    continue
                runs = int(batter.get("runs", 0) or 0)
                balls = int(batter.get("balls", 0) or 0)
                fours = int(batter.get("4s", 0) or 0)
                sixes = int(batter.get("6s", 0) or 0)
                team_batting_runs += runs
                team_batting_balls += balls
                cleaned_batters.append(
                    {
                        "batsman": batter_name,
                        "runs": runs,
                        "balls": balls,
                        "fours": fours,
                        "sixes": sixes,
                    }
                )

            for batter in cleaned_batters:
                strike_rate = (int(batter["runs"]) / int(batter["balls"]) * 100.0) if int(batter["balls"]) > 0 else 0.0
                records.append(
                    {
                        "match_key": match_key,
                        "match_label": match_label,
                        "match_title": match_title,
                        "batting_team": batting_team,
                        "batsman": batter["batsman"],
                        "runs": batter["runs"],
                        "balls": batter["balls"],
                        "fours": batter["fours"],
                        "sixes": batter["sixes"],
                        "strike_rate": strike_rate,
                        "team_batting_runs": team_batting_runs,
                        "team_batting_balls": team_batting_balls,
                    }
                )
    batting_df = pd.DataFrame(records)
    if batting_df.empty:
        return batting_df

    team_tournament_df = (
        batting_df.groupby("batting_team", dropna=False)
        .agg(
            team_tournament_runs=("runs", "sum"),
            team_tournament_balls=("balls", "sum"),
        )
        .reset_index()
    )
    team_tournament_df["team_tournament_strike_rate"] = team_tournament_df.apply(
        lambda row: row["team_tournament_runs"] / row["team_tournament_balls"] * 100.0
        if row["team_tournament_balls"] > 0
        else 0.0,
        axis=1,
    )
    batting_df = batting_df.merge(team_tournament_df, on="batting_team", how="left")
    batting_df["delta_strike_rate"] = (
        batting_df["strike_rate"] - batting_df["team_tournament_strike_rate"]
    ).clip(lower=0.0)
    return batting_df


def build_scorecard_bowling_innings_stats(scorecards_dir: Path) -> pd.DataFrame:
    match_lookup = load_scorecard_match_lookup(scorecards_dir)
    records: list[dict[str, object]] = []

    for json_path in sorted(scorecards_dir.glob("*.json")):
        if json_path.name in {"past_matches.json", "tournament_points_table.json"}:
            continue
        try:
            raw_payload = json.loads(json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        scorecard_payload = load_scorecard_page_payload(scorecards_dir, json_path.stem)
        if scorecard_payload is None:
            continue

        try:
            match_id = int(raw_payload.get("match_id"))
        except (TypeError, ValueError):
            match_id = -1
        match_meta = match_lookup.get(match_id, {})
        match_title = str(match_meta.get("match_title") or raw_payload.get("match_title") or scorecard_payload.get("match_title", "")).strip()
        match_label = str(match_meta.get("match_label") or match_title).strip()
        match_key = str(match_meta.get("match_key") or json_path.stem).strip()

        for innings_row in scorecard_payload.get("scorecard", []) or []:
            batting_team = str(innings_row.get("teamName", "")).strip()
            bowling_team = resolve_scorecard_bowling_team(batting_team, match_title)
            for bowling_row in innings_row.get("bowling", []) or []:
                bowler = str(bowling_row.get("name", "")).replace("  (wk)", "").replace("  (c)", "").strip()
                if not bowler:
                    continue
                legal_balls = overs_value_to_balls(bowling_row.get("overs", 0), bowling_row.get("balls", 0))
                overs_bowled = legal_balls / 6.0
                runs_conceded = int(bowling_row.get("runs", 0) or 0)
                records.append(
                    {
                        "match_key": match_key,
                        "match_label": match_label,
                        "match_title": match_title,
                        "bowling_team": bowling_team,
                        "bowler": bowler,
                        "wickets": int(bowling_row.get("wickets", 0) or 0),
                        "legal_balls": legal_balls,
                        "overs_bowled": overs_bowled,
                        "runs_conceded": runs_conceded,
                        "economy": (runs_conceded / overs_bowled) if overs_bowled > 0 else 0.0,
                    }
                )

    return pd.DataFrame(records)


def summarize_player_scores(
    innings_df: pd.DataFrame,
    *,
    team_column: str,
    player_column: str,
    score_column: str,
    extra_aggregations: dict[str, tuple[str, str]],
) -> pd.DataFrame:
    if innings_df.empty:
        return pd.DataFrame(columns=[team_column, player_column, "innings_count", "total_score", "average_score"])

    summary_df = (
        innings_df.groupby([team_column, player_column], dropna=False)
        .agg(
            innings_count=(score_column, "size"),
            total_score=(score_column, "sum"),
            average_score=(score_column, "mean"),
            **extra_aggregations,
        )
        .reset_index()
    )
    return summary_df.sort_values(
        ["average_score", "total_score", "innings_count", player_column],
        ascending=[False, False, False, True],
    ).head(5)


def score_batting_option_3_innings(
    innings_df: pd.DataFrame,
    *,
    runs_weight: float,
    strike_rate_weight: float,
) -> pd.DataFrame:
    scored_df = innings_df.copy()
    if "runs_norm" not in scored_df.columns:
        scored_df["runs_norm"] = normalize_series(scored_df["runs"])
    if "delta_strike_rate_norm" not in scored_df.columns:
        scored_df["delta_strike_rate_norm"] = normalize_series(scored_df["delta_strike_rate"])
    scored_df["batting_option_3_score"] = (
        runs_weight * scored_df["runs_norm"]
        + strike_rate_weight * scored_df["delta_strike_rate_norm"]
    )
    return scored_df


def compute_batting_option_tables(
    df: pd.DataFrame,
    *,
    runs_weight_option_2: float,
    strike_rate_weight_option_2: float,
    runs_weight_option_3: float,
    strike_rate_weight_option_3: float,
    boundary_weight_option_3: float,
    minimum_balls_faced_option_3: int = 0,
) -> dict[str, pd.DataFrame]:
    innings_df = build_batting_innings_stats(df)
    if innings_df.empty:
        empty_df = pd.DataFrame(
            columns=["batting_team", "batsman", "innings_count", "total_score", "average_score", "runs", "strike_rate"]
        )
        return {"option_1": empty_df, "option_2": empty_df, "option_3": empty_df}

    innings_df = innings_df.copy()
    innings_df["runs_norm"] = normalize_series(innings_df["runs"])
    innings_df["delta_strike_rate_norm"] = normalize_series(innings_df["delta_strike_rate"])

    innings_df["batting_option_1_score"] = innings_df["runs_norm"]
    innings_df["batting_option_2_score"] = (
        runs_weight_option_2 * innings_df["runs_norm"]
        + strike_rate_weight_option_2 * innings_df["delta_strike_rate_norm"]
    )
    innings_df = score_batting_option_3_innings(
        innings_df,
        runs_weight=runs_weight_option_3,
        strike_rate_weight=strike_rate_weight_option_3,
    )

    extra_aggs = {
        "runs": ("runs", "mean"),
        "strike_rate": ("strike_rate", "mean"),
    }
    return {
        "option_1": summarize_player_scores(
            innings_df,
            team_column="batting_team",
            player_column="batsman",
            score_column="batting_option_1_score",
            extra_aggregations=extra_aggs,
        ),
        "option_2": summarize_player_scores(
            innings_df,
            team_column="batting_team",
            player_column="batsman",
            score_column="batting_option_2_score",
            extra_aggregations=extra_aggs,
        ),
        "option_3": summarize_player_scores(
            innings_df,
            team_column="batting_team",
            player_column="batsman",
            score_column="batting_option_3_score",
            extra_aggregations=extra_aggs,
        ),
    }


def compute_bowling_option_tables(
    df: pd.DataFrame,
    *,
    wickets_weight_option_2: float,
    economy_weight_option_2: float,
) -> dict[str, pd.DataFrame]:
    innings_df = build_bowling_innings_stats(df)
    if innings_df.empty:
        empty_df = pd.DataFrame(
            columns=["bowling_team", "bowler", "innings_count", "total_score", "average_score", "wickets", "economy"]
        )
        return {"option_1": empty_df, "option_2": empty_df}

    innings_df = innings_df.copy()
    innings_df["wickets_norm"] = normalize_series(innings_df["wickets"])
    innings_df["economy_inv_norm"] = inverse_normalize_series(innings_df["economy"])

    innings_df["bowling_option_1_score"] = innings_df["wickets_norm"]
    innings_df["bowling_option_2_score"] = (
        wickets_weight_option_2 * innings_df["wickets_norm"]
        + economy_weight_option_2 * innings_df["economy_inv_norm"]
    )

    extra_aggs = {
        "wickets": ("wickets", "mean"),
        "economy": ("economy", "mean"),
    }
    return {
        "option_1": summarize_player_scores(
            innings_df,
            team_column="bowling_team",
            player_column="bowler",
            score_column="bowling_option_1_score",
            extra_aggregations=extra_aggs,
        ),
        "option_2": summarize_player_scores(
            innings_df,
            team_column="bowling_team",
            player_column="bowler",
            score_column="bowling_option_2_score",
            extra_aggregations=extra_aggs,
        ),
    }


def compute_scorecard_all_rounder_score_table(
    scorecards_dir: Path,
    *,
    match_key: str = "ALL",
    batting_runs_weight: float,
    batting_strike_rate_weight: float,
    batting_minimum_balls_faced: int = 0,
    bowling_wickets_weight: float,
    bowling_economy_weight: float,
    bowling_minimum_balls_bowled: int = 30,
) -> pd.DataFrame:
    batting_df_all = build_scorecard_batting_innings_stats(scorecards_dir)
    bowling_df = build_scorecard_bowling_innings_stats(scorecards_dir)
    batting_df = batting_df_all.copy()

    if match_key != "ALL":
        batting_df = batting_df[batting_df["match_key"] == match_key]
        bowling_df = bowling_df[bowling_df["match_key"] == match_key]

    batting_summary = pd.DataFrame(
        columns=[
            "team",
            "player",
            "batting_runs_component",
            "batting_delta_sr_component",
            "batting_total_balls_gate",
            "batting_option_2",
        ]
    )
    bowling_summary = pd.DataFrame(
        columns=[
            "team",
            "player",
            "bowling_wickets_component",
            "bowling_total_balls_gate",
            "bowling_economy_component",
            "bowling_option_2",
        ]
    )

    if not batting_df.empty:
        batting_df = batting_df.copy()
        tournament_scope_runs = pd.to_numeric(batting_df["runs"], errors="coerce").fillna(0).sum()
        tournament_scope_balls = pd.to_numeric(batting_df["balls"], errors="coerce").fillna(0).sum()
        tournament_scope_strike_rate = (
            tournament_scope_runs / tournament_scope_balls * 100.0
            if tournament_scope_balls > 0
            else 0.0
        )
        batting_summary = (
            batting_df.groupby(["batting_team", "batsman"], dropna=False)
            .agg(
                total_runs=("runs", "sum"),
                total_balls=("balls", "sum"),
            )
            .reset_index()
        )
        batting_summary["player_strike_rate"] = batting_summary.apply(
            lambda row: row["total_runs"] / row["total_balls"] * 100.0
            if row["total_balls"] > 0
            else 0.0,
            axis=1,
        )
        batting_summary["total_delta_strike_rate"] = (
            batting_summary["player_strike_rate"] - tournament_scope_strike_rate
        ).clip(lower=0.0)
        batting_summary["runs_norm"] = normalize_series(batting_summary["total_runs"])
        batting_summary["delta_strike_rate_norm"] = normalize_series(
            batting_summary["total_delta_strike_rate"]
        )
        batting_summary["batting_runs_component"] = batting_runs_weight * batting_summary["runs_norm"]
        batting_summary["batting_delta_sr_component_raw"] = (
            batting_strike_rate_weight * batting_summary["delta_strike_rate_norm"]
        )
        batting_summary["batting_total_balls_gate"] = (
            pd.to_numeric(batting_summary["total_balls"], errors="coerce").fillna(0).astype(int)
            >= int(batting_minimum_balls_faced)
        ).astype(float)
        batting_summary["batting_delta_sr_component"] = (
            batting_summary["batting_total_balls_gate"] * batting_summary["batting_delta_sr_component_raw"]
        )
        batting_summary["batting_option_2"] = (
            batting_summary["batting_runs_component"]
            + batting_summary["batting_delta_sr_component"]
        )
        batting_summary = batting_summary.rename(columns={"batting_team": "team", "batsman": "player"})
        batting_summary = batting_summary.drop(
            columns=[
                "total_runs",
                "total_balls",
                "player_strike_rate",
                "total_delta_strike_rate",
                "runs_norm",
                "delta_strike_rate_norm",
                "batting_delta_sr_component_raw",
            ]
        )

    if not bowling_df.empty:
        bowling_df = bowling_df.copy()
        bowling_summary = (
            bowling_df.groupby(["bowling_team", "bowler"], dropna=False)
            .agg(
                total_wickets=("wickets", "sum"),
                total_legal_balls=("legal_balls", "sum"),
                total_runs_conceded=("runs_conceded", "sum"),
            )
            .reset_index()
        )
        bowling_summary["total_overs_bowled"] = bowling_summary["total_legal_balls"] / 6.0
        bowling_summary["total_economy"] = bowling_summary.apply(
            lambda row: row["total_runs_conceded"] / row["total_overs_bowled"]
            if row["total_overs_bowled"] > 0
            else 0.0,
            axis=1,
        )
        bowling_summary["wickets_norm"] = normalize_series(bowling_summary["total_wickets"])
        bowling_summary["economy_inv_norm"] = inverse_normalize_series(
            bowling_summary["total_economy"]
        )
        bowling_summary["bowling_wickets_component"] = (
            bowling_wickets_weight * bowling_summary["wickets_norm"]
        )
        bowling_summary["bowling_total_balls_gate"] = (
            pd.to_numeric(bowling_summary["total_legal_balls"], errors="coerce").fillna(0).astype(int)
            >= int(bowling_minimum_balls_bowled)
        ).astype(float)
        bowling_summary["bowling_economy_component"] = (
            bowling_summary["bowling_total_balls_gate"]
            * bowling_economy_weight
            * bowling_summary["economy_inv_norm"]
        )
        bowling_summary["bowling_option_2"] = (
            bowling_summary["bowling_wickets_component"] + bowling_summary["bowling_economy_component"]
        )
        bowling_summary = bowling_summary.rename(columns={"bowling_team": "team", "bowler": "player"})
        bowling_summary = bowling_summary.drop(
            columns=[
                "total_wickets",
                "total_legal_balls",
                "total_runs_conceded",
                "total_overs_bowled",
                "total_economy",
                "wickets_norm",
                "economy_inv_norm",
            ]
        )

    merged_df = batting_summary.merge(bowling_summary, on=["team", "player"], how="outer")
    if merged_df.empty:
        return pd.DataFrame(
            columns=[
                "team",
                "player",
                "batting_runs_component",
                "batting_delta_sr_component",
                "batting_total_balls_gate",
                "batting_option_2",
                "bowling_wickets_component",
                "bowling_total_balls_gate",
                "bowling_economy_component",
                "bowling_option_2",
                "average_score",
            ]
        )

    for column in [
        "batting_runs_component",
        "batting_delta_sr_component",
        "batting_total_balls_gate",
        "batting_option_2",
        "bowling_wickets_component",
        "bowling_total_balls_gate",
        "bowling_economy_component",
        "bowling_option_2",
    ]:
        merged_df[column] = pd.to_numeric(merged_df[column], errors="coerce").fillna(0.0)
    merged_df["average_score"] = (merged_df["batting_option_2"] + merged_df["bowling_option_2"]) / 2.0
    ordered_columns = [
        "team",
        "player",
        "batting_runs_component",
        "batting_delta_sr_component",
        "batting_total_balls_gate",
        "batting_option_2",
        "bowling_wickets_component",
        "bowling_total_balls_gate",
        "bowling_economy_component",
        "bowling_option_2",
        "average_score",
    ]
    return (
        merged_df[ordered_columns]
        .sort_values(["average_score", "batting_option_2", "bowling_option_2", "player"], ascending=[False, False, False, True])
        .head(5)
    )


def build_option_score_table(
    score_df: pd.DataFrame,
    *,
    table_id: str,
    team_label: str,
    player_label: str,
    player_column: str,
    team_column: str,
    metric_label: str,
    metric_column: str,
) -> dash_table.DataTable:
    display_df = pd.DataFrame(
        {
            team_column: score_df.get(team_column, pd.Series(dtype=str)),
            player_column: score_df.get(player_column, pd.Series(dtype=str)),
            "innings_count": score_df.get("innings_count", pd.Series(dtype=int)),
            "total_score": pd.to_numeric(
                score_df.get("total_score", pd.Series(dtype=float)), errors="coerce"
            ).round(3),
            "average_score": pd.to_numeric(
                score_df.get("average_score", pd.Series(dtype=float)), errors="coerce"
            ).round(3),
            metric_column: pd.to_numeric(
                score_df.get(metric_column, pd.Series(dtype=float)), errors="coerce"
            ).round(2),
        }
    )
    return dash_table.DataTable(
        id=table_id,
        columns=[
            {"name": team_label, "id": team_column},
            {"name": player_label, "id": player_column},
            {"name": "Innings", "id": "innings_count"},
            {"name": "Total Score", "id": "total_score"},
            {"name": "Average Score", "id": "average_score"},
            {"name": metric_label, "id": metric_column},
        ],
        data=display_df.to_dict("records"),
        style_table={"overflowX": "auto"},
        style_cell={"padding": "8px", "textAlign": "center", "minWidth": "84px"},
        style_header={"fontWeight": "bold", "textAlign": "center"},
        style_data={"whiteSpace": "normal", "height": "auto"},
    )


def build_top_bowler_wicket_types_figure(top_bowling_df: pd.DataFrame) -> go.Figure:
    wicket_type_columns = ["caught", "bowled", "stumped", "hit_wicket"]
    label_map = {
        "caught": "Caught",
        "bowled": "Bowled",
        "stumped": "Stumped",
        "hit_wicket": "Hit wicket",
    }
    if top_bowling_df.empty:
        return empty_figure("Wicket Types for Top 5 Bowlers")

    wicket_type_df = top_bowling_df[["bowler", *wicket_type_columns]].copy()
    wicket_type_df["tracked_wickets"] = wicket_type_df[wicket_type_columns].sum(axis=1)
    wicket_type_df = wicket_type_df[wicket_type_df["tracked_wickets"] > 0]
    if wicket_type_df.empty:
        return empty_figure("Wicket Types for Top 5 Bowlers")

    wicket_type_df = wicket_type_df.melt(
        id_vars=["bowler", "tracked_wickets"],
        value_vars=wicket_type_columns,
        var_name="wicket_type",
        value_name="count",
    )
    wicket_type_df = wicket_type_df[wicket_type_df["count"] > 0].copy()
    if wicket_type_df.empty:
        return empty_figure("Wicket Types for Top 5 Bowlers")

    wicket_type_df["wicket_type_label"] = wicket_type_df["wicket_type"].map(label_map)
    wicket_type_df["percentage"] = wicket_type_df.apply(
        lambda row: row["count"] / row["tracked_wickets"] * 100.0 if row["tracked_wickets"] > 0 else 0.0,
        axis=1,
    )
    figure = px.bar(
        wicket_type_df,
        x="bowler",
        y="percentage",
        color="wicket_type_label",
        barmode="stack",
        title="Wicket-Type Contribution for Top 5 Bowlers",
        category_orders={"bowler": top_bowling_df["bowler"].tolist()},
        custom_data=["count"],
    )
    figure.update_layout(
        xaxis_title="Bowler",
        yaxis_title="Contribution (%)",
        legend_title_text="Wicket type",
    )
    figure.update_traces(
        hovertemplate="%{x}<br>%{fullData.name}: %{y:.1f}%<br>Wickets: %{customdata[0]}<extra></extra>"
    )
    return figure


def build_top_batting_table_component(top_batting_df: pd.DataFrame) -> dash_table.DataTable:
    display_df = pd.DataFrame(
        {
            "batting_team": top_batting_df.get("batting_team", pd.Series(dtype=str)),
            "batter": top_batting_df.get("batsman", pd.Series(dtype=str)),
            "runs": top_batting_df.get("runs", pd.Series(dtype=int)),
            "balls": top_batting_df.get("balls", pd.Series(dtype=int)),
            "ones": top_batting_df.get("ones", pd.Series(dtype=int)),
            "twos": top_batting_df.get("twos", pd.Series(dtype=int)),
            "fours": top_batting_df.get("fours", pd.Series(dtype=int)),
            "sixes": top_batting_df.get("sixes", pd.Series(dtype=int)),
            "strike_rate": top_batting_df.get("strike_rate", pd.Series(dtype=float)).round(2),
        }
    )
    derived_columns = set(top_batting_df.attrs.get("derived_columns", []))
    return dash_table.DataTable(
        id="top-batting-table",
        columns=[
            {"name": ["Identity", "Team"], "id": "batting_team"},
            {"name": ["Identity", "Batter"], "id": "batter"},
            {"name": ["Output", "Runs*" if "runs" in derived_columns else "Runs"], "id": "runs"},
            {"name": ["Output", "Balls*" if "balls" in derived_columns else "Balls"], "id": "balls"},
            {"name": ["Scoring Shots", "1s"], "id": "ones"},
            {"name": ["Scoring Shots", "2s"], "id": "twos"},
            {"name": ["Scoring Shots", "4s*" if "fours" in derived_columns else "4s"], "id": "fours"},
            {"name": ["Scoring Shots", "6s*" if "sixes" in derived_columns else "6s"], "id": "sixes"},
            {"name": ["Rates", "SR*" if "strike_rate" in derived_columns else "SR"], "id": "strike_rate"},
        ],
        data=display_df.to_dict("records"),
        merge_duplicate_headers=True,
        style_table={"overflowX": "auto"},
        style_cell={"padding": "8px", "textAlign": "center", "minWidth": "84px"},
        style_header={"fontWeight": "bold", "textAlign": "center"},
        style_data={"whiteSpace": "normal", "height": "auto"},
    )


def build_top_batting_footnote(top_batting_df: pd.DataFrame) -> html.P:
    derived_columns = top_batting_df.attrs.get("derived_columns", [])
    if not derived_columns:
        return html.P("")
    return html.P(
        "* Accurate - Derived from scorecards. Metrics without * are very close to reference but need not be exact",
        style={"fontSize": "12px", "marginTop": "8px", "color": "#555"},
    )


def build_points_table_component(points_df: pd.DataFrame) -> dash_table.DataTable:
    display_columns = [
        "Team Name",
        "Matches",
        "Won",
        "Lost",
        "Points",
        "Net RR",
        "For",
        "Against",
        "Last 5",
    ]
    available_columns = [column for column in display_columns if column in points_df.columns]
    display_df = points_df[available_columns].copy() if available_columns else pd.DataFrame()
    if "Net RR" in display_df.columns:
        display_df["Net RR"] = display_df["Net RR"].round(3)
    derived_columns = set(points_df.attrs.get("derived_points_columns", []))

    return dash_table.DataTable(
        id="points-table-table",
        columns=[
            {"name": f"{column}*" if column in derived_columns else column, "id": column}
            for column in display_df.columns
        ],
        data=display_df.to_dict("records"),
        style_table={"overflowX": "auto"},
        style_cell={"padding": "8px", "textAlign": "center", "minWidth": "84px"},
        style_header={"fontWeight": "bold", "textAlign": "center"},
        style_data={"whiteSpace": "normal", "height": "auto"},
    )


def build_points_table_footnote(points_df: pd.DataFrame) -> html.P:
    derived_columns = points_df.attrs.get("derived_points_columns", [])
    if not derived_columns:
        return html.P("")
    return html.P(
        "* Accurate - Derived from scorecards. Metrics without * are very close to reference but need not be exact",
        style={"fontSize": "12px", "marginTop": "8px", "color": "#555"},
    )


def build_top_batter_scoring_types_figure(top_batting_df: pd.DataFrame) -> go.Figure:
    scoring_columns = ["ones_runs", "twos_runs", "fours_runs", "sixes_runs"]
    label_map = {
        "ones_runs": "1s",
        "twos_runs": "2s",
        "fours_runs": "4s",
        "sixes_runs": "6s",
    }
    if top_batting_df.empty:
        return empty_figure("Scoring-Type Run Contribution for Top 5 Batters")

    contribution_df = top_batting_df[["batsman", *scoring_columns]].copy()
    contribution_df["tracked_runs"] = contribution_df[scoring_columns].sum(axis=1)
    contribution_df = contribution_df[contribution_df["tracked_runs"] > 0]
    if contribution_df.empty:
        return empty_figure("Scoring-Type Run Contribution for Top 5 Batters")

    contribution_df = contribution_df.melt(
        id_vars=["batsman", "tracked_runs"],
        value_vars=scoring_columns,
        var_name="scoring_type",
        value_name="runs",
    )
    contribution_df = contribution_df[contribution_df["runs"] > 0].copy()
    contribution_df["percentage"] = contribution_df.apply(
        lambda row: row["runs"] / row["tracked_runs"] * 100.0 if row["tracked_runs"] > 0 else 0.0,
        axis=1,
    )
    contribution_df["scoring_type_label"] = contribution_df["scoring_type"].map(label_map)
    figure = px.bar(
        contribution_df,
        x="batsman",
        y="percentage",
        color="scoring_type_label",
        barmode="stack",
        title="Run Contribution of 1s, 2s, 4s, and 6s for Top 5 Batters",
        category_orders={"batsman": top_batting_df["batsman"].tolist()},
        custom_data=["runs"],
    )
    figure.update_layout(
        xaxis_title="Batter",
        yaxis_title="Contribution (%)",
        legend_title_text="Scoring type",
    )
    figure.update_traces(
        hovertemplate="%{x}<br>%{fullData.name}: %{y:.1f}%<br>Runs: %{customdata[0]}<extra></extra>"
    )
    return figure


def empty_figure(title: str) -> go.Figure:
    figure = go.Figure()
    figure.update_layout(title=title)
    return figure


def table_figure(
    header_values: list[str], cell_values: list[pd.Series], title: str, *, max_rows: int | None = None
) -> go.Figure:
    row_count = max((len(values) for values in cell_values), default=0)
    if max_rows is not None:
        row_count = min(row_count, max_rows)
    table_height = 72 + 26 * row_count
    figure_height = max(140, min(320, table_height + 60))
    figure = go.Figure(
        data=[
            go.Table(
                header={"values": header_values, "height": 28},
                cells={"values": cell_values, "height": 26},
            )
        ]
    )
    figure.update_layout(
        title=title,
        height=figure_height,
        margin={"t": 40, "b": 10, "l": 10, "r": 10},
    )
    return figure


def clean_facet_annotations(figure: go.Figure) -> go.Figure:
    figure.for_each_annotation(
        lambda annotation: annotation.update(
            text=annotation.text.replace("match_label=", "")
        )
    )
    return figure


def build_top_bowling_table_component(top_bowling_df: pd.DataFrame) -> dash_table.DataTable:
    display_df = pd.DataFrame(
        {
            "bowling_team": top_bowling_df.get("bowling_team", pd.Series(dtype=str)),
            "bowler": top_bowling_df.get("bowler", pd.Series(dtype=str)),
            "wickets": top_bowling_df.get("wickets", pd.Series(dtype=int)),
            "caught": top_bowling_df.get("caught", pd.Series(dtype=int)),
            "bowled": top_bowling_df.get("bowled", pd.Series(dtype=int)),
            "stumped": top_bowling_df.get("stumped", pd.Series(dtype=int)),
            "hit_wicket": top_bowling_df.get("hit_wicket", pd.Series(dtype=int)),
            "overs_bowled": top_bowling_df.get("overs_bowled", pd.Series(dtype=float)).round(2),
            "runs_conceded": top_bowling_df.get("runs_conceded", pd.Series(dtype=int)),
            "extras": top_bowling_df.get("extras", pd.Series(dtype=int)),
            "economy": top_bowling_df.get("economy", pd.Series(dtype=float)).round(2),
            "bowling_strike_rate": top_bowling_df.get("bowling_strike_rate", pd.Series(dtype=float)).round(2),
            "bowling_average": top_bowling_df.get("bowling_average", pd.Series(dtype=float)).round(2),
        }
    )
    derived_columns = set(top_bowling_df.attrs.get("derived_columns", []))
    return dash_table.DataTable(
        id="top-bowling-table",
        columns=[
            {"name": ["Identity", "Team"], "id": "bowling_team"},
            {"name": ["Identity", "Bowler"], "id": "bowler"},
            {"name": ["Wickets", "Total*" if "wickets" in derived_columns else "Total"], "id": "wickets"},
            {"name": ["Wicket Types", "Caught*" if "caught" in derived_columns else "Caught"], "id": "caught"},
            {"name": ["Wicket Types", "Bowled*" if "bowled" in derived_columns else "Bowled"], "id": "bowled"},
            {"name": ["Wicket Types", "Stumped*" if "stumped" in derived_columns else "Stumped"], "id": "stumped"},
            {"name": ["Wicket Types", "Hit wicket*" if "hit_wicket" in derived_columns else "Hit wicket"], "id": "hit_wicket"},
            {"name": ["Workload", "Overs*" if "overs_bowled" in derived_columns else "Overs"], "id": "overs_bowled"},
            {"name": ["Runs", "Conceded*" if "runs_conceded" in derived_columns else "Conceded"], "id": "runs_conceded"},
            {"name": ["Runs", "Extras*" if "extras" in derived_columns else "Extras"], "id": "extras"},
            {"name": ["Rates", "Economy*" if "economy" in derived_columns else "Economy"], "id": "economy"},
            {"name": ["Rates", "SR*" if "bowling_strike_rate" in derived_columns else "SR"], "id": "bowling_strike_rate"},
            {"name": ["Rates", "Avg*" if "bowling_average" in derived_columns else "Avg"], "id": "bowling_average"},
        ],
        data=display_df.to_dict("records"),
        merge_duplicate_headers=True,
        style_table={"overflowX": "auto"},
        style_cell={"padding": "8px", "textAlign": "center", "minWidth": "84px"},
        style_header={"fontWeight": "bold", "textAlign": "center"},
        style_data={"whiteSpace": "normal", "height": "auto"},
    )


def build_top_bowling_footnote(top_bowling_df: pd.DataFrame) -> html.P:
    derived_columns = top_bowling_df.attrs.get("derived_columns", [])
    if not derived_columns:
        return html.P("")
    return html.P(
        "* Accurate - Derived from scorecards. Metrics without * are very close to reference but need not be exact",
        style={"fontSize": "12px", "marginTop": "8px", "color": "#555"},
    )


def ordered_match_labels(df: pd.DataFrame) -> list[str]:
    if df.empty:
        return []
    ordered = (
        df[["match_date", "match_label"]]
        .dropna()
        .drop_duplicates()
        .sort_values(["match_date", "match_label"])
    )
    return ordered["match_label"].tolist()


def ordered_over_labels(df: pd.DataFrame) -> list[str]:
    if df.empty:
        return []
    ordered = (
        df[["over_number", "over_label"]]
        .dropna()
        .drop_duplicates()
        .sort_values(["over_number", "over_label"])
    )
    return ordered["over_label"].tolist()


def ordered_team_labels(df: pd.DataFrame) -> list[str]:
    if df.empty:
        return []
    teams = pd.concat(
        [
            df.get("team_1", pd.Series(dtype=str)),
            df.get("team_2", pd.Series(dtype=str)),
            df.get("batting_team", pd.Series(dtype=str)),
            df.get("bowling_team", pd.Series(dtype=str)),
        ],
        ignore_index=True,
    )
    return sorted(team for team in teams.dropna().astype(str).unique().tolist() if team and team != "Unknown")


def build_points_table_components(
    points_df: pd.DataFrame, commentary_df: pd.DataFrame, scorecards_dir: Path | None = None
) -> tuple[dash_table.DataTable, html.P, dash_table.DataTable, html.P, dash_table.DataTable, html.P, go.Figure, go.Figure]:
    if points_df.empty:
        empty_points_table = build_points_table_component(pd.DataFrame())
        empty_points_footnote = build_points_table_footnote(pd.DataFrame())
        empty_batting_table = build_top_batting_table_component(pd.DataFrame())
        empty_batting_footnote = build_top_batting_footnote(pd.DataFrame())
        empty_table = build_top_bowling_table_component(pd.DataFrame())
        empty_bowling_footnote = build_top_bowling_footnote(pd.DataFrame())
        empty_figure_component = empty_figure("Tournament Points Table")
        return (
            empty_points_table,
            empty_points_footnote,
            empty_batting_table,
            empty_batting_footnote,
            empty_table,
            empty_bowling_footnote,
            empty_figure_component,
            empty_figure_component,
        )

    points_table_component = build_points_table_component(points_df)
    points_table_footnote = build_points_table_footnote(points_df)

    if commentary_df.empty and scorecards_dir is None:
        top_batting_df = pd.DataFrame(
            columns=[
                "batting_team",
                "batsman",
                "runs",
                "balls",
                "ones",
                "twos",
                "fours",
                "sixes",
                "ones_runs",
                "twos_runs",
                "fours_runs",
                "sixes_runs",
                "strike_rate",
            ]
        )
        top_bowling_df = pd.DataFrame(
            columns=[
                "bowling_team",
                "bowler",
                "wickets",
                "overs_bowled",
                "runs_conceded",
                "extras",
                "economy",
                "bowling_strike_rate",
                "bowling_average",
                "caught",
                "bowled",
                "stumped",
                "hit_wicket",
            ]
        )
    else:
        if scorecards_dir is not None:
            top_batting_df = aggregate_scorecard_batting_leaders(scorecards_dir, commentary_df)
            top_bowling_df = aggregate_scorecard_bowling_leaders(scorecards_dir, commentary_df)
        else:
            top_batting_df = aggregate_batting_leaders(commentary_df)
            top_bowling_df = aggregate_bowling_leaders(commentary_df)

    batting_table_component = build_top_batting_table_component(top_batting_df)
    batting_table_footnote = build_top_batting_footnote(top_batting_df)
    top_batter_scoring_types_fig = build_top_batter_scoring_types_figure(top_batting_df)
    bowling_table_component = build_top_bowling_table_component(top_bowling_df)
    bowling_table_footnote = build_top_bowling_footnote(top_bowling_df)
    top_bowler_wicket_types_fig = build_top_bowler_wicket_types_figure(top_bowling_df)

    return (
        points_table_component,
        points_table_footnote,
        batting_table_component,
        batting_table_footnote,
        bowling_table_component,
        bowling_table_footnote,
        top_batter_scoring_types_fig,
        top_bowler_wicket_types_fig,
    )


def build_app(
    df: pd.DataFrame, points_df: pd.DataFrame, figure_config: dict[str, bool], scorecards_dir: Path | None = None
) -> dash.Dash:
    app = dash.Dash(__name__)
    default_batting_option_2 = {"runs": 0.75, "delta_sr": 0.25}
    default_bowling_option_2 = {"wickets": 0.7, "economy": 0.3}
    default_batting_option_2_minimum_balls = 20
    default_bowling_option_2_minimum_balls = 30
    match_options = [{"label": "All matches", "value": "ALL"}]
    if not df.empty:
        match_options.extend(
            {"label": match_label, "value": match_key}
            for match_key, match_label in (
                df[["match_key", "match_label"]]
                .dropna()
                .drop_duplicates()
                .sort_values(["match_label"])
                .itertuples(index=False, name=None)
            )
        )

    points_table_component, points_table_footnote, top_batting_table, top_batting_footnote, top_bowling_table, top_bowling_footnote, top_batter_scoring_types_fig, top_bowler_wicket_types_fig = (
        build_points_table_components(points_df, df, scorecards_dir)
    )
    all_rounder_scores = (
        compute_scorecard_all_rounder_score_table(
            scorecards_dir,
            match_key="ALL",
            batting_runs_weight=default_batting_option_2["runs"],
            batting_strike_rate_weight=default_batting_option_2["delta_sr"],
            batting_minimum_balls_faced=default_batting_option_2_minimum_balls,
            bowling_wickets_weight=default_bowling_option_2["wickets"],
            bowling_economy_weight=default_bowling_option_2["economy"],
            bowling_minimum_balls_bowled=default_bowling_option_2_minimum_balls,
        )
        if scorecards_dir is not None
        else pd.DataFrame(
            columns=[
                "team",
                "player",
                "batting_runs_component",
                "batting_delta_sr_component",
                "batting_total_balls_gate",
                "batting_option_2",
                "bowling_wickets_component",
                "bowling_total_balls_gate",
                "bowling_economy_component",
                "bowling_option_2",
                "average_score",
            ]
        )
    )
    app.layout = html.Div(
        [
            html.H1("Ball-by-Ball Analytics Dashboard"),
            html.Div(
                [
                    html.P(
                        "Extras discipline, boundary dependency, dropped catches, and tournament standings.",
                        style={"margin": "0"},
                    ),
                    html.Div(
                        [
                            html.Span(f"Version: {APP_VERSION}", style={"marginRight": "16px"}),
                            html.Span(f"Last updated: {LAST_UPDATED}", style={"marginRight": "16px"}),
                            html.A("Version log", href=VERSION_LOG_HREF, target="_blank"),
                        ],
                        style={"marginLeft": "auto", "textAlign": "right"},
                    ),
                ],
                style={
                    "display": "flex",
                    "alignItems": "baseline",
                    "gap": "12px",
                    "marginBottom": "16px",
                },
            ),
            html.Div(
                [
                    html.Label("Match"),
                    dcc.Dropdown(
                        id="match-filter",
                        options=match_options,
                        value="ALL",
                        clearable=False,
                    ),
                ],
                style={"maxWidth": "480px", "marginBottom": "16px"},
            ),
            dcc.Tabs(
                value="mvp-tab",
                children=[
                    dcc.Tab(
                        value="discipline-tab",
                        label="Extras & Fielding Discipline",
                        children=[
                            html.Div(
                                [
                                    html.Div(
                                        dcc.Graph(id="team-extras-total-stack"),
                                        style=component_style(figure_config["team_extras_total_stack"]),
                                    ),
                                    html.Div(
                                        dcc.Graph(id="team-extras-type-stack"),
                                        style=component_style(figure_config["team_extras_type_stack"]),
                                    ),
                                    html.Div(
                                        dcc.Graph(id="team-keeper-byes-efficiency-bar"),
                                        style=component_style(
                                            figure_config["team_keeper_byes_efficiency_bar"]
                                        ),
                                    ),
                                    html.Div(
                                        dcc.Graph(id="team-extras-bar"),
                                        style=component_style(figure_config["team_extras_bar"]),
                                    ),
                                    html.Div(
                                        dcc.Graph(id="bowler-discipline-scatter"),
                                        style=component_style(figure_config["bowler_discipline_scatter"]),
                                    ),
                                    html.Div(
                                        dcc.Graph(id="over-heatmap"),
                                        style=component_style(figure_config["over_heatmap"]),
                                    ),
                                    html.Div(
                                        dcc.Graph(id="boundary-runs-stack"),
                                        style=component_style(figure_config["boundary_runs_stack"]),
                                    ),
                                    html.Div(
                                        dcc.Graph(id="dropped-catches-team-bar"),
                                        style=component_style(figure_config["dropped_catches_team_bar"]),
                                    ),
                                ]
                            ),
                            html.Div(
                                [
                                    html.H2("Bowler Discipline Table"),
                                    dcc.Graph(id="bowler-summary-table"),
                                ],
                                style=component_style(figure_config["bowler_summary_table"]),
                            ),
                            html.Div(
                                [
                                    html.H2("Boundary Dependency Table"),
                                    dcc.Graph(id="boundary-dependency-table"),
                                ],
                                style=component_style(figure_config["boundary_dependency_table"]),
                            ),
                            html.Div(
                                [
                                    html.H2("Dropped Catches Table"),
                                    dcc.Graph(id="dropped-catches-table"),
                                ],
                                style=component_style(figure_config["dropped_catches_table"]),
                            ),
                        ],
                    ),
                    dcc.Tab(
                        value="standings-tab",
                        label="Standings & Leaders",
                        children=[
                            html.Div(
                                [
                                    html.H2("Tournament Points Table"),
                                    points_table_component,
                                    points_table_footnote,
                                ],
                                style=component_style(figure_config["points_table_table"]),
                            ),
                            html.Div(
                                [
                                    html.H2("Top 5 Batting Performances"),
                                    top_batting_table,
                                    top_batting_footnote,
                                ],
                                style=component_style(figure_config["top_batting_table"]),
                            ),
                            html.Div(
                                [
                                    html.H2("Top 5 Bowling Performances"),
                                    top_bowling_table,
                                    top_bowling_footnote,
                                ],
                                style=component_style(figure_config["top_bowling_table"]),
                            ),
                            html.Div(
                                dcc.Graph(
                                    figure=top_batter_scoring_types_fig,
                                    id="top-batter-scoring-types-chart",
                                ),
                                style=component_style(figure_config["top_batter_scoring_types_chart"]),
                            ),
                            html.Div(
                                dcc.Graph(
                                    figure=top_bowler_wicket_types_fig,
                                    id="top-bowler-wicket-types-chart",
                                ),
                                style=component_style(figure_config["top_bowler_wicket_types_chart"]),
                            ),
                        ],
                    ),
                    dcc.Tab(
                        value="mvp-tab",
                        label="Most Valued Player",
                        children=[
                            html.Div(
                                [
                                    html.H2("Equations for value(computed for whole tournament)"),
                                    html.P(
                                        "Viewer-adjustable coefficients update the value terms and rankings below.",
                                        style={"marginTop": "0"},
                                    ),
                                    dcc.Markdown(
                                        (
                                            "$$\\operatorname{norm}(x; x_{\\min}, x_{\\max}) = \\frac{x - x_{\\min}}{x_{\\max} - x_{\\min}}$$\n\n"
                                            "Batting value:\n"
                                            "$$V_{bat} = w_r R^* + \\mathbf{1}[B_{tot} \\ge B_{\\min}] w_{sr} \\Delta SR^*$$\n\n"
                                            "$$R^* = \\operatorname{norm}(R_{tot}; R_{tot,\\min}, R_{tot,\\max})$$\n\n"
                                            "$$\\Delta SR^* = \\operatorname{norm}(\\max(0, SR_{tot} - TourSR); \\Delta SR_{\\min}, \\Delta SR_{\\max})$$\n\n"
                                            "Bowling value:\n"
                                            "$$V_{bowl} = w_w W^* + \\mathbf{1}[LB_{tot} \\ge LB_{\\min}] w_e ECO^*_{inv}$$\n\n"
                                            "$$W^* = \\operatorname{norm}(W_{tot}; W_{tot,\\min}, W_{tot,\\max})$$\n\n"
                                            "$$ECO^*_{inv} = 1 - \\operatorname{norm}(ECO_{tot}; ECO_{tot,\\min}, ECO_{tot,\\max})$$\n\n"
                                            "Here $$TourSR$$ is the tournament-wide strike rate, $$LB$$ means legal balls, "
                                            "and all totals are computed over the whole tournament."
                                        ),
                                        mathjax=True,
                                    ),
                                    html.Div(
                                        [
                                            html.Div(
                                                [
                                                    html.H3("Batting Value Weights"),
                                                    html.Label("Runs weight"),
                                                    dcc.Input(
                                                        id="batting-option-3-runs-weight",
                                                        type="number",
                                                        value=default_batting_option_2["runs"],
                                                        step=0.05,
                                                    ),
                                                    html.Label("Delta SR weight"),
                                                    dcc.Input(
                                                        id="batting-option-3-delta-sr-weight",
                                                        type="number",
                                                        value=default_batting_option_2["delta_sr"],
                                                        step=0.05,
                                                    ),
                                                    html.Label("Total minimum balls faced"),
                                                    dcc.Input(
                                                        id="batting-option-3-minimum-balls",
                                                        type="number",
                                                        value=default_batting_option_2_minimum_balls,
                                                        step=1,
                                                        min=0,
                                                    ),
                                                ],
                                                style={"display": "grid", "gap": "8px", "minWidth": "220px"},
                                            ),
                                            html.Div(
                                                [
                                                    html.H3("Bowling Value Weights"),
                                                    html.Label("Wickets weight"),
                                                    dcc.Input(
                                                        id="bowling-option-2-wickets-weight",
                                                        type="number",
                                                        value=default_bowling_option_2["wickets"],
                                                        step=0.05,
                                                    ),
                                                    html.Label("Economy weight"),
                                                    dcc.Input(
                                                        id="bowling-option-2-economy-weight",
                                                        type="number",
                                                        value=default_bowling_option_2["economy"],
                                                        step=0.05,
                                                    ),
                                                    html.Label("Total minimum balls bowled"),
                                                    dcc.Input(
                                                        id="bowling-option-2-minimum-balls",
                                                        type="number",
                                                        value=default_bowling_option_2_minimum_balls,
                                                        step=1,
                                                        min=0,
                                                    ),
                                                ],
                                                style={"display": "grid", "gap": "8px", "minWidth": "220px"},
                                            ),
                                        ],
                                        style={
                                            "display": "flex",
                                            "flexWrap": "wrap",
                                            "gap": "24px",
                                            "marginBottom": "16px",
                                        },
                                    ),
                                    html.Div(
                                        [
                                            html.H3("Top 5 Combined MVP Values"),
                                            dash_table.DataTable(
                                                id="scorecard-all-rounder-table",
                                                columns=[
                                                    {"name": "Team", "id": "team"},
                                                    {"name": "Player", "id": "player"},
                                                    {"name": "w_r R*", "id": "batting_runs_component"},
                                                    {"name": "w_sr ΔSR*", "id": "batting_delta_sr_component"},
                                                    {"name": "1[B_tot≥B_min]", "id": "batting_total_balls_gate"},
                                                    {"name": "V_bat", "id": "batting_option_2"},
                                                    {"name": "w_w W*", "id": "bowling_wickets_component"},
                                                    {"name": "1[LB_tot≥LB_min]", "id": "bowling_total_balls_gate"},
                                                    {"name": "w_e ECO*_inv", "id": "bowling_economy_component"},
                                                    {"name": "V_bowl", "id": "bowling_option_2"},
                                                    {"name": "V", "id": "average_score"},
                                                ],
                                                data=all_rounder_scores.round(3).to_dict("records"),
                                                style_table={"overflowX": "auto"},
                                                style_cell={"padding": "8px", "textAlign": "center", "minWidth": "84px"},
                                                style_header={"fontWeight": "bold", "textAlign": "center"},
                                                style_data={"whiteSpace": "normal", "height": "auto"},
                                            ),
                                        ],
                                        id="scorecard-all-rounder-table-container",
                                    ),
                                ]
                            ),
                        ],
                    ),
                ]
            ),
        ],
        style={"padding": "16px 20px", "fontFamily": "Arial, sans-serif"},
    )

    @app.callback(
        Output("team-extras-bar", "figure"),
        Output("team-extras-total-stack", "figure"),
        Output("team-keeper-byes-efficiency-bar", "figure"),
        Output("team-extras-type-stack", "figure"),
        Output("bowler-discipline-scatter", "figure"),
        Output("over-heatmap", "figure"),
        Output("bowler-summary-table", "figure"),
        Output("boundary-runs-stack", "figure"),
        Output("dropped-catches-team-bar", "figure"),
        Output("boundary-dependency-table", "figure"),
        Output("dropped-catches-table", "figure"),
        Input("match-filter", "value"),
    )
    def update_figures(
        match_value: str,
    ) -> tuple[
        go.Figure,
        go.Figure,
        go.Figure,
        go.Figure,
        go.Figure,
        go.Figure,
        go.Figure,
        go.Figure,
        go.Figure,
        go.Figure,
        go.Figure,
    ]:
        if df.empty:
            empty = empty_figure("No commentary CSVs found")
            return (empty, empty, empty, empty, empty, empty, empty, empty, empty, empty, empty)

        filtered = df if match_value == "ALL" else df[df["match_key"] == match_value]
        match_label_order = ordered_match_labels(filtered)
        over_label_order = ordered_over_labels(filtered)
        team_label_order = ordered_team_labels(filtered)

        team_summary = aggregate_team_summary(filtered)
        bowler_summary = aggregate_bowler_summary(filtered)
        over_summary = aggregate_over_summary(filtered)
        boundary_summary = aggregate_boundary_dependency(filtered)
        dropped_summary = aggregate_dropped_catches(filtered)

        extras_stack = team_summary.melt(
            id_vars=["match_key", "match_label", "bowling_team"],
            value_vars=["wide_runs", "no_ball_runs", "bye_runs", "leg_bye_runs"],
            var_name="extra_type",
            value_name="runs",
        )
        team_fig_kwargs = {
            "data_frame": extras_stack,
            "x": "bowling_team",
            "y": "runs",
            "color": "extra_type",
            "barmode": "stack",
            "hover_data": ["match_label"],
            "title": "Extra Runs Conceded by Bowling Team",
            "category_orders": {
                "match_label": match_label_order,
                "bowling_team": team_label_order,
            },
        }
        if match_value == "ALL":
            team_fig_kwargs["facet_col"] = "match_label"
            team_fig_kwargs["facet_col_wrap"] = 3
        team_fig = px.bar(**team_fig_kwargs)
        team_fig.update_layout(
            legend_title_text="Extra type",
            xaxis_title="Bowling team",
            yaxis_title="Extra runs",
        )

        team_total_stack_fig = px.bar(
            team_summary,
            x="bowling_team",
            y="extras_runs",
            color="match_label",
            barmode="stack",
            hover_data=["wide_runs", "no_ball_runs", "bye_runs", "leg_bye_runs", "extras_per_over"],
            title="Total Extras by Team with Match Contribution",
            category_orders={"bowling_team": team_label_order, "match_label": match_label_order},
        )
        team_total_stack_fig.update_layout(
            xaxis_title="Bowling team",
            yaxis_title="Total extras",
            legend_title_text="Match",
        )

        keeper_byes_efficiency = (
            team_summary.groupby("bowling_team", as_index=False)[["bye_runs", "legal_balls"]].sum()
        )
        keeper_byes_efficiency["byes_per_ball"] = keeper_byes_efficiency.apply(
            lambda row: row["bye_runs"] / row["legal_balls"] if row["legal_balls"] > 0 else 0.0,
            axis=1,
        )
        keeper_byes_efficiency_fig = px.bar(
            keeper_byes_efficiency.sort_values(["byes_per_ball", "bowling_team"], ascending=[False, True]),
            x="bowling_team",
            y="byes_per_ball",
            text="byes_per_ball",
            hover_data=["bye_runs", "legal_balls"],
            title="Keeper Byes Efficiency by Team (Lower is better)",
            category_orders={"bowling_team": team_label_order},
        )
        keeper_byes_efficiency_fig.update_traces(texttemplate="%{text:.3f}", textposition="outside")
        keeper_byes_efficiency_fig.update_layout(
            xaxis_title="Team",
            yaxis_title="Byes per ball across all matches",
            showlegend=False,
        )

        team_type_stack = (
            team_summary.groupby("bowling_team", as_index=False)[
                ["wide_runs", "no_ball_runs", "bye_runs", "leg_bye_runs"]
            ]
            .sum()
            .melt(
                id_vars=["bowling_team"],
                value_vars=["wide_runs", "no_ball_runs", "bye_runs", "leg_bye_runs"],
                var_name="extra_type",
                value_name="runs",
            )
        )
        team_type_stack_fig = px.bar(
            team_type_stack,
            x="bowling_team",
            y="runs",
            color="extra_type",
            barmode="stack",
            title="Total Extras by Team with Extra Type Contribution",
            category_orders={"bowling_team": team_label_order},
        )
        team_type_stack_fig.update_layout(
            xaxis_title="Bowling team",
            yaxis_title="Total extras",
            legend_title_text="Extra type",
        )

        scatter_fig = px.scatter(
            bowler_summary,
            x="overs_bowled",
            y="extras_per_over",
            size="extras_runs",
            color="bowling_team",
            hover_data=[
                "match_title",
                "match_label",
                "bowler",
                "wide_runs",
                "no_ball_runs",
                "bye_runs",
                "leg_bye_runs",
                "extra_event_rate",
                "discipline_score",
            ],
            title="Bowler Extras Discipline: Volume vs Rate",
        )
        scatter_fig.update_layout(
            xaxis_title="Overs bowled in that match",
            yaxis_title="Extras per over in that match",
        )

        if over_summary.empty:
            heatmap_fig = empty_figure("")
        else:
            match_count = max(1, over_summary["match_label"].nunique())
            heatmap_fig = px.density_heatmap(
                over_summary,
                x="over_label",
                y="bowler",
                z="extras_runs",
                histfunc="sum",
                facet_col="match_label" if match_value == "ALL" else None,
                facet_col_wrap=match_count if match_value == "ALL" else 0,
                color_continuous_scale="Reds",
                category_orders={"match_label": match_label_order, "over_label": over_label_order},
                title="Extras Hotspot",
            )
        heatmap_fig.update_layout(title="Extras Hotspot")
        clean_facet_annotations(heatmap_fig)

        display_table = bowler_summary[
            [
                "match_title",
                "match_label",
                "bowling_team",
                "bowler",
                "overs_bowled",
                "extras_runs",
                "wide_runs",
                "no_ball_runs",
                "bye_runs",
                "leg_bye_runs",
                "extras_per_over",
                "extra_event_rate",
                "discipline_score",
            ]
        ].round({"overs_bowled": 2, "extras_per_over": 2, "extra_event_rate": 3})
        table_fig = table_figure(
            list(display_table.columns),
            [display_table[col] for col in display_table.columns],
            "Bowler Discipline Summary",
        )

        boundary_mix = boundary_summary.melt(
            id_vars=["match_key", "match_label", "batting_team"],
            value_vars=["boundary_runs", "non_boundary_bat_runs", "extras_runs"],
            var_name="run_source",
            value_name="runs",
        )
        boundary_mix_fig = px.bar(
            boundary_mix,
            x="batting_team",
            y="runs",
            color="run_source",
            facet_col="match_label" if match_value == "ALL" else None,
            title="Innings Run Mix: Boundaries vs Running vs Extras",
            category_orders={"match_label": match_label_order, "batting_team": team_label_order},
        )
        boundary_mix_fig.update_layout(legend_title_text="Run source")
        clean_facet_annotations(boundary_mix_fig)

        if dropped_summary.empty:
            dropped_team_fig = empty_figure("Dropped Catches by Fielding Team")
        else:
            dropped_team_fig = px.bar(
                dropped_summary,
                x="fielding_team",
                y="dropped_catches",
                color="match_label",
                barmode="stack",
                hover_data=["fielders_involved"],
                title="Dropped Catches by Fielding Team",
                category_orders={"fielding_team": team_label_order, "match_label": match_label_order},
            )
            dropped_team_fig.update_layout(legend_title_text="Match")

        boundary_table_df = boundary_summary[
            [
                "match_title",
                "match_label",
                "batting_team",
                "innings_runs",
                "boundary_runs",
                "non_boundary_bat_runs",
                "extras_runs",
                "boundaries",
                "boundary_dependency_pct",
            ]
        ].round({"boundary_dependency_pct": 2})
        boundary_table_fig = table_figure(
            list(boundary_table_df.columns),
            [boundary_table_df[col] for col in boundary_table_df.columns],
            "Boundary Dependency Summary",
        )

        dropped_table_df = dropped_summary.copy()
        dropped_table_fig = table_figure(
            list(dropped_table_df.columns),
            [dropped_table_df[col] for col in dropped_table_df.columns],
            "Dropped Catches Summary",
        )
        return (
            team_fig,
            team_total_stack_fig,
            keeper_byes_efficiency_fig,
            team_type_stack_fig,
            scatter_fig,
            heatmap_fig,
            table_fig,
            boundary_mix_fig,
            dropped_team_fig,
            boundary_table_fig,
            dropped_table_fig,
        )

    @app.callback(
        Output("scorecard-all-rounder-table-container", "children"),
        Input("match-filter", "value"),
        Input("batting-option-3-runs-weight", "value"),
        Input("batting-option-3-delta-sr-weight", "value"),
        Input("batting-option-3-minimum-balls", "value"),
        Input("bowling-option-2-wickets-weight", "value"),
        Input("bowling-option-2-economy-weight", "value"),
        Input("bowling-option-2-minimum-balls", "value"),
    )
    def update_option_score_table(
        match_value: str,
        batting_option_3_runs_weight: float | None,
        batting_option_3_delta_sr_weight: float | None,
        batting_option_3_minimum_balls: float | None,
        bowling_option_2_wickets_weight: float | None,
        bowling_option_2_economy_weight: float | None,
        bowling_option_2_minimum_balls: float | None,
    ) -> list[html.Component]:
        score_table_df = (
            compute_scorecard_all_rounder_score_table(
                scorecards_dir,
                match_key=match_value,
                batting_runs_weight=float(batting_option_3_runs_weight or 0.0),
                batting_strike_rate_weight=float(batting_option_3_delta_sr_weight or 0.0),
                batting_minimum_balls_faced=max(0, int(batting_option_3_minimum_balls or 0)),
                bowling_wickets_weight=float(bowling_option_2_wickets_weight or 0.0),
                bowling_economy_weight=float(bowling_option_2_economy_weight or 0.0),
                bowling_minimum_balls_bowled=max(0, int(bowling_option_2_minimum_balls or 0)),
            )
            if scorecards_dir is not None
            else pd.DataFrame(
                columns=[
                    "team",
                    "player",
                    "batting_runs_component",
                    "batting_delta_sr_component",
                    "batting_total_balls_gate",
                    "batting_option_2",
                    "bowling_wickets_component",
                    "bowling_total_balls_gate",
                    "bowling_economy_component",
                    "bowling_option_2",
                    "average_score",
                ]
            )
        )
        return [
            html.H3("Top 5 Combined MVP Values"),
            dash_table.DataTable(
                id="scorecard-all-rounder-table",
                columns=[
                    {"name": "Team", "id": "team"},
                    {"name": "Player", "id": "player"},
                    {"name": "w_r R*", "id": "batting_runs_component"},
                    {"name": "w_sr ΔSR*", "id": "batting_delta_sr_component"},
                    {"name": "1[B_tot≥B_min]", "id": "batting_total_balls_gate"},
                    {"name": "V_bat", "id": "batting_option_2"},
                    {"name": "w_w W*", "id": "bowling_wickets_component"},
                    {"name": "1[LB_tot≥LB_min]", "id": "bowling_total_balls_gate"},
                    {"name": "w_e ECO*_inv", "id": "bowling_economy_component"},
                    {"name": "V_bowl", "id": "bowling_option_2"},
                    {"name": "V", "id": "average_score"},
                ],
                data=score_table_df.round(3).to_dict("records"),
                style_table={"overflowX": "auto"},
                style_cell={"padding": "8px", "textAlign": "center", "minWidth": "84px"},
                style_header={"fontWeight": "bold", "textAlign": "center"},
                style_data={"whiteSpace": "normal", "height": "auto"},
            ),
        ]

    return app


def create_dash_app(
    input_dir: Path = Path("ball-by-ball"),
    points_table_path: Path = Path("scorecards/tournament_points_table.json"),
    figure_config_path: Path = Path("dashboard_figures.yaml"),
) -> dash.Dash:
    df = load_commentary_data(input_dir)
    points_df = load_points_table(points_table_path)
    figure_config = load_figure_config(figure_config_path)
    return build_app(df, points_df, figure_config, points_table_path.parent)


def main() -> int:
    args = parse_args()
    app = create_dash_app(args.input_dir, args.points_table, args.figure_config)
    app.run(
        host=args.host,
        port=args.port,
        debug=not args.no_reload,
        dev_tools_hot_reload=not args.no_reload,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
