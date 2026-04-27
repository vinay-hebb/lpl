# Version Log

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
