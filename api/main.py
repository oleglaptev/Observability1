# Observability1/api/main.py
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from datetime import datetime, timezone
import json
import os
import time
import uuid

import pika

from api.database import engine, get_db, Base
from api.models import Order
from common.jsonlog import init as init_logging, log_event

init_logging("api")

# Создаём таблицы
Base.metadata.create_all(bind=engine)

app = FastAPI()

QUEUE_NAME = "orders_queue"

# ─── МЕТРИКИ (без изменений из лабы №1) ───
REQUEST_COUNT = Counter(
    'http_requests_total',
    'Total HTTP requests',
    ['method', 'route', 'status']
)
REQUEST_DURATION = Histogram(
    'http_request_duration_seconds',
    'HTTP request duration in seconds',
    ['method', 'route']
)
ORDERS_CREATED = Counter(
    'shop_orders_created_total',
    'Total orders created'
)


# ─── Pydantic-схемы (без изменений) ───
class OrderCreate(BaseModel):
    amount: float
    items_count: int


class OrderResponse(BaseModel):
    id: int
    amount: float
    items_count: int
    status: str
    created_at: str | None = None
    processed_at: str | None = None

    class Config:
        from_attributes = True


# ─── Middleware: request_id + метрики + логи request_started/request_finished ───
@app.middleware("http")
async def observability_middleware(request: Request, call_next):
    # Берём X-Request-ID от клиента или генерируем новый
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id

    method = request.method
    route = request.url.path
    start = time.perf_counter()

    log_event("request_started", request_id=request_id, method=method, route=route)

    try:
        response = await call_next(request)
    except Exception as exc:
        duration = time.perf_counter() - start
        REQUEST_COUNT.labels(method=method, route=route, status=500).inc()
        REQUEST_DURATION.labels(method=method, route=route).observe(duration)
        log_event("request_finished", level="ERROR", request_id=request_id,
                  method=method, route=route, status=500,
                  duration_ms=round(duration * 1000, 2), error=str(exc))
        raise

    duration = time.perf_counter() - start
    REQUEST_COUNT.labels(method=method, route=route, status=response.status_code).inc()
    REQUEST_DURATION.labels(method=method, route=route).observe(duration)

    # Возвращаем request_id клиенту — удобно для тестирования
    response.headers["X-Request-ID"] = request_id

    log_event("request_finished", request_id=request_id, method=method, route=route,
              status=response.status_code, duration_ms=round(duration * 1000, 2))
    return response


# ─── Пользовательские ручки ───
@app.post("/orders", response_model=OrderResponse)
def create_order(order: OrderCreate, request: Request, db: Session = Depends(get_db)):
    request_id = request.state.request_id

    new_order = Order(
        amount=order.amount,
        items_count=order.items_count,
        status="NEW",
        created_at=datetime.now(timezone.utc)
    )
    db.add(new_order)
    db.commit()
    db.refresh(new_order)

    log_event("order_created", request_id=request_id, order_id=new_order.id,
              amount=new_order.amount, items_count=new_order.items_count)

    # Отправляем в RabbitMQ: order_id + request_id одним JSON-сообщением
    rabbitmq_host = os.getenv("RABBITMQ_HOST", "rabbitmq")
    try:
        connection = pika.BlockingConnection(
            pika.ConnectionParameters(host=rabbitmq_host)
        )
        channel = connection.channel()
        channel.queue_declare(queue=QUEUE_NAME, durable=True)
        message = json.dumps({"order_id": new_order.id, "request_id": request_id})
        channel.basic_publish(
            exchange='',
            routing_key=QUEUE_NAME,
            body=message,
            properties=pika.BasicProperties(delivery_mode=2, content_type="application/json")
        )
        connection.close()
    except Exception as e:
        log_event("message_send_failed", level="ERROR", request_id=request_id,
                  order_id=new_order.id, queue=QUEUE_NAME, error=str(e))
        raise HTTPException(status_code=500, detail=f"RabbitMQ error: {e}")

    log_event("message_sent", request_id=request_id, order_id=new_order.id, queue=QUEUE_NAME)
    ORDERS_CREATED.inc()

    return OrderResponse(
        id=new_order.id,
        amount=new_order.amount,
        items_count=new_order.items_count,
        status=new_order.status,
        created_at=new_order.created_at.isoformat() if new_order.created_at else None,
        processed_at=new_order.processed_at.isoformat() if new_order.processed_at else None
    )


@app.get("/orders/{order_id}", response_model=OrderResponse)
def get_order(order_id: int, db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return OrderResponse(
        id=order.id, amount=order.amount, items_count=order.items_count,
        status=order.status,
        created_at=order.created_at.isoformat() if order.created_at else None,
        processed_at=order.processed_at.isoformat() if order.processed_at else None
    )


@app.get("/orders")
def list_orders(db: Session = Depends(get_db)):
    orders = db.query(Order).all()
    return [
        {
            "id": o.id, "amount": o.amount, "items_count": o.items_count,
            "status": o.status,
            "created_at": o.created_at.isoformat() if o.created_at else None,
            "processed_at": o.processed_at.isoformat() if o.processed_at else None
        }
        for o in orders
    ]


@app.get("/health")
def health():
    return {"status": "ok"}


# ─── Технические ручки (без изменений) ───
@app.get("/metrics")
def metrics():
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/internal/stats")
def internal_stats(db: Session = Depends(get_db)):
    orders_new = db.query(func.count(Order.id)).filter(Order.status == "NEW").scalar()
    orders_processing = db.query(func.count(Order.id)).filter(Order.status == "PROCESSING").scalar()
    orders_done = db.query(func.count(Order.id)).filter(Order.status == "DONE").scalar()
    last_processed = db.query(func.max(Order.processed_at)).filter(Order.status == "DONE").scalar()
    return {
        "orders_new": orders_new or 0,
        "orders_processing": orders_processing or 0,
        "orders_done": orders_done or 0,
        "last_processed_at": last_processed.isoformat() if last_processed else None
    }