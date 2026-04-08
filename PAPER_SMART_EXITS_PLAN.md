# Paper Smart Exits — Track Hypothetical Exit Outcomes

## The Problem
The Smart Exits page recommends actions (Take Profit, Danger, Exit, Hold) for live portfolio positions. But we don't know if following these recommendations would actually improve returns vs just holding to resolution.

## The Solution
**Snapshot every recommendation** with the current price. When the trade eventually resolves, compare:
- What we **ACTUALLY got** (hold to resolution)
- What we **WOULD have got** (if we'd sold at the recommended time)

This builds a dataset to prove which exit strategy is best before using real money.

---

## What Gets Captured

Every 30 minutes, for each open live trade with a non-"hold" recommendation:

| Field | Description |
|-------|-------------|
| `trade_id` | Which trade |
| `recommendation` | take_profit / danger / exit_forecast_changed / consider_exit |
| `snapshot_time` | When the recommendation was made |
| `exit_price` | Current midpoint (what we'd sell at) |
| `exit_value` | shares × exit_price (what we'd receive) |
| `hypothetical_profit` | exit_value - total_cost (profit if we'd exited) |
| `forecast_gap` | How far forecast is from band threshold |
| `captured_pct` | % of max upside already captured |

**Later, when the trade resolves:**

| Field | Description |
|-------|-------------|
| `actual_outcome` | won / lost |
| `actual_profit` | What we actually made by holding |
| `exit_vs_hold` | hypothetical_profit - actual_profit |

**`exit_vs_hold` is the key metric:**
- **Positive** = exiting would have been BETTER (we left money on the table or avoided a loss)
- **Negative** = holding was BETTER (the trade made more by running to completion)

---

## Implementation Steps

### Step 1: Create database table
New `exit_snapshots` table in Supabase to store the data.

### Step 2: Collect snapshots automatically
Add to the Railway trading loop (every 30 min alongside auto-redeem):
- Run forecast comparison for each live portfolio
- For non-"hold" recommendations, save a snapshot
- Deduplicate: only one snapshot per trade per recommendation type

### Step 3: Backfill on resolution
When the resolver closes a trade:
- Find all snapshots for that trade
- Fill in actual_outcome, actual_profit, exit_vs_hold
- Now we can see if the recommendation was right

### Step 4: UI to review results
Add "Paper Exit History" section on Smart Exits page:
- Table of past recommendations with outcomes
- Summary: "Take Profit recs: X total, Y resolved, avg benefit = +$Z"
- Which recommendation type beats holding most often?

---

## Files to Modify
1. **Supabase** — create `exit_snapshots` table
2. **`railway-trader/trading_loop.py`** — add snapshot collection every 30 min
3. **`paper_trading.py`** — backfill snapshots when trades resolve
4. **`public/exits.html`** — add Paper Exit History section
5. **`api/exit_snapshots.py`** (new) — API to retrieve snapshot data

---

## What We'll Learn

After 1-2 weeks of data collection:

- **"Take Profit at 80% captured" — does it beat holding?** If positions that captured 80%+ of upside rarely gain the last 20%, then exiting is clearly better.
- **"Danger — Close to Band" — is it a real warning?** If positions flagged as Danger lose more often, the warning is valid.
- **"Exit — Thesis Broken" — how bad is it to hold?** If thesis-broken positions lose 80%+ of the time, auto-exit should be implemented.
- **What's the average dollar benefit of each recommendation?** This directly tells us how much money the exit strategy would make/save.

---

## Timeline
- **Day 1:** Deploy snapshot collection (starts recording immediately)
- **Day 2-3:** First trades resolve, backfill starts flowing
- **Week 1:** Enough data to see initial patterns
- **Week 2:** Statistically meaningful results for high-frequency recommendations
