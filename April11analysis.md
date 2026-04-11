# PolyWeather Portfolio Deep Analysis — April 11, 2026

**Dataset:** 1,907 closed trades across 22 portfolios (April 1-11, 2026)
**Purpose:** Identify best strategies for live trading, optimal position sizing, and hidden risks before deploying real money.

---

## 1. Portfolio Rankings (by Profit)

| Portfolio | Trades | W | L | WR% | Invested | Profit | ROI% | Sharpe |
|-----------|--------|---|---|-----|----------|--------|------|--------|
| **Datagrab** (unlimited) | 631 | 458 | 173 | 72.6% | $1,922,884 | $94,605 | 4.9% | 0.067 |
| **US Cities Only** | 46 | 43 | 3 | 93.5% | $32,853 | $2,106 | 6.4% | 0.165 |
| **Pure Safe NO 70+** | 75 | 75 | 0 | 100.0% | $57,163 | $1,865 | 3.3% | 0.740 |
| **Safe NO Volume** | 76 | 76 | 0 | 100.0% | $56,061 | $1,779 | 3.2% | 0.877 |
| **Tim Birthday Portfolio** | 78 | 71 | 7 | 91.0% | $42,009 | $1,778 | 4.2% | 0.193 |
| **Night Owl** | 96 | 79 | 17 | 82.3% | $38,317 | $1,722 | 4.5% | 0.082 |
| Safe NO Fortress | 75 | 75 | 0 | 100.0% | $54,029 | $1,624 | 3.0% | 0.823 |
| NO-Focused Safe Portfolio | 81 | 71 | 10 | 87.7% | $39,910 | $1,618 | 4.1% | 0.090 |
| Balanced NO | 98 | 88 | 10 | 89.8% | $46,251 | $1,256 | 2.7% | 0.144 |
| NO-Only Edge Tightened v1 | 90 | 73 | 17 | 81.1% | $33,268 | $1,169 | 3.5% | 0.071 |
| China Focus | 26 | 22 | 4 | 84.6% | $30,664 | $1,118 | 3.6% | 0.076 |
| Wide Net NO | 104 | 86 | 18 | 82.7% | $42,351 | $1,012 | 2.4% | 0.050 |

**Bottom performers (losing money):**

| Portfolio | Trades | WR% | Profit | ROI% |
|-----------|--------|-----|--------|------|
| Original 10K | 145 | 61.4% | -$1,002 | -2.2% |
| City Alpha | 41 | 87.8% | -$1,232 | -3.7% |
| NO Edge Sniper | 43 | 65.1% | -$1,562 | -8.1% |
| Europe Focus | 25 | 80.0% | -$2,003 | -8.5% |
| Edge NO Micro | 58 | 65.5% | -$2,952 | -11.8% |
| High Edge 25+ | 39 | 59.0% | -$4,342 | -24.8% |
| Exact Band Specialist | 42 | 59.5% | -$5,442 | -29.1% |

### Key Insight: The "edge" bet type is the profit killer
Every losing portfolio takes edge bets aggressively. The profitable portfolios either avoid them entirely (Safe NO variants) or filter them very tightly (US Cities Only).

---

## 2. Drawdown Analysis

| Portfolio | Win Streak | Lose Streak | Max DD $ | Max DD % | Recovery (trades) |
|-----------|-----------|-------------|----------|----------|-------------------|
| US Cities Only | 17 | 1 | $1,418 | 14.2% | 13 |
| Pure Safe NO 70+ | 75 | **0** | $0.00 | 0.0% | 0 |
| Safe NO Volume | 76 | **0** | $0.00 | 0.0% | 0 |
| Tim Birthday | 26 | 1 | $719 | 7.2% | 40 |
| Night Owl | 13 | 2 | $1,705 | 17.0% | 67 |
| Balanced NO | 32 | 2 | $555 | 5.6% | 33 |
| Original 10K | 15 | **8** | $4,986 | **49.9%** | 14 |
| Exact Band Specialist | 6 | **4** | $5,906 | **59.1%** | never recovered |

**Pure Safe NO 70+ and Safe NO Volume have NEVER lost a trade.** Zero drawdown, zero losing streaks. This is the strongest signal in the entire dataset.

---

## 3. Profit Concentration

How dependent is each portfolio on its top trades?

| Portfolio | Profitable % | Top 10% of trades = % of profit | Mean/trade | Median/trade |
|-----------|-------------|--------------------------------|------------|--------------|
| US Cities Only | 93.5% | 107% (4 trades carry everything) | $45.78 | $18.48 |
| Pure Safe NO 70+ | 100.0% | 42% (well distributed) | $24.87 | $13.17 |
| Safe NO Volume | 100.0% | 36% (well distributed) | $23.40 | $14.73 |
| Tim Birthday | 91.0% | 101% (7 trades carry everything) | $22.79 | $11.70 |
| Night Owl | 82.3% | 227% (9 trades carry, losses offset) | $17.94 | $10.68 |

**Critical finding:** US Cities Only, Tim Birthday, and Night Owl have profit concentration >100% — meaning a few big winners offset losses. Remove those top trades and they'd be unprofitable. **Pure Safe NO 70+ and Safe NO Volume have broadly distributed profits** — no single trade dependency.

---

## 4. Trade Overlap & Crossover

### Overlap between Top 8 Portfolios

The Safe NO trio (Pure Safe NO 70+, Safe NO Volume, Safe NO Fortress) share ~81% of trades with each other. They're essentially the same strategy with minor parameter differences.

| Pair | Overlap % |
|------|----------|
| Pure Safe NO 70+ ↔ Safe NO Volume | 83-84% |
| Pure Safe NO 70+ ↔ Safe NO Fortress | 81% |
| Safe NO Volume ↔ Safe NO Fortress | 80-81% |
| US Cities Only ↔ Tim Birthday | 70% |
| Night Owl ↔ NO-Focused Safe | 69% |
| US Cities Only ↔ Night Owl | 23-48% |

**Running multiple Safe NO variants live would be nearly identical — pick one.**

### Consensus Winners (trades that won in 3+ portfolios)

The top consensus winners are all **NO bets on extreme bands** — markets pricing unlikely temperature outcomes that essentially never happen:

- Seattle 16-16C (won across 14 portfolios)
- Munich 15C (14 portfolios)
- Paris 25C NO, Toronto 4C NO, Austin >=29C NO (13 each)

These are the bread-and-butter safe NO trades: high probability, low payout, consistent wins.

### Consensus Losers (what to avoid)

**Every consensus loser is an "edge" bet on an "exact" band:**

| City | Band | # Portfolios Lost |
|------|------|-------------------|
| Miami | 26-26C NO | 15 |
| Madrid | 26C NO | 13 |
| Tel Aviv | 21C/20C NO | 12 each |
| Paris | 15C NO | 11 |
| Milan | 24C NO | 11 |

**Loser pattern breakdown:**
- 95% are "edge" bet type (not safe_no or sure)
- 90% are "exact" band type
- 74% are NO side
- Cities: Paris (42), Tokyo (36), Tel Aviv (34) dominate losses

### Unique Winners (differentiation)

| Portfolio | Unique wins (not in other top-5) |
|-----------|----------------------------------|
| Night Owl | 14/79 = 17.7% unique |
| US Cities Only | 8/43 = 18.6% unique |
| Tim Birthday | 10/71 = 14.1% unique |
| Safe NO Volume | 8/76 = 10.5% unique |
| Pure Safe NO 70+ | 6/75 = 8.0% unique |

### Core Overlap: Top 3 Combined

If you combined US Cities Only + Pure Safe NO 70+ + Safe NO Volume into one portfolio:
- 116 unique trades across all three
- 15 trades appear in ALL three: **100% win rate, $3,221 profit**
- The overlap set is a gold mine — these are the highest-conviction trades

---

## 5. Optimal Position Sizing

### Kelly Criterion

| Portfolio | Win Rate | Avg Win | Avg Loss | W/L Ratio | Kelly | Half-Kelly | Quarter-Kelly |
|-----------|---------|---------|----------|-----------|-------|------------|---------------|
| US Cities Only | 93.5% | $91.14 | $604.38 | 0.15 | 50.2% | 25.1% | 12.6% |
| Pure Safe NO 70+ | 100.0% | $24.87 | ~$0 | 2487x | 100% | 50% | 25% |
| Safe NO Volume | 100.0% | $23.40 | ~$0 | 2340x | 100% | 50% | 25% |
| Tim Birthday | 91.0% | $45.77 | $210.24 | 0.22 | 49.8% | 24.9% | 12.5% |
| Night Owl | 82.3% | $86.55 | $300.93 | 0.29 | 20.7% | 10.4% | 5.2% |

### Historical Backtest: Optimal Max Trade %

Starting $10,000, which max-trade-% maximizes terminal wealth?

| Portfolio | Best for Wealth | Terminal $ | Best for Sharpe | Sharpe |
|-----------|----------------|------------|-----------------|--------|
| US Cities Only | 20% | $12,074 | 4% | 0.229 |
| Pure Safe NO 70+ | 20% | $11,758 | 1% | 1.661 |
| Safe NO Volume | 20% | $11,751 | 1% | 1.821 |
| Tim Birthday | 20% | $11,622 | 20% | 0.177 |
| Night Owl | 17% | $11,722 | 17% | 0.085 |

**Note:** The Safe NO portfolios show high Kelly fractions because they've never lost. With real money, use extreme caution — a 100% win rate over 75 trades doesn't guarantee future performance. **Recommended max trade: 3-5% of capital** for these portfolios to survive the first loss.

---

## 6. Risk of Ruin

Bootstrap simulation (10,000 iterations, resampling trades with replacement):

| Portfolio | Max Consecutive Losses | P(50% DD) @ $500 | Min Capital for <5% P(50% DD) |
|-----------|----------------------|-------------------|-------------------------------|
| US Cities Only | 1 | 0.1% | ~$102 |
| Pure Safe NO 70+ | 0 | 0.0% | ~$102 |
| Safe NO Volume | 0 | 0.0% | ~$102 |
| Tim Birthday | 1 | 0.0% | ~$102 |
| Night Owl | 2 | 0.0% | ~$102 |

**All top-5 portfolios show extremely low risk of ruin.** Even $100-500 starting capital is safe based on historical data. However, this is only 10 days of data — the true distribution of losses hasn't been fully observed yet.

---

## 7. City & Time Concentration Risk

| Portfolio | Top 3 Cities (% of profit) | Max trades/day | Max same-city same-day |
|-----------|---------------------------|----------------|------------------------|
| US Cities Only | Seattle, NYC, Toronto (123%) | 14 | 4 |
| Pure Safe NO 70+ | Munich, Buenos Aires, London (32%) | 21 | 3 |
| Safe NO Volume | Munich, Dallas, Buenos Aires (34%) | 24 | 3 |
| Tim Birthday | Beijing, Seattle, Paris (71%) | 19 | 3 |
| Night Owl | Seattle, Hong Kong, Dallas (155%) | 23 | 3 |

**US Cities Only and Night Owl are heavily concentrated in Seattle.** If Seattle forecasts degrade, these portfolios get hit hard. Pure Safe NO and Safe NO Volume have the best city diversification.

---

## 8. Trade Size Distribution

| Portfolio | Mean | Median | Dominant Bucket | WR (smallest Q) | WR (largest Q) |
|-----------|------|--------|-----------------|-----------------|----------------|
| US Cities Only | $714 | $610 | $500+ (24/46) | 92% | 91% |
| Pure Safe NO 70+ | $762 | $518 | $500+ (38/75) | 100% | 100% |
| Safe NO Volume | $738 | $823 | $500+ (48/76) | 100% | 100% |
| Tim Birthday | $539 | $185 | $100-500 (49/78) | 93% | 100% |
| Night Owl | $399 | $297 | $100-500 (45/96) | 84% | 91% |

Night Owl shows an interesting pattern: **larger trades win more often (91% vs 84%)**. The strategy's capital allocation is correctly prioritizing higher-conviction trades.

---

## 9. Recommendations for Live Trading

### Tier 1 — Strongest candidates

**Safe NO Volume** or **Pure Safe NO 70+** (pick one — they're 83% identical)
- 100% win rate, 0 drawdowns, best Sharpe ratios in the dataset
- Broadly diversified across cities (no single-city dependency)
- Conservative returns (~3.2% ROI) but maximum consistency
- **Recommended max trade: 3% of capital** (despite Kelly suggesting much more, protect against the first-ever loss)
- **Risk:** Has never been tested by a loss. When the first loss comes, it could be psychologically jarring. Set expectations: you WILL eventually lose a trade.

### Tier 2 — High potential with more risk

**US Cities Only**
- 93.5% win rate, 6.4% ROI — best ROI among safe portfolios
- Only 46 trades (small sample) — less statistical confidence
- 123% profit concentrated in Seattle — geographic risk
- **Recommended max trade: 3% of capital**
- **Risk:** Only 3 losses observed. The loss distribution is poorly characterized.

### Tier 3 — Worth monitoring but not yet live

**Tim Birthday Portfolio** — 91% WR, decent returns, but top-10% trades carry all profit
**Night Owl** — More trades and higher returns but 17% max drawdown and Seattle concentration

### What NOT to trade live

- **Any portfolio with "edge" bet types heavily weighted** — these are where all the losses are
- **Exact Band Specialist, High Edge 25+, Edge NO Micro** — all deeply negative ROI
- **Original 10K** — lost money with an 8-trade losing streak and 50% drawdown

### Position Sizing Recommendation

For a live portfolio with real money:

| Capital | Max Single Trade | Max Daily Exposure | Max City Exposure |
|---------|------------------|--------------------|-------------------|
| $500 | $15 (3%) | $75 (15%) | $50 (10%) |
| $1,000 | $30 (3%) | $150 (15%) | $100 (10%) |
| $5,000 | $150 (3%) | $750 (15%) | $500 (10%) |

### Things to Watch

1. **Sample size warning:** 10 days of data is NOT enough to characterize tail risk. The strategies look incredible, but we haven't seen a major weather surprise or Polymarket liquidity crisis yet.

2. **Safe NO hasn't lost yet:** This is both the strongest signal AND the biggest risk. 75 trades at 100% WR is unusual. When the first loss comes (and it will), make sure position sizing can absorb it.

3. **Edge bets are toxic in current calibration:** The forecast model's edge detection on exact bands is systematically wrong, especially for Paris, Tokyo, and Tel Aviv. Either fix the model or avoid edge bets entirely.

4. **Correlated exposure:** Up to 24 trades on the same date — if the forecast model has a systematic error on a given day, ALL of these lose simultaneously. This is the real tail risk.

5. **The "live" portfolios (Balanced NO Live, Tim Birthday Live) are tiny** — $40 starting capital, 17-18 trades. Too small to draw conclusions from, but both are profitable.
