"""
Vercel Python serverless function — returns exit snapshot data from Supabase.

GET /api/exit_snapshots                          — all snapshots
GET /api/exit_snapshots?portfolio_id=xxx         — filter by portfolio
GET /api/exit_snapshots?resolved=true            — only resolved ones
GET /api/exit_snapshots?summary=true             — summary stats by recommendation type
"""
import os
import json
import urllib.request
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from collections import defaultdict

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()


def supabase_query(path):
    """Query Supabase REST API."""
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def build_summary(snapshots):
    """Build summary stats grouped by recommendation type."""
    by_rec = defaultdict(lambda: {
        "total": 0,
        "resolved": 0,
        "hypothetical_profits": [],
        "actual_profits": [],
        "exit_vs_holds": [],
        "hours_to_resolution": [],
        "capital_locked": [],
    })

    for snap in snapshots:
        rec = snap.get("recommendation", "unknown") or "unknown"
        by_rec[rec]["total"] += 1

        # Time and capital metrics (available before resolution)
        htr = snap.get("hours_to_resolution")
        if htr is not None:
            by_rec[rec]["hours_to_resolution"].append(float(htr))
        cl = snap.get("capital_locked")
        if cl is not None:
            by_rec[rec]["capital_locked"].append(float(cl))

        if snap.get("actual_outcome") is not None:
            by_rec[rec]["resolved"] += 1
            hp = float(snap.get("hypothetical_profit", 0) or 0)
            ap = float(snap.get("actual_profit", 0) or 0)
            evh = float(snap.get("exit_vs_hold", 0) or 0)
            by_rec[rec]["hypothetical_profits"].append(hp)
            by_rec[rec]["actual_profits"].append(ap)
            by_rec[rec]["exit_vs_holds"].append(evh)

    result = []
    for rec, data in sorted(by_rec.items()):
        resolved = data["resolved"]
        hps = data["hypothetical_profits"]
        aps = data["actual_profits"]
        evhs = data["exit_vs_holds"]
        htrs = data["hours_to_resolution"]
        cls = data["capital_locked"]
        exit_better = sum(1 for v in evhs if v > 0)
        hold_better = sum(1 for v in evhs if v < 0)
        # Capital-hours: sum of (capital × hours) that would be freed by exiting
        capital_hours_freed = sum(c * h for c, h in zip(cls, htrs)) if cls and htrs and len(cls) == len(htrs) else 0
        result.append({
            "recommendation": rec,
            "total": data["total"],
            "resolved": resolved,
            "avg_hypothetical_profit": round(sum(hps) / len(hps), 4) if hps else 0,
            "avg_actual_profit": round(sum(aps) / len(aps), 4) if aps else 0,
            "avg_exit_vs_hold": round(sum(evhs) / len(evhs), 4) if evhs else 0,
            "exit_better_count": exit_better,
            "hold_better_count": hold_better,
            "exit_better_pct": round(exit_better / resolved * 100, 1) if resolved > 0 else 0,
            # Opportunity cost metrics
            "avg_hours_to_resolution": round(sum(htrs) / len(htrs), 1) if htrs else None,
            "total_capital_locked": round(sum(cls), 2) if cls else 0,
            "total_capital_hours_freed": round(capital_hours_freed, 1),
        })

    return {"by_recommendation": result}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            params = parse_qs(urlparse(self.path).query)
            portfolio_id = params.get("portfolio_id", [None])[0]
            resolved = params.get("resolved", [None])[0]
            summary = params.get("summary", [None])[0]

            # Build query
            filters = []
            if portfolio_id:
                filters.append(f"portfolio_id=eq.{portfolio_id}")
            if resolved == "true":
                filters.append("actual_outcome=not.is.null")

            query = "exit_snapshots?select=*&order=snapshot_time.desc"
            if filters:
                query += "&" + "&".join(filters)

            snapshots = supabase_query(query)

            if summary == "true":
                data = build_summary(snapshots)
            else:
                data = snapshots

            self._respond(200, data)

        except Exception as e:
            self._respond(500, {"error": str(e)})

    def _respond(self, status, data):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def do_OPTIONS(self):
        self._respond(200, {})
