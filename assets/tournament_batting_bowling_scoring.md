# Tournament Batting and Bowling Scoring

This note defines the current tournament-wide batting and bowling scoring baseline for this repo, and also lists alternative scoring models from simple to complex. It is inspired by CricHeroes MVP scoring, but adapted to the fields we actually have locally.<sup>[1](https://blog.cricheroes.com/most-valuable-player-mvp-by-cricheroes/)</sup>

## Scope

- Matches covered: 8
- Innings covered: 16
- Data sources: `ball-by-ball/*.csv`, `scorecards/*.json`, `scorecards/*.html`
- Format assumption: the recorded innings top out at over index `13`, so the tournament behaves like a 14-over competition. That maps most closely to the CricHeroes `13-16 overs` bracket.<sup>[1](https://blog.cricheroes.com/most-valuable-player-mvp-by-cricheroes/)</sup>

## Normalization convention

All option formulas below are written as scores in the range $[0, 1]$.

$$
\operatorname{clip01}(x) = \max(0, \min(1, x))
$$

$$
\operatorname{norm}(x; x_{\min}, x_{\max}) = \operatorname{clip01}\left(\frac{x - x_{\min}}{x_{\max} - x_{\min}}\right)
$$

In practice, $x_{\min}$ and $x_{\max}$ can be chosen from tournament-level observed values or from fixed cricket-specific caps.

## Current batting baseline

For each batting innings $i$:

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

- $R_i$: batter runs in innings $i$
- $B_i$: balls faced in innings $i$, excluding wides
- $TR_i$: total innings runs
- $TB_i$: total innings balls, excluding wides

## Current bowling baseline

For each wicket $w$ credited to the bowler:

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

For each bowling innings $i$:

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

- $RC_i$: runs conceded by the bowler in innings $i$
- $LB_i$: legal balls bowled by the bowler in innings $i$
- $TRC_i$: total runs conceded by the bowling team in that innings
- $TLB_i$: total legal balls in that innings
- $M_i$: maiden overs by that bowler in that innings

## Batting score options

| Option | Description |
| --- | --- |
| 1. Simple runs-only | $S = \operatorname{norm}\left(\sum_i R_i; 0, R_{\max}\right)$. Easiest to explain, but ignores tempo and innings context. |
| 2. Runs plus strike rate gate | $S = \operatorname{clip01}\left(w_r \operatorname{norm}\left(\sum_i R_i; 0, R_{\max}\right) + w_{sr}\operatorname{norm}\left(\sum_i \max(0, \text{SR}_i - \text{TeamSR}_i); 0, \Delta \text{SR}_{\max}\right)\right)$. Good low-complexity option and close to the current baseline. |
| 3. Weighted batting impact | $S = \operatorname{clip01}\left(w_r R^* + w_{sr}\Delta \text{SR}^* + w_b B^*\right)$ where each starred term is separately normalized to $[0,1]$. Lets you reward volume and tempo separately. |
| 4. Full MVP-style batting | $S = \operatorname{clip01}\left(w_r R^* + w_{sr} SR^* + w_{par} PAR^* + w_{ctx} CONTEXT^*\right)$. Strongest model, but it needs richer batting-order and situation data.<sup>[1](https://blog.cricheroes.com/most-valuable-player-mvp-by-cricheroes/)</sup> |

## Bowling score options

| Option | Description |
| --- | --- |
| 1. Simple wickets-only | $S = \operatorname{norm}\left(\sum_i W_i; 0, W_{\max}\right)$. Easiest option, but it ignores spell quality and wicket context. |
| 2. Wickets plus economy | $S = \operatorname{clip01}\left(w_w W^* + w_e ECO^*_{inv}\right)$ where $W^*$ is normalized wickets and $ECO^*_{inv}$ is inverse-normalized economy. Good compact baseline if you want reward and control together. |
| 3. Wicket-type weighted bowling | $S = \operatorname{clip01}\left(w_c C^* + w_b B^* + w_l LBW^* + w_s ST^* + w_h HW^*\right)$ where wicket-type counts are normalized independently. Useful when dismissal mode is considered informative. |
| 4. Workload-adjusted bowling | $S = \operatorname{clip01}\left(w_w W^* + w_e ECO^*_{inv} + w_{sr} SR^*_{inv} + w_o O^*\right)$. Better when you want to reward both usage and efficiency. |
| 5. Batter-strength bowling | $S = \operatorname{clip01}\left(w_{wk} WKSTRENGTH^* + w_e ECO^*_{inv} + w_{sr} SR^*_{inv}\right)$. This is close to the current baseline. |
| 6. Full MVP-style bowling | $S = \operatorname{clip01}\left(w_{wk} WKSTRENGTH^* + w_e ECO^*_{inv} + w_{sr} SR^*_{inv} + w_m MAIDEN^* + w_h HAUL^*\right)$. Highest signal, but also the most assumption-heavy.<sup>[1](https://blog.cricheroes.com/most-valuable-player-mvp-by-cricheroes/)</sup> |

## Recommended use

| Option | Description |
| --- | --- |
| Dashboard default | Keep the current batting and bowling baselines because they are explainable and fit the available local data. |
| Simple fallback | Use runs-only for batting and wickets-only for bowling if you need a transparent leaderboard fast. |
| Future upgrade | Move to phase-aware batting and workload-adjusted bowling once the match-state and scorecard payloads are more complete. |

## Notes

- The current batting baseline is intentionally simpler than the full CricHeroes batting MVP logic because the local commentary dataset does not reliably preserve explicit batting-order semantics.
- The current bowling baseline is stronger because wicket mode, spell size, and runs conceded are recoverable from scorecards and commentary together.
- If richer scorecard fields are preserved consistently, the full MVP-style batting and bowling options become more realistic to implement.

## References

1. <sup>[1](https://blog.cricheroes.com/most-valuable-player-mvp-by-cricheroes/)</sup> CricHeroes, "Most Valuable Player (MVP) by CricHeroes", updated November 20, 2025.
