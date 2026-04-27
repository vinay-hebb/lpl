from __future__ import annotations

import pandas as pd

from extras_discipline_dashboard import (
    aggregate_batting_leaders,
    aggregate_bowling_leaders,
    build_points_table_components,
    build_top_batter_scoring_types_figure,
    build_top_bowler_wicket_types_figure,
    resolve_wicket_type,
)


def test_resolve_wicket_type_maps_commentary_subtypes() -> None:
    rows = [
        ({"dismissal_kind": "out", "commentary": "A to B, OUT Caught out, Caught by Fielder"}, "caught"),
        ({"dismissal_kind": "out", "commentary": "A to B, OUT Bowled B b A"}, "bowled"),
        ({"dismissal_kind": "out", "commentary": "A to B, OUT LBW B lbw b A"}, "lbw"),
        ({"dismissal_kind": "out", "commentary": "A to B, OUT Stumped B st Keeper b A"}, "stumped"),
        ({"dismissal_kind": "out", "commentary": "A to B, OUT Hit wicket B hit wkt b A"}, "hit_wicket"),
        ({"dismissal_kind": "run_out", "commentary": "A to B, OUT Run out"}, "run_out"),
        ({"dismissal_kind": "retired_hurt", "commentary": "A to B, retired hurt"}, "retired_hurt"),
    ]

    for row, expected in rows:
        assert resolve_wicket_type(pd.Series(row)) == expected


def test_aggregate_bowling_leaders_includes_wicket_type_counts() -> None:
    df = pd.DataFrame(
        [
            {
                "bowling_team": "Team 1",
                "bowler": "Bowler A",
                "is_wicket": 1,
                "dismissal_kind": "out",
                "wicket_type": "caught",
                "batsman_runs": 0,
                "extras": 0,
                "extra_type": "none",
                "is_legal_ball": 1,
            },
            {
                "bowling_team": "Team 1",
                "bowler": "Bowler A",
                "is_wicket": 1,
                "dismissal_kind": "out",
                "wicket_type": "bowled",
                "batsman_runs": 0,
                "extras": 0,
                "extra_type": "none",
                "is_legal_ball": 1,
            },
            {
                "bowling_team": "Team 1",
                "bowler": "Bowler A",
                "is_wicket": 1,
                "dismissal_kind": "run_out",
                "wicket_type": "run_out",
                "batsman_runs": 0,
                "extras": 0,
                "extra_type": "none",
                "is_legal_ball": 1,
            },
            {
                "bowling_team": "Team 2",
                "bowler": "Bowler B",
                "is_wicket": 1,
                "dismissal_kind": "out",
                "wicket_type": "hit_wicket",
                "batsman_runs": 0,
                "extras": 0,
                "extra_type": "none",
                "is_legal_ball": 1,
            },
        ]
    )

    result = aggregate_bowling_leaders(df)

    bowler_a = result[result["bowler"] == "Bowler A"].iloc[0]
    assert bowler_a["wickets"] == 2
    assert bowler_a["caught"] == 1
    assert bowler_a["bowled"] == 1
    assert bowler_a["hit_wicket"] == 0

    bowler_b = result[result["bowler"] == "Bowler B"].iloc[0]
    assert bowler_b["wickets"] == 1
    assert bowler_b["hit_wicket"] == 1


def test_aggregate_batting_leaders_includes_scoring_shot_counts() -> None:
    df = pd.DataFrame(
        [
            {
                "batting_team": "Team 1",
                "batsman": "Batter A",
                "batsman_runs": 1,
                "extra_type": "none",
                "boundary_type": "",
            },
            {
                "batting_team": "Team 1",
                "batsman": "Batter A",
                "batsman_runs": 2,
                "extra_type": "none",
                "boundary_type": "",
            },
            {
                "batting_team": "Team 1",
                "batsman": "Batter A",
                "batsman_runs": 4,
                "extra_type": "none",
                "boundary_type": "FOUR",
            },
            {
                "batting_team": "Team 1",
                "batsman": "Batter A",
                "batsman_runs": 6,
                "extra_type": "none",
                "boundary_type": "SIX",
            },
            {
                "batting_team": "Team 1",
                "batsman": "Batter A",
                "batsman_runs": 1,
                "extra_type": "wide",
                "boundary_type": "",
            },
        ]
    )

    result = aggregate_batting_leaders(df)

    batter_a = result[result["batsman"] == "Batter A"].iloc[0]
    assert batter_a["runs"] == 13
    assert batter_a["balls"] == 4
    assert batter_a["ones"] == 1
    assert batter_a["twos"] == 1
    assert batter_a["fours"] == 1
    assert batter_a["sixes"] == 1
    assert batter_a["ones_runs"] == 1
    assert batter_a["twos_runs"] == 2
    assert batter_a["fours_runs"] == 4
    assert batter_a["sixes_runs"] == 6


def test_build_top_bowler_wicket_types_figure_uses_non_zero_counts() -> None:
    top_bowling_df = pd.DataFrame(
        [
            {
                "bowler": "Bowler A",
                "caught": 2,
                "bowled": 1,
                "stumped": 0,
                "hit_wicket": 1,
            }
        ]
    )

    figure = build_top_bowler_wicket_types_figure(top_bowling_df)

    trace_names = {trace.name for trace in figure.data}
    assert trace_names == {"Caught", "Bowled", "Hit wicket"}
    percentages = {trace.name: trace.y[0] for trace in figure.data}
    assert percentages == {"Caught": 50.0, "Bowled": 25.0, "Hit wicket": 25.0}
    absolute_values = {trace.name: trace.customdata[0][0] for trace in figure.data}
    assert absolute_values == {"Caught": 2, "Bowled": 1, "Hit wicket": 1}
    assert "Wickets: %{customdata[0]}" in figure.data[0].hovertemplate


def test_build_top_batter_scoring_types_figure_uses_percentage_contributions() -> None:
    top_batting_df = pd.DataFrame(
        [
            {
                "batsman": "Batter A",
                "ones_runs": 2,
                "twos_runs": 4,
                "fours_runs": 8,
                "sixes_runs": 6,
            }
        ]
    )

    figure = build_top_batter_scoring_types_figure(top_batting_df)

    trace_names = {trace.name for trace in figure.data}
    assert trace_names == {"1s", "2s", "4s", "6s"}
    percentages = {trace.name: trace.y[0] for trace in figure.data}
    assert percentages == {"1s": 10.0, "2s": 20.0, "4s": 40.0, "6s": 30.0}
    absolute_values = {trace.name: trace.customdata[0][0] for trace in figure.data}
    assert absolute_values == {"1s": 2, "2s": 4, "4s": 8, "6s": 6}
    assert "Runs: %{customdata[0]}" in figure.data[0].hovertemplate


def test_build_points_table_components_uses_grouped_bowling_headers() -> None:
    points_df = pd.DataFrame(
        [
            {
                "Team Name": "Team 1",
                "Matches": 1,
                "Won": 1,
                "Lost": 0,
                "Points": 2,
                "Net RR": 1.0,
                "For": "10/1",
                "Against": "9/2",
                "Last 5": "W",
            }
        ]
    )
    commentary_df = pd.DataFrame(
        [
            {
                "batting_team": "Team 2",
                "bowling_team": "Team 1",
                "batsman": "Batter A",
                "bowler": "Bowler A",
                "batsman_runs": 0,
                "extras": 0,
                "extra_type": "none",
                "is_boundary": 0,
                "boundary_type": "",
                "is_wicket": 1,
                "dismissal_kind": "out",
                "wicket_type": "caught",
                "is_legal_ball": 1,
                "total_runs": 0,
            }
        ]
    )

    (
        points_table_component,
        batting_table_component,
        bowling_table_component,
        batting_chart_figure,
        _,
    ) = build_points_table_components(points_df, commentary_df)

    assert points_table_component.columns == [
        {"name": "Team Name", "id": "Team Name"},
        {"name": "Matches", "id": "Matches"},
        {"name": "Won", "id": "Won"},
        {"name": "Lost", "id": "Lost"},
        {"name": "Points", "id": "Points"},
        {"name": "Net RR", "id": "Net RR"},
        {"name": "For", "id": "For"},
        {"name": "Against", "id": "Against"},
        {"name": "Last 5", "id": "Last 5"},
    ]
    assert points_table_component.data == [
        {
            "Team Name": "Team 1",
            "Matches": 1,
            "Won": 1,
            "Lost": 0,
            "Points": 2,
            "Net RR": 1.0,
            "For": "10/1",
            "Against": "9/2",
            "Last 5": "W",
        }
    ]
    assert batting_table_component.merge_duplicate_headers is True
    assert batting_table_component.columns[:5] == [
        {"name": ["Identity", "Team"], "id": "batting_team"},
        {"name": ["Identity", "Batsman"], "id": "batsman"},
        {"name": ["Output", "Runs"], "id": "runs"},
        {"name": ["Output", "Balls"], "id": "balls"},
        {"name": ["Scoring Shots", "1s"], "id": "ones"},
    ]

    assert bowling_table_component.merge_duplicate_headers is True
    assert bowling_table_component.columns[:4] == [
        {"name": ["Identity", "Team"], "id": "bowling_team"},
        {"name": ["Identity", "Bowler"], "id": "bowler"},
        {"name": ["Wickets", "Total"], "id": "wickets"},
        {"name": ["Wicket Types", "Caught"], "id": "caught"},
    ]
    assert batting_chart_figure.layout.title.text == "Scoring-Type Run Contribution for Top 5 Batters"
