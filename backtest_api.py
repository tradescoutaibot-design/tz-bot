"""
backtest_api.py — lightweight HTTP server that serves backtest results
to the dashboard. Run alongside main.py on Railway.

Endpoint: GET /backtest?strategy=connors&ticker=SPY&start=2022-01-01&end=2024-12-31&capital=1000
Endpoint: GET /compare?ticker=SPY&start=2022-01-01&end=2024-12-31&capital=1000
Endpoint: GET /health
"""
import json
import asyncio
from aiohttp import web
from core.backtester import run_backtest, run_comparison
from core.logger import log


async def handle_backtest(request):
    try:
        p        = request.rel_url.query
        strategy = p.get("strategy", "connors")
        ticker   = p.get("ticker", "SPY").upper()
        start    = p.get("start", "2022-01-01")
        end      = p.get("end", "2024-12-31")
        capital  = float(p.get("capital", 1000))

        # Parse optional params
        params = {}
        for key in ["rsi_threshold","trend_period","exit_rsi","stop_loss_pct",
                    "take_profit_pct","position_pct","fast","slow"]:
            if key in p:
                params[key] = float(p[key])

        result = run_backtest(strategy, ticker, start, end, capital, params)
        return web.Response(
            text=json.dumps(result, default=str),
            content_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"}
        )
    except Exception as e:
        log.error(f"Backtest API error: {e}")
        return web.Response(
            text=json.dumps({"error": str(e)}),
            content_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
            status=500
        )


async def handle_compare(request):
    try:
        p       = request.rel_url.query
        ticker  = p.get("ticker", "SPY").upper()
        start   = p.get("start", "2022-01-01")
        end     = p.get("end", "2024-12-31")
        capital = float(p.get("capital", 1000))
        result  = run_comparison(ticker, start, end, capital)
        return web.Response(
            text=json.dumps(result, default=str),
            content_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"}
        )
    except Exception as e:
        return web.Response(
            text=json.dumps({"error": str(e)}),
            content_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
            status=500
        )


async def handle_health(request):
    return web.Response(text=json.dumps({"status": "ok"}),
                        content_type="application/json",
                        headers={"Access-Control-Allow-Origin": "*"})


app = web.Application()
app.router.add_get("/backtest", handle_backtest)
app.router.add_get("/compare",  handle_compare)
app.router.add_get("/health",   handle_health)

if __name__ == "__main__":
    import os
    port = int(os.environ.get("BACKTEST_PORT", 8080))
    log.info(f"Backtest API running on port {port}")
    web.run_app(app, port=port)
