import os
import time
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from prometheus_client import Gauge, generate_latest, CONTENT_TYPE_LATEST

app = FastAPI()

API_URL = os.getenv("API_URL", "http://api:8080")

# МЕТРИКИ
GAUGE_NEW = Gauge('shop_stats_orders_new', 'Orders with status NEW')
GAUGE_PROCESSING = Gauge('shop_stats_orders_processing', 'Orders with status PROCESSING')
GAUGE_DONE = Gauge('shop_stats_orders_done', 'Orders with status DONE')
GAUGE_SINCE_LAST = Gauge('shop_stats_seconds_since_last_processed', 'Seconds since last order processed')

@app.get("/metrics")
def metrics():
    try:
        resp = httpx.get(f"{API_URL}/internal/stats", timeout=5.0)
        data = resp.json()

        GAUGE_NEW.set(data.get("orders_new", 0))
        GAUGE_PROCESSING.set(data.get("orders_processing", 0))
        GAUGE_DONE.set(data.get("orders_done", 0))

        last_processed = data.get("last_processed_at")
        if last_processed:
            dt = datetime.fromisoformat(last_processed.replace("Z", "+00:00"))
            seconds_since = (datetime.now(timezone.utc) - dt).total_seconds()
            GAUGE_SINCE_LAST.set(max(0, seconds_since))
        else:
            GAUGE_SINCE_LAST.set(0)
    except Exception as e:
        print(f"[Exporter] Error fetching stats: {e}")

    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)