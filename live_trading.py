"""
Live Trading Module for PolyWeather.

Handles execution of live trades via the Railway trader service.
Reuses filtering and capital management logic from paper_trading.py.
"""
import os
import json
import time
import smtplib
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from paper_trading import (
    compute_position_from_book_levels,
    _supabase_request,
    _supabase_get,
    _score_and_sort_opportunities,
)

RAILWAY_URL = os.environ.get("RAILWAY_URL", "").rstrip("/")
RAILWAY_API_SECRET = os.environ.get("RAILWAY_API_SECRET", "")
LIVE_TRADING_ENABLED = os.environ.get("LIVE_TRADING_ENABLED", "false").lower() == "true"
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")

# Polymarket fee rate (1.25% on all trades)
POLYMARKET_FEE_RATE = 0.0125

DASHBOARD_URL = "https://polyweather.vercel.app"


def _send_trade_alert(trade_data, portfolio_name):
    """Send email notification when a live trade is executed."""
    if not RESEND_API_KEY:
        print("[LIVE ALERT] No RESEND_API_KEY set, skipping email")
        return

    try:
        city = trade_data.get("city", "?")
        band_c = trade_data.get("band_c", "?")
        side = trade_data.get("side", "?")
        cost = trade_data.get("total_cost_usd", 0)
        shares = trade_data.get("total_shares", 0)
        entry_price = trade_data.get("entry_price", 0)
        edge = trade_data.get("edge", 0)
        confidence = trade_data.get("confidence", "?")
        bet_type = trade_data.get("bet_type", "?")
        date = trade_data.get("date", "?")
        trade_mode = trade_data.get("trade_mode", "live")

        subject = f"Trade Executed: {city} {band_c} {side} — ${cost:.2f}"

        html = f"""
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #1a1a2e; color: #e0e0e0; padding: 20px;">
  <div style="max-width: 500px; margin: 0 auto; background: #16213e; border-radius: 8px; padding: 24px; border: 1px solid #0f3460;">
    <h2 style="color: #00d2ff; margin-top: 0;">Trade Executed</h2>
    <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
      <tr><td style="padding: 8px 0; color: #999;">City</td><td style="padding: 8px 0; text-align: right; font-weight: 600;">{city}</td></tr>
      <tr><td style="padding: 8px 0; color: #999;">Date</td><td style="padding: 8px 0; text-align: right;">{date}</td></tr>
      <tr><td style="padding: 8px 0; color: #999;">Band</td><td style="padding: 8px 0; text-align: right;">{band_c}</td></tr>
      <tr><td style="padding: 8px 0; color: #999;">Side</td><td style="padding: 8px 0; text-align: right;">{side}</td></tr>
      <tr><td style="padding: 8px 0; color: #999;">Type</td><td style="padding: 8px 0; text-align: right;">{bet_type}</td></tr>
      <tr style="border-top: 1px solid #0f3460;"><td style="padding: 8px 0; color: #999;">Cost</td><td style="padding: 8px 0; text-align: right; font-weight: 600; color: #00d2ff;">${cost:.2f}</td></tr>
      <tr><td style="padding: 8px 0; color: #999;">Shares</td><td style="padding: 8px 0; text-align: right;">{shares:.2f}</td></tr>
      <tr><td style="padding: 8px 0; color: #999;">Entry Price</td><td style="padding: 8px 0; text-align: right;">{entry_price:.4f}</td></tr>
      <tr><td style="padding: 8px 0; color: #999;">Edge</td><td style="padding: 8px 0; text-align: right;">{edge}</td></tr>
      <tr><td style="padding: 8px 0; color: #999;">Confidence</td><td style="padding: 8px 0; text-align: right;">{confidence}</td></tr>
    </table>
    <div style="margin-top: 16px; padding-top: 16px; border-top: 1px solid #0f3460; font-size: 13px; color: #999;">
      <strong>Portfolio:</strong> {portfolio_name} &nbsp;|&nbsp; <strong>Mode:</strong> {trade_mode}
    </div>
    <div style="margin-top: 16px;">
      <a href="{DASHBOARD_URL}" style="color: #00d2ff; text-decoration: none;">View Dashboard &rarr;</a>
    </div>
  </div>
</body>
</html>
"""

        msg = MIMEMultipart("alternative")
        msg["From"] = "PolyWeather Alerts <onboarding@resend.dev>"
        msg["To"] = "tim@theboost.ai"
        msg["Subject"] = subject
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP("smtp.resend.com", 587) as server:
            server.starttls()
            server.login("resend", RESEND_API_KEY)
            server.sendmail("onboarding@resend.dev", "tim@theboost.ai", msg.as_string())

        print(f"[LIVE ALERT] Email sent: {subject}")
    except Exception as e:
        print(f"[LIVE ALERT] Failed to send email: {e}")


def _log_execution(supabase_url, headers, trade_id=None, portfolio_id=None,
                   action="", request_payload=None, response_payload=None,
                   error_message=None, duration_ms=None):
    """Write an entry to the execution_log table."""
    try:
        row = {
            "action": action,
            "request_payload": request_payload,
            "response_payload": response_payload,
            "error_message": error_message,
            "duration_ms": duration_ms,
        }
        if trade_id:
            row["trade_id"] = trade_id
        if portfolio_id:
            row["portfolio_id"] = portfolio_id

        url = f"{supabase_url}/rest/v1/execution_log"
        _supabase_request(url, [row], headers)
    except Exception as log_err:
        print(f"[LIVE LOG] Failed to write log: {log_err}")


def _check_trading_hours(strategy):
    """Check if current UTC time is within allowed trading hours.

    Strategy can include:
        "trading_hours": {
            "enabled": true,
            "timezone": "UTC",
            "allowed_windows": [{"start": "06:00", "end": "22:00"}],
            "blackout_windows": [{"start": "14:00", "end": "14:30"}]
        }

    Logic:
        1. If trading_hours not present or not enabled, allow all times.
        2. If allowed_windows is set, current time must be in at least one window.
        3. If blackout_windows is set, current time must NOT be in any blackout window.
        Blackout takes priority over allowed.

    Returns:
        (allowed: bool, reason: str)
    """
    trading_hours = strategy.get("trading_hours")
    if not trading_hours or not trading_hours.get("enabled", False):
        return True, "no restrictions"

    now = datetime.now(timezone.utc)
    current_minutes = now.hour * 60 + now.minute
    current_time_str = now.strftime("%H:%M")

    def _parse_time(t):
        """Parse 'HH:MM' to minutes since midnight."""
        parts = t.strip().split(":")
        return int(parts[0]) * 60 + int(parts[1])

    def _in_window(start_str, end_str):
        """Check if current time is in [start, end). Handles overnight spans."""
        start = _parse_time(start_str)
        end = _parse_time(end_str)
        if start <= end:
            return start <= current_minutes < end
        else:
            # Overnight: e.g. 22:00 - 06:00
            return current_minutes >= start or current_minutes < end

    # Check blackout first (takes priority)
    blackout_windows = trading_hours.get("blackout_windows", [])
    for bw in blackout_windows:
        if _in_window(bw.get("start", "00:00"), bw.get("end", "00:00")):
            return False, f"blackout window {bw['start']}-{bw['end']} (now={current_time_str} UTC)"

    # Check allowed windows
    allowed_windows = trading_hours.get("allowed_windows", [])
    if allowed_windows:
        for aw in allowed_windows:
            if _in_window(aw.get("start", "00:00"), aw.get("end", "23:59")):
                return True, f"in allowed window {aw['start']}-{aw['end']} (now={current_time_str} UTC)"
        return False, f"outside all allowed windows (now={current_time_str} UTC)"

    return True, "no restrictions"


def execute_live_trades(opps, scan_id, supabase_url, supabase_service_key,
                        portfolio_id=None, portfolio=None):
    """Execute live trades for scanner opportunities.

    Same filtering and capital management as paper_trading.open_paper_trades(),
    but sends orders to Railway for actual execution on Polymarket.
    """
    if not LIVE_TRADING_ENABLED:
        print(f"[LIVE] Skipping — LIVE_TRADING_ENABLED is false")
        return

    if not RAILWAY_URL or not RAILWAY_API_SECRET:
        print(f"[LIVE] Skipping — RAILWAY_URL or RAILWAY_API_SECRET not set")
        return

    headers = {
        "apikey": supabase_service_key,
        "Authorization": f"Bearer {supabase_service_key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }

    pf_name = (portfolio or {}).get("name", str(portfolio_id))
    strategy = (portfolio or {}).get("strategy", {})

    # --- Check trading hours ---
    hours_ok, hours_reason = _check_trading_hours(strategy)
    if not hours_ok:
        msg = f"[LIVE] Skipping {pf_name} — {hours_reason}"
        print(msg)
        _log_execution(supabase_url, headers, portfolio_id=portfolio_id,
                       action="trading_hours_blocked",
                       response_payload={"reason": hours_reason, "opps_count": len(opps)})
        return

    _log_execution(supabase_url, headers, portfolio_id=portfolio_id,
                   action="scan_start",
                   request_payload={"scan_id": scan_id, "opps_count": len(opps),
                                    "trading_hours": hours_reason})

    # --- Capital management (same as paper_trading) ---
    use_capital_mgmt = (
        portfolio is not None
        and not portfolio.get("unlimited_capital", True)
        and portfolio.get("starting_capital_usd") is not None
    )

    if use_capital_mgmt:
        starting_capital = float(portfolio.get("starting_capital_usd", 0))
        cap_mgmt = strategy.get("capital_management", {})
        max_single_trade_usd = float(cap_mgmt.get("max_single_trade_usd", 999999))
        max_single_trade_pct = float(cap_mgmt.get("max_single_trade_pct", 100))
        max_portfolio_util_pct = float(cap_mgmt.get("max_portfolio_utilization_pct", 100))
        max_corr_exposure_pct = float(cap_mgmt.get("max_correlated_exposure_pct", 100))

        deployed_url = (
            f"{supabase_url}/rest/v1/paper_trades"
            f"?status=eq.open&portfolio_id=eq.{portfolio_id}&select=total_cost_usd,city"
        )
        open_trades = _supabase_get(deployed_url, headers)
        deployed = sum(float(t.get("total_cost_usd", 0) or 0) for t in open_trades)

        city_exposure = {}
        for t in open_trades:
            c = t.get("city", "")
            city_exposure[c] = city_exposure.get(c, 0.0) + float(t.get("total_cost_usd", 0) or 0)

        pnl_url = (
            f"{supabase_url}/rest/v1/paper_trades"
            f"?status=in.(won,lost)&portfolio_id=eq.{portfolio_id}&select=profit_usd"
        )
        resolved_trades = _supabase_get(pnl_url, headers)
        realized_pnl = sum(float(t.get("profit_usd", 0) or 0) for t in resolved_trades)

        current_capital = starting_capital + realized_pnl
        available = current_capital - deployed

        capital_info = {
            "starting": starting_capital, "realized_pnl": realized_pnl,
            "current": current_capital, "deployed": deployed, "available": available,
        }
        print(f"[LIVE CAPITAL] starting=${starting_capital:.2f} current=${current_capital:.2f} "
              f"deployed=${deployed:.2f} available=${available:.2f}")

        _log_execution(supabase_url, headers, portfolio_id=portfolio_id,
                       action="capital_snapshot", response_payload=capital_info)

        opps = _score_and_sort_opportunities(opps, strategy)

    # --- Strategy filters (same as paper_trading) ---
    allowed_sides = strategy.get("allowed_sides")
    allowed_bet_types = strategy.get("allowed_bet_types")
    allowed_band_types = strategy.get("allowed_band_types")
    blocked_cities = strategy.get("blocked_cities", [])
    allowed_cities = strategy.get("allowed_cities", [])

    count = 0
    skipped = {"filter": 0, "liquidity": 0, "capital": 0, "duplicate": 0, "error": 0}

    for opp in opps:
        opp_label = f"{opp.get('city','?')}/{opp.get('band_c','?')}/{opp.get('side','?')}"
        try:
            # Strategy filters
            opp_side = opp.get("side", "")
            opp_bet_type = opp.get("bet_type", "")
            opp_band_type = opp.get("band_type", "")
            opp_city = opp.get("city", "")

            if allowed_sides and opp_side not in allowed_sides:
                skipped["filter"] += 1
                continue
            if allowed_bet_types and opp_bet_type not in allowed_bet_types:
                skipped["filter"] += 1
                continue
            if allowed_band_types and opp_band_type not in allowed_band_types:
                skipped["filter"] += 1
                continue
            if blocked_cities and opp_city in blocked_cities:
                skipped["filter"] += 1
                continue
            if allowed_cities and opp_city not in allowed_cities:
                skipped["filter"] += 1
                continue

            # Entry price filter
            min_entry = strategy.get("preferred_entry_price_min")
            if min_entry is not None:
                opp_entry = opp.get("entry_price") or (opp.get("mkt_p", 0) / 100 if opp.get("mkt_p") else None)
                if opp_entry and float(opp_entry) < float(min_entry):
                    skipped["filter"] += 1
                    continue

            liquidity = opp.get("liquidity")
            if not liquidity:
                skipped["liquidity"] += 1
                continue

            position = compute_position_from_book_levels(liquidity)
            if position is None:
                skipped["liquidity"] += 1
                continue
            if position["total_cost_usd"] < 5.0:
                skipped["liquidity"] += 1
                continue

            # Capital checks
            cost = position["total_cost_usd"]

            # GTC limit orders are maker orders = 0% fee on Polymarket
            # No fee buffer needed in capital calculations

            if use_capital_mgmt:
                max_by_pct = current_capital * max_single_trade_pct / 100
                capped_cost = min(cost, max_single_trade_usd, max_by_pct)
                if capped_cost < cost:
                    scale = capped_cost / cost if cost > 0 else 1
                    position["total_cost_usd"] = round(capped_cost, 2)
                    position["total_shares"] = round(position["total_shares"] * scale, 2)
                    cost = capped_cost

                max_deployed = current_capital * max_portfolio_util_pct / 100
                if deployed + cost > max_deployed:
                    print(f"[LIVE] SKIP {opp_label}: utilization limit")
                    skipped["capital"] += 1
                    continue

                city_exp = city_exposure.get(opp_city, 0.0)
                max_city = current_capital * max_corr_exposure_pct / 100
                if city_exp + cost > max_city:
                    print(f"[LIVE] SKIP {opp_label}: city exposure limit")
                    skipped["capital"] += 1
                    continue

                deployed += cost
                city_exposure[opp_city] = city_exp + cost

            # Polymarket minimums: 5 shares AND $1 marketable order
            if position["total_shares"] < 5.0:
                skipped["liquidity"] += 1
                continue
            if position["total_cost_usd"] < 1.10:
                skipped["liquidity"] += 1
                continue

            # Get token_id for the correct side
            token_id = opp.get("token_id", "")
            if not token_id:
                print(f"[LIVE] SKIP {opp_label}: no token_id")
                skipped["error"] += 1
                continue

            # 1. Write trade to Supabase with pending_execution status
            trade_row = {
                "city": opp_city,
                "date": opp.get("date", ""),
                "band_c": opp.get("band_c", ""),
                "band_f": opp.get("band_f", ""),
                "band_type": opp_band_type,
                "side": opp_side,
                "bet_type": opp_bet_type,
                "entry_price": position["entry_price"],
                "total_cost_usd": position["total_cost_usd"],
                "total_shares": position["total_shares"],
                "num_levels": position["num_levels"],
                "my_p": opp.get("my_p"),
                "mkt_p": opp.get("mkt_p"),
                "edge": opp.get("edge"),
                "confidence": opp.get("confidence"),
                "ev_per_dollar": opp.get("ev_per_dollar"),
                "half_kelly": opp.get("hk"),
                "forecast_c": opp.get("forecast_c"),
                "risk": opp.get("risk"),
                "token_id": token_id,
                "condition_id": opp.get("condition_id"),
                "event_slug": opp.get("event_slug"),
                "market_slug": opp.get("market_slug"),
                "url": opp.get("url"),
                "liquidity": liquidity,
                "status": "pending_execution",
                "trade_mode": "live",
                "portfolio_id": portfolio_id,
            }

            try:
                insert_url = f"{supabase_url}/rest/v1/paper_trades"
                result = _supabase_request(insert_url, [trade_row], headers)
                trade_id = result[0].get("id") if result else None
            except urllib.error.HTTPError as e:
                if e.code == 409:
                    print(f"[LIVE] Trade already exists: {opp_label}")
                    skipped["duplicate"] += 1
                    continue
                raise

            if not trade_id:
                print(f"[LIVE] Failed to insert trade record")
                skipped["error"] += 1
                continue

            _log_execution(supabase_url, headers, trade_id=trade_id, portfolio_id=portfolio_id,
                           action="trade_inserted",
                           request_payload={
                               "opp": opp_label, "cost": cost, "shares": position["total_shares"],
                               "price": position["entry_price"], "edge": opp.get("edge"),
                               "bet_type": opp_bet_type, "token_id": token_id[:20] + "...",
                           })

            # 2. Call Railway to execute
            t_exec = time.time()
            try:
                railway_payload = {
                    "trade_id": trade_id,
                    "token_id": token_id,
                    "side": "BUY",  # We always BUY the side we want
                    "price": position["entry_price"],
                    "size": position["total_shares"],
                    "order_type": "GTC",
                    "portfolio_id": portfolio_id,
                }

                railway_req = urllib.request.Request(
                    f"{RAILWAY_URL}/execute",
                    data=json.dumps(railway_payload).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {RAILWAY_API_SECRET}",
                    },
                    method="POST",
                )

                with urllib.request.urlopen(railway_req, timeout=30) as resp:
                    exec_result = json.loads(resp.read().decode("utf-8"))

                exec_ms = int((time.time() - t_exec) * 1000)

                if exec_result.get("success"):
                    # Use actual on-chain cost if available, else estimated
                    actual_cost = exec_result.get("actual_cost_usd")
                    estimated_cost = exec_result.get("estimated_cost_usd", cost)
                    final_cost = actual_cost if actual_cost is not None else estimated_cost

                    fill_data = exec_result.get("fill_data", {})
                    size_matched = fill_data.get("size_matched", "0") if fill_data else "0"

                    print(f"[LIVE] Executed: {opp_label} "
                          f"actual_cost=${actual_cost} estimated=${estimated_cost} "
                          f"matched={size_matched} ({exec_ms}ms)")
                    count += 1

                    # Update trade with actual execution data
                    trade_update = {
                        "status": "open",
                        "execution_details": {
                            "order_id": exec_result.get("order_id"),
                            "order_status": exec_result.get("status"),
                            "estimated_cost_usd": estimated_cost,
                            "actual_cost_usd": actual_cost,
                            "usdc_before": exec_result.get("usdc_before"),
                            "usdc_after": exec_result.get("usdc_after"),
                            "fill_data": fill_data,
                            "executed_at": datetime.now(timezone.utc).isoformat(),
                        },
                    }
                    # Override cost/shares with actuals if we have them
                    if actual_cost is not None and actual_cost > 0:
                        trade_update["total_cost_usd"] = round(actual_cost, 4)
                    if fill_data and fill_data.get("size_matched") and float(fill_data["size_matched"]) > 0:
                        trade_update["total_shares"] = round(float(fill_data["size_matched"]), 4)
                        if actual_cost and float(fill_data["size_matched"]) > 0:
                            trade_update["entry_price"] = round(actual_cost / float(fill_data["size_matched"]), 6)

                    open_url = f"{supabase_url}/rest/v1/paper_trades?id=eq.{trade_id}"
                    _supabase_request(open_url, trade_update, headers, method="PATCH")

                    _log_execution(supabase_url, headers, trade_id=trade_id, portfolio_id=portfolio_id,
                                   action="trade_executed",
                                   request_payload=railway_payload,
                                   response_payload=exec_result,
                                   duration_ms=exec_ms)

                    # Send email notification
                    try:
                        _send_trade_alert(trade_row, pf_name)
                    except Exception:
                        pass  # Don't fail trade on notification error
                else:
                    error = exec_result.get("error", "unknown")
                    print(f"[LIVE] Execution failed: {opp_label}: {error}")
                    # Delete the pending trade — don't leave void records
                    try:
                        del_url = f"{supabase_url}/rest/v1/paper_trades?id=eq.{trade_id}"
                        del_req = urllib.request.Request(del_url, headers=headers, method="DELETE")
                        urllib.request.urlopen(del_req, timeout=10)
                    except Exception:
                        pass

                    _log_execution(supabase_url, headers, trade_id=None, portfolio_id=portfolio_id,
                                   action="trade_failed",
                                   request_payload={**railway_payload, "trade_id": trade_id},
                                   response_payload=exec_result,
                                   error_message=error, duration_ms=exec_ms)

            except Exception as railway_err:
                exec_ms = int((time.time() - t_exec) * 1000)
                print(f"[LIVE] Railway call failed: {railway_err}")
                _log_execution(supabase_url, headers, trade_id=trade_id, portfolio_id=portfolio_id,
                               action="railway_error",
                               request_payload=railway_payload,
                               error_message=str(railway_err), duration_ms=exec_ms)

        except Exception as e:
            print(f"[LIVE] Error processing {opp_label}: {e}")
            skipped["error"] += 1
            _log_execution(supabase_url, headers, portfolio_id=portfolio_id,
                           action="opp_error",
                           request_payload={"opp": opp_label},
                           error_message=str(e))
            continue

    summary = {
        "executed": count, "total_opps": len(opps), "skipped": skipped,
    }
    print(f"[LIVE] Done: {count} executed, {skipped} skipped — portfolio {pf_name}")
    _log_execution(supabase_url, headers, portfolio_id=portfolio_id,
                   action="scan_complete", response_payload=summary)
