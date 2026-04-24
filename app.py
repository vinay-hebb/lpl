#!/usr/bin/env python
"""Hugging Face Spaces entrypoint for the Dash app."""

from __future__ import annotations

import os
from pathlib import Path

from extras_discipline_dashboard import create_dash_app


PORT = int(os.environ.get("PORT", "7860"))
HOST = os.environ.get("HOST", "0.0.0.0")
INPUT_DIR = Path(os.environ.get("INPUT_DIR", "ball-by-ball"))
POINTS_TABLE = Path(os.environ.get("POINTS_TABLE", "scorecards/tournament_points_table.json"))
FIGURE_CONFIG = Path(os.environ.get("FIGURE_CONFIG", "dashboard_figures.yaml"))

app = create_dash_app(INPUT_DIR, POINTS_TABLE, FIGURE_CONFIG)
server = app.server


if __name__ == "__main__":
    app.run(host=HOST, port=PORT, debug=False)
