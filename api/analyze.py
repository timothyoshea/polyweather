"""
Vercel Python serverless function — AI-powered trade analysis using Claude API.

POST /api/analyze — sends trade data to Claude for pattern analysis
GET  /api/analyze — returns history of past analyses
"""
import os
import json
import urllib.request
import urllib.error
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()


def save_analysis(question, analysis, trade_count, date_range=None, portfolio_id=None):
    """Save an AI analysis to Supabase for history."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return
    try:
        url = f"{SUPABASE_URL}/rest/v1/ai_analyses"
        headers = {
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }
        row = {
            "question": question,
            "analysis": analysis,
            "trade_count": trade_count,
            "date_range": date_range,
            "created_at": datetime.utcnow().isoformat() + "Z",
        }
        if portfolio_id:
            row["portfolio_id"] = portfolio_id
        data = json.dumps([row]).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"[WARN] Failed to save analysis: {e}")


def get_analysis_history(limit=20, portfolio_id=None):
    """Fetch past AI analyses from Supabase."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return []
    try:
        pf_filter = f"&portfolio_id=eq.{portfolio_id}" if portfolio_id else ""
        url = (
            f"{SUPABASE_URL}/rest/v1/ai_analyses"
            f"?select=*&order=created_at.desc&limit={limit}{pf_filter}"
        )
        headers = {
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        }
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[WARN] Failed to fetch analysis history: {e}")
        return []


def call_claude(trades_json, user_question=None, breakdowns=None, date_range=None):
    """Send trade data to Claude API for analysis."""
    system_prompt = """You are an expert quantitative trading analyst reviewing paper trading results from a weather prediction market (Polymarket). These are weather temperature bets — the system forecasts temperatures using weather models and bets when it finds edges against market prices.

Your job is to provide CLEAR, ACTIONABLE analysis. Structure your response with these sections:

## DO — What to Keep Doing
Specific strategies, cities, bet types, and conditions that are working. Back everything with numbers.

## DON'T — What to Stop Doing
Specific strategies, cities, bet types, and conditions that are losing money. Be blunt about what to cut.

## Key Insights
Surprising patterns, correlations, or edge cases found in the data. Multi-dimensional patterns matter most (e.g., "safe_no + NO side + 80¢+ entry = 100% win rate").

## Position Sizing
Are trades too large/small? Which categories deserve bigger/smaller positions? What's the risk profile?

## Recommendations
Ranked list of the top 3-5 concrete changes to make, with expected impact.

## Statistical Significance
For every pattern you highlight, assess its **binomial significance**. Use this method:
- Null hypothesis: the win rate equals the overall portfolio win rate (baseline)
- For a group with k wins out of n trades, compute the p-value using binomial test against the baseline rate
- Report significance as: p < 0.01 (highly significant), p < 0.05 (significant), p < 0.10 (marginally significant), or "not significant"
- Only recommend acting on patterns that are at least marginally significant (p < 0.10)
- Flag any pattern you mention that is NOT statistically significant — say "Note: small sample, not statistically significant"

Rules:
- Only analyze CLOSED trades (won/lost). Never include open trades.
- Be specific with numbers — cite actual cities, bet types, win rates, profits, ROI.
- Use markdown tables when comparing categories. Include a "Sig." column showing p-value significance level.
- Keep it concise but thorough. No fluff.
- Bold the most important numbers and conclusions."""

    period_note = f" (time period: {date_range})" if date_range else ""
    user_content = f"Here are my paper trading results — {len(trades_json)} closed trades{period_note}:\n\n"

    # Add breakdowns summary first (more useful than raw trades)
    if breakdowns:
        user_content += "## Performance Breakdowns\n\n"
        for label, data in breakdowns.items():
            nice_label = label.replace("_", " ").replace("by ", "By ").replace("cross ", "Cross: ").title()
            user_content += f"### {nice_label}\n"
            if isinstance(data, list) and data:
                user_content += "| Group | Count | Won | Lost | Win% | Invested | Profit | ROI |\n"
                user_content += "|-------|-------|-----|------|------|----------|--------|-----|\n"
                for row in data:
                    user_content += f"| {row.get('group','')} | {row.get('count',0)} | {row.get('won',0)} | {row.get('lost',0)} | {row.get('win_rate',0)}% | ${row.get('invested',0)} | ${row.get('profit',0)} | {row.get('roi',0)}% |\n"
            user_content += "\n"

    # Add raw trades
    user_content += f"## Raw Trade Data ({len(trades_json)} trades)\n\n```json\n{json.dumps(trades_json, indent=1)}\n```"

    if user_question:
        user_content += f"\n\n## Specific Question\n{user_question}"
    else:
        user_content += "\n\nAnalyze these results. Focus on what's working vs what's not, and give me clear DO and DON'T instructions."

    body = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 4096,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_content}],
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        # Extract text from content blocks
        text_parts = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
        return "\n".join(text_parts)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Return history of past AI analyses."""
        try:
            params = parse_qs(urlparse(self.path).query)
            limit = int(params.get("limit", ["20"])[0])
            portfolio_id = params.get("portfolio_id", [None])[0]
            history = get_analysis_history(limit, portfolio_id)
            self._respond(200, history)
        except Exception as e:
            self._respond(500, {"error": str(e)})

    def do_POST(self):
        try:
            if not ANTHROPIC_API_KEY:
                self._respond(500, {"error": "ANTHROPIC_API_KEY env var not set"})
                return

            content_length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_length).decode("utf-8"))

            trades = body.get("trades", [])
            question = body.get("question", None)
            date_range = body.get("date_range", None)
            breakdowns = body.get("breakdowns", None)

            if not trades:
                self._respond(400, {"error": "No trade data provided"})
                return

            # Only include closed trades
            closed_trades = [t for t in trades if t.get("status") in ("won", "lost")]
            if not closed_trades:
                self._respond(400, {"error": "No closed trades to analyze"})
                return

            # Slim down trade data to reduce token usage
            slim_trades = []
            for t in closed_trades:
                slim_trades.append({
                    "city": t.get("city"),
                    "date": t.get("date"),
                    "band_c": t.get("band_c"),
                    "band_type": t.get("band_type"),
                    "side": t.get("side"),
                    "bet_type": t.get("bet_type"),
                    "status": t.get("status"),
                    "entry_price": t.get("entry_price"),
                    "total_cost_usd": t.get("total_cost_usd"),
                    "total_shares": t.get("total_shares"),
                    "my_p": t.get("my_p"),
                    "mkt_p": t.get("mkt_p"),
                    "edge": t.get("edge"),
                    "confidence": t.get("confidence"),
                    "risk": t.get("risk"),
                    "profit_usd": t.get("profit_usd"),
                    "roi_pct": t.get("roi_pct"),
                    "actual_temp_c": t.get("actual_temp_c"),
                    "forecast_c": t.get("forecast_c"),
                })

            portfolio_id = body.get("portfolio_id", None)

            analysis = call_claude(slim_trades, question, breakdowns, date_range)

            # Save to history
            save_analysis(
                question=question or "Full analysis",
                analysis=analysis,
                trade_count=len(slim_trades),
                date_range=date_range,
                portfolio_id=portfolio_id,
            )

            self._respond(200, {"analysis": analysis})

        except Exception as e:
            self._respond(500, {"error": str(e)})

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _respond(self, status_code, data):
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())
