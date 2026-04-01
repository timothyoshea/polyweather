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


def save_analysis(question, analysis, trade_count, date_range=None):
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
        data = json.dumps([row]).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"[WARN] Failed to save analysis: {e}")


def get_analysis_history(limit=20):
    """Fetch past AI analyses from Supabase."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return []
    try:
        url = (
            f"{SUPABASE_URL}/rest/v1/ai_analyses"
            f"?select=*&order=created_at.desc&limit={limit}"
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


def call_claude(trades_json, user_question=None):
    """Send trade data to Claude API for analysis."""
    system_prompt = """You are an expert quantitative trading analyst reviewing paper trading results
from a weather prediction market (Polymarket). Analyze the trade data provided and give actionable insights.

Focus on:
1. Which patterns (city, bet type, side, edge range, confidence, trade size) are most profitable
2. Which patterns are losing money and should be avoided
3. Specific recommendations to improve the trading strategy
4. Any surprising correlations or edge cases
5. Risk assessment and position sizing observations

Be specific with numbers. Reference actual cities, bet types, and metrics. Keep it concise but thorough.
Format your response in markdown with clear sections."""

    user_content = f"Here are my paper trading results ({len(trades_json)} trades):\n\n```json\n{json.dumps(trades_json, indent=2)}\n```"
    if user_question:
        user_content += f"\n\nSpecific question: {user_question}"
    else:
        user_content += "\n\nPlease analyze these results and identify the most important patterns, what's working, what's not, and what I should change."

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
    def do_POST(self):
        try:
            if not ANTHROPIC_API_KEY:
                self._respond(500, {"error": "ANTHROPIC_API_KEY env var not set"})
                return

            content_length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_length).decode("utf-8"))

            trades = body.get("trades", [])
            question = body.get("question", None)

            if not trades:
                self._respond(400, {"error": "No trade data provided"})
                return

            # Slim down trade data to reduce token usage
            slim_trades = []
            for t in trades:
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

            analysis = call_claude(slim_trades, question)
            self._respond(200, {"analysis": analysis})

        except Exception as e:
            self._respond(500, {"error": str(e)})

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _respond(self, status_code, data):
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())
