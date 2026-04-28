# Tournament Batting and Bowling Scores

This note defines a pragmatic tournament-wide batting score and bowling score using the ball-by-ball commentary already present in this repo. It is inspired by CricHeroes MVP scoring, but simplified to match the fields we actually have in the commentary CSVs.<sup>[1](https://blog.cricheroes.com/most-valuable-player-mvp-by-cricheroes/)</sup>

## Scope

- Matches covered: 8
- Innings covered: 16
- Data source: `ball-by-ball/*.csv`
- Format assumption: the recorded innings top out at over index `13`, so the tournament behaves like a 14-over competition. That maps most closely to the CricHeroes `13-16 overs` bracket.<sup>[1](https://blog.cricheroes.com/most-valuable-player-mvp-by-cricheroes/)</sup>

## Batting score

For each batting innings \(i\):

$$
\text{BaseBattingScore}_i = \frac{R_i}{10}
$$

$$
\text{StrikeRate}_i = \frac{100 \times R_i}{B_i}
$$

$$
\text{TeamStrikeRate}_i = \frac{100 \times TR_i}{TB_i}
$$

$$
\text{SRBonus}_i =
\begin{cases}
\left(\frac{\text{StrikeRate}_i}{\text{TeamStrikeRate}_i}\right) \times 0.08 \times \text{BaseBattingScore}_i, & \text{if } \text{StrikeRate}_i > \text{TeamStrikeRate}_i \\
0, & \text{otherwise}
\end{cases}
$$

$$
\text{BattingScore}_i = \text{BaseBattingScore}_i + \text{SRBonus}_i
$$

$$
\text{TournamentBattingScore}_p = \sum_i \text{BattingScore}_{p,i}
$$

Where:

- \(R_i\): batter runs in innings \(i\)
- \(B_i\): balls faced in innings \(i\), excluding wides
- \(TR_i\): total innings runs
- \(TB_i\): total innings balls, excluding wides

### Batting notes

- This follows the CricHeroes idea that runs are the base currency and faster-than-team scoring deserves a bonus.<sup>[1](https://blog.cricheroes.com/most-valuable-player-mvp-by-cricheroes/)</sup>
- The older CricHeroes par-score adjustment is intentionally not used here because the commentary dataset does not explicitly preserve the batting-order semantics needed for a clean implementation.<sup>[1](https://blog.cricheroes.com/most-valuable-player-mvp-by-cricheroes/)</sup>
- Batting penalties for slow innings are not applied.

## Bowling score

For each wicket \(w\) credited to the bowler:

$$
\text{WicketBase}_w = \frac{16 \times \text{Strength}(\text{Position}_w)}{10}
$$

with:

$$
\text{Strength}(\text{Position}) =
\begin{cases}
1.0, & \text{positions } 1 \text{ to } 4 \\
0.8, & \text{positions } 5 \text{ to } 8 \\
0.6, & \text{positions } 9 \text{ to } 11
\end{cases}
$$

For each bowling innings \(i\):

$$
\text{BowlerRunRate}_i = \frac{100 \times RC_i}{LB_i}
$$

$$
\text{TeamRunRate}_i = \frac{100 \times TRC_i}{TLB_i}
$$

$$
\text{BowlingSRBonus}_i =
\begin{cases}
\left(\frac{\text{TeamRunRate}_i}{\text{BowlerRunRate}_i}\right) \times 0.08, & \text{if } \text{TeamRunRate}_i \ge \text{BowlerRunRate}_i \\
0, & \text{otherwise}
\end{cases}
$$

$$
\text{AdditionalWicketBonus}_i =
\begin{cases}
1.5, & \text{if wickets} \ge 10 \\
1.0, & \text{if wickets} \ge 5 \\
0.5, & \text{if wickets} \ge 3 \\
0, & \text{otherwise}
\end{cases}
$$

$$
\text{MaidenBonus}_i = 0.8 \times M_i
$$

$$
\text{BowlingScore}_i = \sum_w \text{WicketBase}_w + \text{AdditionalWicketBonus}_i + \text{BowlingSRBonus}_i + \text{MaidenBonus}_i
$$

$$
\text{TournamentBowlingScore}_p = \sum_i \text{BowlingScore}_{p,i}
$$

Where:

- \(RC_i\): runs conceded by the bowler in innings \(i\)
- \(LB_i\): legal balls bowled by the bowler in innings \(i\)
- \(TRC_i\): total runs conceded by the bowling team in that innings
- \(TLB_i\): total legal balls in that innings
- \(M_i\): maiden overs by that bowler in that innings

### Bowling notes

- The 14-over assumption uses CricHeroes' `13-16 overs` base wicket value of `16` runs per wicket.<sup>[1](https://blog.cricheroes.com/most-valuable-player-mvp-by-cricheroes/)</sup>
- Batting position is inferred from first appearance order in the innings because the commentary CSVs do not store an explicit batting-order field.
- Bowler-attributed wickets exclude `run_out` and `retired_hurt`, matching the repo's current bowling aggregation logic.
- Maiden overs are counted only when an over has exactly 6 legal balls and 0 runs conceded.
- Bowling penalties for expensive spells are not applied.

## Current tournament leaders

### Top batting scores

| Rank | Team | Batter | Matches | Runs | Balls | 4s | 6s | Strike Rate | Batting Score |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Akatsuki Corps | Shadab | 4 | 97 | 73 | 4 | 5 | 132.88 | 10.83 |
| 2 | Minions | Vinay LS | 4 | 81 | 70 | 6 | 1 | 115.71 | 8.98 |
| 3 | Illuminati | Jahir | 4 | 66 | 66 | 1 | 3 | 100.00 | 7.22 |
| 4 | Minions | Sayooj Kalapurakkal | 4 | 57 | 87 | 1 | 2 | 65.52 | 5.80 |
| 5 | The Fibrillators | Gokul S | 4 | 48 | 62 | 3 | 0 | 77.42 | 5.07 |
| 6 | Illuminati | Sibin k s | 4 | 35 | 39 | 2 | 1 | 89.74 | 3.76 |
| 7 | The Fibrillators | Sandeep Eldho Jacob | 4 | 31 | 27 | 0 | 3 | 114.81 | 3.51 |
| 8 | Akatsuki Corps | Vishal Ls | 4 | 27 | 41 | 0 | 0 | 65.85 | 2.70 |
| 9 | Illuminati | Prashanth | 3 | 19 | 21 | 2 | 0 | 90.48 | 2.16 |
| 10 | Illuminati | Sharath | 3 | 20 | 32 | 0 | 0 | 62.50 | 2.13 |

### Top bowling scores

| Rank | Team | Bowler | Matches | Wickets | Overs | Runs Conceded | Maidens | Economy | Bowling Score |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Illuminati | Sharath | 4 | 11 | 10.8 | 48 | 0 | 4.43 | 17.40 |
| 2 | Akatsuki Corps | Raghi | 4 | 8 | 10.8 | 43 | 0 | 3.97 | 12.57 |
| 3 | Minions | Akash L S | 4 | 5 | 9.3 | 33 | 0 | 3.54 | 7.57 |
| 4 | Minions | Sayooj Kalapurakkal | 4 | 5 | 10.8 | 47 | 0 | 4.34 | 7.44 |
| 5 | The Fibrillators | Suresh | 4 | 5 | 10.8 | 57 | 0 | 5.26 | 6.59 |
| 6 | Minions | SHAMAL KM | 3 | 5 | 6.8 | 31 | 0 | 4.54 | 6.55 |
| 7 | Illuminati | Sibin k s | 4 | 5 | 9.8 | 70 | 0 | 7.12 | 6.49 |
| 8 | Akatsuki Corps | Rama | 4 | 4 | 10.2 | 49 | 0 | 4.82 | 6.07 |
| 9 | Minions | Salman faris | 3 | 4 | 8.0 | 44 | 0 | 5.50 | 5.94 |
| 10 | The Fibrillators | Arvind Thomas | 4 | 4 | 10.0 | 76 | 0 | 7.60 | 5.44 |

## Suggested use

| Option | Description |
| --- | --- |
| Tournament leaderboard | Use `TournamentBattingScore` and `TournamentBowlingScore` as season-long player ranking columns. |
| Match card summary | Keep the same formulas at innings level to produce per-match batting and bowling scorecards. |
| Future refinement | If full scorecard batting-order metadata becomes available, add par-score bonus exactly as CricHeroes describes. |

## References

1. <sup>[1](https://blog.cricheroes.com/most-valuable-player-mvp-by-cricheroes/)</sup> CricHeroes, "Most Valuable Player (MVP) by CricHeroes", updated November 20, 2025.
