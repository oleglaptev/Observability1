from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from datetime import datetime, timezone
import pika
import os
import time

from api.database import engine, get_db, Base
from api.models import Order

# Создаём таблицы
Base.metadata.create_all(bind=engine)

app = FastAPI()

# МЕТРИКИ

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

# Pydantic-схема
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


# Middleware для HTTP-метрик
@app.middleware("http")
async def metrics_middleware(request, call_next):
    start = time.time()
    response = await call_next(request)
    duration = time.time() - start

    route = request.url.path
    REQUEST_COUNT.labels(
        method=request.method,
        route=route,
        status=response.status_code
    ).inc()
    REQUEST_DURATION.labels(
        method=request.method,
        route=route
    ).observe(duration)
    return response

# Пользовательские ручки
@app.post("/orders", response_model=OrderResponse)
def create_order(order: OrderCreate, db: Session = Depends(get_db)):
    new_order = Order(
        amount=order.amount,
        items_count=order.items_count,
        status="NEW",
        created_at=datetime.now(timezone.utc)
    )
    db.add(new_order)
    db.commit()
    db.refresh(new_order)

    # Отправляем в RabbitMQ
    rabbitmq_host = os.getenv("RABBITMQ_HOST", "rabbitmq")
    try:
        connection = pika.BlockingConnection(
            pika.ConnectionParameters(host=rabbitmq_host)
        )
        channel = connection.channel()
        channel.queue_declare(queue="orders_queue", durable=True)
        channel.basic_publish(
            exchange='',
            routing_key='orders_queue',
            body=str(new_order.id),
            properties=pika.BasicProperties(delivery_mode=2)
        )
        connection.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"RabbitMQ error: {e}")

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

# Технические ручки
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