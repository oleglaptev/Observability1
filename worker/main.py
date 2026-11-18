# Observability1/worker/main.py
import json
import os
import time
import uuid
from datetime import datetime, timezone

import pika
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from prometheus_client import Histogram, start_http_server

from common.jsonlog import init as init_logging, log_event

init_logging("worker")

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/shopdb")
PROCESSING_DELAY_MS = int(os.getenv("PROCESSING_DELAY_MS", "1000"))
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
METRICS_PORT = int(os.getenv("METRICS_PORT", "9101"))
QUEUE_NAME = "orders_queue"

# МЕТРИКА (без изменений из лабы №1)
PROCESSING_DURATION = Histogram(
    'shop_order_processing_duration_seconds',
    'Time from receiving order to DONE'
)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)


def parse_message(body: bytes) -> tuple[int, str]:
    """Новый формат: {"order_id": 42, "request_id": "..."}.
    Старый формат лабы №1 (просто '42') — тоже поддерживаем."""
    raw = body.decode()
    try:
        payload = json.loads(raw)
        return int(payload["order_id"]), str(payload.get("request_id") or uuid.uuid4())
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return int(raw), str(uuid.uuid4())


def process_order(ch, method, properties, body):
    start = time.perf_counter()
    order_id, request_id = parse_message(body)

    log_event("message_received", request_id=request_id, order_id=order_id, queue=QUEUE_NAME)

    db = SessionLocal()
    try:
        log_event("processing_started", request_id=request_id, order_id=order_id)

        # Статус PROCESSING
        db.execute(
            text("UPDATE orders SET status = 'PROCESSING' WHERE id = :id"),
            {"id": order_id}
        )
        db.commit()

        # Имитация работы
        time.sleep(PROCESSING_DELAY_MS / 1000.0)

        # Статус DONE
        db.execute(
            text("UPDATE orders SET status = 'DONE', processed_at = :now WHERE id = :id"),
            {"id": order_id, "now": datetime.now(timezone.utc)}
        )
        db.commit()

        duration = time.perf_counter() - start
        PROCESSING_DURATION.observe(duration)
        log_event("processing_finished", request_id=request_id, order_id=order_id,
                  duration_ms=round(duration * 1000, 2))
    except Exception as e:
        log_event("processing_failed", level="ERROR", request_id=request_id,
                  order_id=order_id, error=str(e))
    finally:
        db.close()

    ch.basic_ack(delivery_tag=method.delivery_tag)


def main():
    start_http_server(METRICS_PORT)
    log_event("worker_started", metrics_port=METRICS_PORT,
              processing_delay_ms=PROCESSING_DELAY_MS)

    while True:
        try:
            connection = pika.BlockingConnection(
                pika.ConnectionParameters(host=RABBITMQ_HOST, heartbeat=600)
            )
            channel = connection.channel()
            channel.queue_declare(queue=QUEUE_NAME, durable=True)
            channel.basic_qos(prefetch_count=1)
            channel.basic_consume(queue=QUEUE_NAME, on_message_callback=process_order)
            log_event("worker_waiting_messages", queue=QUEUE_NAME)
            channel.start_consuming()
        except Exception as e:
            log_event("connection_error", level="ERROR", error=str(e), retry_in_seconds=5)
            time.sleep(5)


if __name__ == "__main__":
    main()