import os
import time
from datetime import datetime, timezone

import pika
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from prometheus_client import Histogram, start_http_server

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/shopdb")
PROCESSING_DELAY_MS = int(os.getenv("PROCESSING_DELAY_MS", "1000"))
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
METRICS_PORT = int(os.getenv("METRICS_PORT", "9101"))

# ──────────────────────────────────────────────
# МЕТРИКА (п. 3.2 из методички)
# ──────────────────────────────────────────────
PROCESSING_DURATION = Histogram(
    'shop_order_processing_duration_seconds',
    'Time from receiving order to DONE'
)

# Подключение к БД
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)

def process_order(ch, method, properties, body):
    start_time = time.time()
    order_id = int(body.decode())
    print(f"[Worker] Processing order {order_id}")

    db = SessionLocal()
    try:
        from sqlalchemy import text
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

        duration = time.time() - start_time
        PROCESSING_DURATION.observe(duration)
        print(f"[Worker] Order {order_id} DONE in {duration:.2f}s")
    except Exception as e:
        print(f"[Worker] Error processing order {order_id}: {e}")
    finally:
        db.close()
        ch.basic_ack(delivery_tag=method.delivery_tag)

def main():
    # Запускаем HTTP-сервер для метрик
    start_http_server(METRICS_PORT)
    print(f"[Worker] Metrics server started on :{METRICS_PORT}")

    while True:
        try:
            connection = pika.BlockingConnection(
                pika.ConnectionParameters(host=RABBITMQ_HOST, heartbeat=600)
            )
            channel = connection.channel()
            channel.queue_declare(queue="orders_queue", durable=True)
            channel.basic_qos(prefetch_count=1)
            channel.basic_consume(queue="orders_queue", on_message_callback=process_order)
            print("[Worker] Waiting for messages...")
            channel.start_consuming()
        except Exception as e:
            print(f"[Worker] Connection error: {e}. Retrying in 5s...")
            time.sleep(5)

if __name__ == "__main__":
    main()