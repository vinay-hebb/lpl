# Version Log

## 0.5.0 - 2026-05-05

- Added the most recent match data and commentary.
- Fixed workbook rank formulas and MVP value aggregation.
- Renamed dashboard batting labels to "Batter" and standardized scorecard-derived footnotes.
- Added workbook generator, tournament equations, and expanded notation docs.
- Added generated scoring workbook to `.gitignore`.

## 0.4.0 - 2026-04-28

- Switched the batting and bowling leaderboards to scorecard-first derivation where local scorecards are sufficient, while keeping commentary-only fields such as batting 1s and 2s.
- Marked scorecard-derived points-table, batting-table, and bowling-table metrics with `*` and added dashboard footnotes explaining the data source.
- Fixed match metadata repair for inconsistent commentary CSV filenames, removed the unexpected `Unknown` team from extras charts, and corrected top-bowling team attribution and wicket totals.
- Renamed commentary PDFs to the canonical `<batting_team>_<date>.pdf` format using batting team text extracted directly from each PDF.
- Rebuilt commentary CSVs with corrected `2026-04-27` match metadata and added the normalized tournament batting/bowling scoring note plus refresh-pipeline documentation updates.

## 0.3.0 - 2026-04-27

- Converted the tournament points table to a Dash `DataTable` for consistent tabular rendering with the batting and bowling leaderboards.
- Replaced the top bowling summary with a grouped-header `DataTable`.
- Added wicket-type counts to bowling aggregation and a stacked wicket-type contribution chart for the top 5 bowlers.
- Added scoring-shot counts to batting aggregation, expanded the batting leaderboard `DataTable`, and added a stacked run-contribution chart for 1s, 2s, 4s, and 6s.

## 0.2.0 - 2026-04-25

- Updated the bowler discipline scatter axis labels to clarify that each point is for a single bowler in a single match.
- Added the bowler name to scatter hover details.
- Added dashboard metadata in the UI header: version, last updated timestamp, and this version log link.
- Added a team-level stacked chart showing total extras split by extra type contribution.
- Ordered match-faceted extras and innings charts chronologically by match date.

## 0.1.0 - 2026-04-24

- Initial dashboard release with extras discipline, boundary dependency, dropped catches, and tournament standings views.
