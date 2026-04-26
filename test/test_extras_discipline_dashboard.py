from __future__ import annotations

import pandas as pd

from extras_discipline_dashboard import (
    aggregate_bowling_leaders,
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
