#!/usr/bin/env python
"""Dash app for CricHeroes ball-by-ball analytics and tournament points table."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import dash
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import yaml
from dash import Input, Output, dash_table, dcc, html

from commentary_common import parse_match_csv_metadata


DROP_RE = re.compile(r"dropped by\s+(?P<fielder>[^,#]+)", re.IGNORECASE)
APP_VERSION = "0.2.0"
LAST_UPDATED = "2026-04-26 11:15 IST"
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

    return points_df.sort_values(["Points", "Net RR"], ascending=[False, False])


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
            fours=("boundary_type", lambda s: int((s == "FOUR").sum())),
            sixes=("boundary_type", lambda s: int((s == "SIX").sum())),
        )
        .reset_index()
    )
    grouped = grouped[grouped["batsman"].ne("")]
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

    wicket_type_df = top_bowling_df[["bowler", *wicket_type_columns]].melt(
        id_vars=["bowler"],
        value_vars=wicket_type_columns,
        var_name="wicket_type",
        value_name="count",
    )
    wicket_type_df = wicket_type_df[wicket_type_df["count"] > 0].copy()
    if wicket_type_df.empty:
        return empty_figure("Wicket Types for Top 5 Bowlers")

    wicket_type_df["wicket_type_label"] = wicket_type_df["wicket_type"].map(label_map)
    figure = px.bar(
        wicket_type_df,
        x="bowler",
        y="count",
        color="wicket_type_label",
        barmode="group",
        title="Wicket Types for Top 5 Bowlers",
        category_orders={"bowler": top_bowling_df["bowler"].tolist()},
    )
    figure.update_layout(
        xaxis_title="Bowler",
        yaxis_title="Dismissals",
        legend_title_text="Wicket type",
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
    return dash_table.DataTable(
        id="top-bowling-table",
        columns=[
            {"name": ["Identity", "Team"], "id": "bowling_team"},
            {"name": ["Identity", "Bowler"], "id": "bowler"},
            {"name": ["Wickets", "Total"], "id": "wickets"},
            {"name": ["Wicket Types", "Caught"], "id": "caught"},
            {"name": ["Wicket Types", "Bowled"], "id": "bowled"},
            {"name": ["Wicket Types", "Stumped"], "id": "stumped"},
            {"name": ["Wicket Types", "Hit wicket"], "id": "hit_wicket"},
            {"name": ["Workload", "Overs"], "id": "overs_bowled"},
            {"name": ["Runs", "Conceded"], "id": "runs_conceded"},
            {"name": ["Runs", "Extras"], "id": "extras"},
            {"name": ["Rates", "Economy"], "id": "economy"},
            {"name": ["Rates", "SR"], "id": "bowling_strike_rate"},
            {"name": ["Rates", "Avg"], "id": "bowling_average"},
        ],
        data=display_df.to_dict("records"),
        merge_duplicate_headers=True,
        style_table={"overflowX": "auto"},
        style_cell={"padding": "8px", "textAlign": "center", "minWidth": "84px"},
        style_header={"fontWeight": "bold", "textAlign": "center"},
        style_data={"whiteSpace": "normal", "height": "auto"},
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
    points_df: pd.DataFrame, commentary_df: pd.DataFrame
) -> tuple[go.Figure, go.Figure, dash_table.DataTable, go.Figure]:
    if points_df.empty:
        empty = empty_figure("Tournament Points Table")
        empty_table = build_top_bowling_table_component(pd.DataFrame())
        return empty, empty, empty_table, empty

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
    display_df = points_df[available_columns].copy()
    if "Net RR" in display_df.columns:
        display_df["Net RR"] = display_df["Net RR"].round(3)

    table_fig = table_figure(
        list(display_df.columns),
        [display_df[col] for col in display_df.columns],
        "Tournament Points Table",
    )

    if commentary_df.empty:
        top_batting_df = pd.DataFrame(
            columns=["batting_team", "batsman", "runs", "balls", "fours", "sixes", "strike_rate"]
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
        top_batting_df = aggregate_batting_leaders(commentary_df)
        top_bowling_df = aggregate_bowling_leaders(commentary_df)

    batting_table_fig = table_figure(
        ["Team", "Batsman", "Runs", "Balls", "4s", "6s", "SR"],
        [
            top_batting_df.get("batting_team", pd.Series(dtype=str)),
            top_batting_df.get("batsman", pd.Series(dtype=str)),
            top_batting_df.get("runs", pd.Series(dtype=int)),
            top_batting_df.get("balls", pd.Series(dtype=int)),
            top_batting_df.get("fours", pd.Series(dtype=int)),
            top_batting_df.get("sixes", pd.Series(dtype=int)),
            top_batting_df.get("strike_rate", pd.Series(dtype=float)).round(2),
        ],
        "Top 5 Batting Performances",
    )

    bowling_table_component = build_top_bowling_table_component(top_bowling_df)
    top_bowler_wicket_types_fig = build_top_bowler_wicket_types_figure(top_bowling_df)

    return table_fig, batting_table_fig, bowling_table_component, top_bowler_wicket_types_fig


def build_app(
    df: pd.DataFrame, points_df: pd.DataFrame, figure_config: dict[str, bool]
) -> dash.Dash:
    app = dash.Dash(__name__)
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

    points_table_fig, top_batting_fig, top_bowling_table, top_bowler_wicket_types_fig = (
        build_points_table_components(points_df, df)
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
                [
                    dcc.Tab(
                        label="Discipline & Boundary",
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
                        label="Points Table",
                        children=[
                            html.Div(
                                dcc.Graph(figure=points_table_fig, id="points-table-table"),
                                style=component_style(figure_config["points_table_table"]),
                            ),
                            html.Div(
                                dcc.Graph(figure=top_batting_fig, id="top-batting-table"),
                                style=component_style(figure_config["top_batting_table"]),
                            ),
                            html.Div(
                                [
                                    html.H2("Top 5 Bowling Performances"),
                                    top_bowling_table,
                                ],
                                style=component_style(figure_config["top_bowling_table"]),
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

    return app


def create_dash_app(
    input_dir: Path = Path("ball-by-ball"),
    points_table_path: Path = Path("scorecards/tournament_points_table.json"),
    figure_config_path: Path = Path("dashboard_figures.yaml"),
) -> dash.Dash:
    df = load_commentary_data(input_dir)
    points_df = load_points_table(points_table_path)
    figure_config = load_figure_config(figure_config_path)
    return build_app(df, points_df, figure_config)


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
