https://github.com/oleglaptev/Observability1
# Лабораторная работа №1 — Метрики и Prometheus

Курс «Наблюдаемость распределённых систем»

Учебный стенд наблюдаемости: небольшое приложение обработки заказов (API + Worker),
для которого самостоятельно настроен сбор метрик приложения, PostgreSQL, RabbitMQ,
хоста и контейнеров, а также доставка метрик в долгосрочное хранилище VictoriaMetrics.

![Python](https://img.shields.io/badge/Python-3.11-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green)
![Prometheus](https://img.shields.io/badge/Prometheus-2.53-orange)
![VictoriaMetrics](https://img.shields.io/badge/VictoriaMetrics-1.101-blueviolet)
![Docker](https://img.shields.io/badge/Docker-compose-2496ED)

---

## Цель работы

Написать приложение-стенд и настроить полный контур наблюдаемости:
встроенные метрики кода, готовые инфраструктурные экспортеры,
собственный exporter-адаптер (JSON → Prometheus), сбор метрик Prometheus
и долгосрочное хранение в VictoriaMetrics через `remote_write`.

---

## Архитектура стенда
<img width="903" height="939" alt="image" src="https://github.com/user-attachments/assets/7625609f-8016-4d79-bf86-729defe6830d" />


**Жизненный цикл заказа:** `POST /orders` → запись `NEW` в PostgreSQL →
сообщение в RabbitMQ → Worker переводит в `PROCESSING` → ждёт
`PROCESSING_DELAY_MS` → переводит в `DONE` и проставляет `processed_at`.

---

## Технологический стек

| Слой | Технология |
|---|---|
| Язык / фреймворк | Python 3.11, FastAPI, uvicorn |
| База данных | PostgreSQL 16 |
| Брокер сообщений | RabbitMQ 3.13 (management + prometheus plugin) |
| Метрики кода | prometheus-client |
| Сбор метрик | Prometheus 2.53 |
| Долгосрочное хранение | VictoriaMetrics 1.101 |
| Инфраструктурные экспортеры | node_exporter, cAdvisor, postgres_exporter, rabbitmq_prometheus |
| Упаковка | Docker, docker-compose |

---

## Быстрый старт

Требуется: Docker + Docker Compose.

```bash
git clone [https://github.com/oleglaptev/Observability1/]
cd Observability1
docker-compose up -d --build
```

Проверка:

```bash
curl http://localhost:8080/health          # {"status":"ok"}
curl http://localhost:9090/targets         # все цели UP (или открыть в браузере)
```

Генерация нагрузки (PowerShell):

```powershell
for ($i = 1; $i -le 50; $i++) {
    Invoke-RestMethod -Method POST -Uri "http://localhost:8080/orders" `
      -ContentType "application/json" `
      -Body (@{ amount = (Get-Random -Minimum 100 -Maximum 5000); items_count = (Get-Random -Minimum 1 -Maximum 10) } | ConvertTo-Json)
}
```

### Компоненты и адреса

| Компонент | Адрес | Назначение |
|---|---|---|
| API | http://localhost:8080 | бизнес-ручки + `/metrics`, `/internal/stats`, Swagger: `/docs` |
| Worker metrics | http://localhost:9101/metrics | метрика времени обработки |
| Собственный exporter | http://localhost:9102/metrics | Gauge-метрики из `/internal/stats` |
| RabbitMQ Management | http://localhost:15672 | guest / guest |
| RabbitMQ metrics | http://localhost:15692/metrics | встроенный prometheus-плагин |
| node_exporter | http://localhost:9100/metrics | метрики хоста |
| cAdvisor | http://localhost:8081 | метрики контейнеров |
| postgres_exporter | http://localhost:9187/metrics | метрики PostgreSQL |
| Prometheus | http://localhost:9090 | сбор метрик, Targets, Graph |
| VictoriaMetrics | http://localhost:8428 / `/vmui` | долгосрочное хранение |

---

## 📈 Метрики

### Метрики API (встроенные, prometheus-client в коде)

| Метрика | Тип | Метки | Когда обновляется |
|---|---|---|---|
| `http_requests_total` | Counter | `method`, `route`, `status` | после каждого HTTP-запроса |
| `http_request_duration_seconds` | Histogram | `method`, `route` | после каждого HTTP-запроса |
| `shop_orders_created_total` | Counter | — | после успешного `POST /orders` |

### Метрика Worker (встроенная)

| Метрика | Тип | Когда обновляется |
|---|---|---|
| `shop_order_processing_duration_seconds` | Histogram | по завершении обработки заказа (сообщение из RabbitMQ → статус `DONE`) |

### Метрики собственного exporter (адаптер JSON → Prometheus)

| Метрика | Тип | Источник |
|---|---|---|
| `shop_stats_orders_new` | Gauge | `orders_new` из `/internal/stats` |
| `shop_stats_orders_processing` | Gauge | `orders_processing` |
| `shop_stats_orders_done` | Gauge | `orders_done` |
| `shop_stats_seconds_since_last_processed` | Gauge | `now() − last_processed_at` |

> Дублирования нет: текущие состояния `NEW / PROCESSING / DONE` не выводятся
> через `/metrics` API — их создаёт только собственный exporter,
> а HTTP-метрики и время обработки живут только в коде API/Worker.

### Готовые метрики инфраструктуры

| Источник | Метрики |
|---|---|
| node_exporter | `node_cpu_seconds_total`, `node_memory_MemAvailable_bytes`, `node_filesystem_avail_bytes` |
| cAdvisor | `container_cpu_usage_seconds_total`, `container_memory_working_set_bytes`, `container_network_receive_bytes_total` / `..._transmit_bytes_total` |
| postgres_exporter | `pg_up`, `pg_stat_database_numbackends`, `pg_stat_database_xact_commit_total` |
| rabbitmq_prometheus | `rabbitmq_queue_messages_ready`, `rabbitmq_queue_messages_unacked`, `rabbitmq_queue_consumers`, `rabbitmq_connections` |

---

## ⚙️ Конфигурация мониторинга

`prometheus/prometheus.yml`: 7 целей scrape
(`api`, `worker`, `custom_exporter`, `node_exporter`, `cadvisor`,
`postgres_exporter`, `rabbitmq`) + `remote_write` в VictoriaMetrics:

```yaml
remote_write:
  - url: 'http://victoria_metrics:8428/api/v1/write'
```

Проверка доставки данных в VictoriaMetrics:

```bash
curl "http://localhost:8428/api/v1/query?query=up"
```
<img width="2074" height="782" alt="image" src="https://github.com/user-attachments/assets/c0fd5a33-ec90-4ffb-9b81-0b538adbd6de" />

### Скриншот Prometheus → Targets (все цели UP)

<img width="1722" height="1151" alt="Снимок экрана 2026-11-03 152045" src="https://github.com/user-attachments/assets/e308163c-83e6-47cf-adcf-10aa145e61c0" />

---

## PromQL-запросы
### 1 | Сколько запросов в секунду получает API? | `sum(rate(http_requests_total[1m]))`
<img width="2215" height="955" alt="Снимок экрана 2026-11-03 020556" src="https://github.com/user-attachments/assets/92199a61-640e-4de5-b9f2-52f6cc7e95e9" />
Показывает суммарный RPS по всем ручкам API; «полка» ~0.12 req/s в покое — это служебный трафик (scrape Prometheus и опрос exporter), пики — пользовательская нагрузка.

### 2 | Какой p95 времени ответа POST /orders? | `histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket{method="POST",route="/orders"}[5m])) by (le))` 
<img width="2196" height="946" alt="Снимок экрана 2026-11-03 020612" src="https://github.com/user-attachments/assets/41b1c172-e573-4710-abc0-3123b3d81d2a" />
Показывает 95-й перцентиль из гистограммы длительностей.

### 3 | Сколько памяти использует контейнер API? | `container_memory_working_set_bytes{name=~".*api.*"} / 1024 / 1024` 
<img width="2216" height="961" alt="Снимок экрана 2026-11-03 020629" src="https://github.com/user-attachments/assets/646d06d2-3a55-47f9-9be0-32a4c5b0da79" />
Рабочая память контейнера API по данным cAdvisor, в МБ.

### 4 | Сколько сообщений сейчас ждут обработки в RabbitMQ? | `rabbitmq_queue_messages_ready` 
<img width="2214" height="941" alt="Снимок экрана 2026-11-03 020639" src="https://github.com/user-attachments/assets/08e78397-b5d0-4d4d-848c-e5f8398418ca" />
Глубина очереди: растёт пиками, когда сообщения приходят быстрее, чем Worker успевает обрабатывать (`PROCESSING_DELAY_MS`), и падает до 0 после выгребания.

### 5 | Сколько заказов сейчас находятся в PROCESSING? | `shop_stats_orders_processing` |
<img width="2207" height="989" alt="Снимок экрана 2026-11-03 020651" src="https://github.com/user-attachments/assets/2462867d-2b6a-45f8-841b-c257d01e38a4" />
Бизнес-показатели, созданные exporter-адаптером из JSON `/internal/stats`.

### 6 | Сколько секунд прошло с момента последнего обработанного заказа? | `shop_stats_seconds_since_last_processed`
<img width="2198" height="973" alt="Снимок экрана 2026-11-03 020702" src="https://github.com/user-attachments/assets/598f7219-ae1a-464a-aabc-bd1444c7d603" />
Бизнес-показатели, созданные exporter-адаптером из JSON `/internal/stats`.

### 7 | Сколько соединений открыто к PostgreSQL? | `pg_stat_database_numbackends{datname="shopdb"}`
<img width="2191" height="1000" alt="Снимок экрана 2026-11-03 020719" src="https://github.com/user-attachments/assets/29af1b1c-647e-4d73-83e2-20f25ab41304" />
Число активных backend-соединений к базе `shopdb`.

---

## Вывод

В работе использованы три способа получения метрик, и они принципиально различаются:

1. **Встроенные метрики API/Worker** рождаются внутри кода в момент выполнения
   операции (HTTP-запрос, обработка заказа). Они описывают поведение сервиса
   изнутри и доступны только потому, что мы владеем исходным кодом.
2. **Готовые метрики инфраструктуры** (node_exporter, cAdvisor,
   postgres_exporter, rabbitmq_prometheus) собираются внешними агентами без
   изменения кода наблюдаемых систем и описывают состояние хоста, контейнеров
   и зависимостей, а не бизнес-логику.
3. **Метрики собственного exporter** появляются там, где система не умеет
   отдавать формат Prometheus, но предоставляет данные через JSON:
   exporter выступает адаптером-переводчиком, опрашивая `/internal/stats`
   и создавая Gauge-метрики на лету. Это типовой подход для интеграции
   «чужих» или legacy-систем в контур мониторинга.

Prometheus при этом не создаёт исходные метрики сам — он лишь забирает
уже подготовленные значения с ручек `/metrics`, а VictoriaMetrics
обеспечивает их долгосрочное хранение через `remote_write`.

---
# Лабораторная работа №2: Логи и централизованное логирование

## 1. Схема стенда
<img width="1550" height="1390" alt="Наблюдаемость2" src="https://github.com/user-attachments/assets/14b692d2-48b7-4e30-bcfe-f1a1d0db9adb" />

## 2. Реализация request_id
- **Генерация:** Middleware в API генерирует UUID или берет из заголовка `X-Request-ID`.
- **Передача:** В RabbitMQ отправляется JSON `{"order_id": ..., "request_id": ...}`.
- **Приём:** Worker парсит JSON и использует `request_id` во всех логах.

## 3. Примеры логов (JSON)
**API (order_created):**
```json
{"timestamp": "2026-11-18T16:39:14.933Z", "level": "INFO", "service": "api", "event": "order_created", "request_id": "lab2-slow-12", "order_id": 433, "amount": 250.0, "items_count": 2}
```
**Worker (processing_finished):**
```json
{"timestamp": "2026-11-18T16:40:13.508Z", "level": "INFO", "service": "worker", "event": "processing_finished", "request_id": "lab2-slow-12", "order_id": 433, "duration_ms": 5006.12}
```
## 4. Сценарий медленного Worker
- **Условие:** `PROCESSING_DELAY_MS=5000`, серия из 12 заказов.
- **Метрики (Prometheus):**
  - `rabbitmq_queue_messages_ready`: пик до 11 сообщений.
  - `histogram_quantile(0.95, ...)`: рост p95 времени обработки до 5с.
- **Анализ заказа lab2-slow-12 (Kibana):**
  - Время в очереди: 53 561 мс.
  - Время обработки: 5 006 мс.
  - **Вывод:** Основная задержка (91% времени) возникла в очереди RabbitMQ из-за ограниченной пропускной способности одного Worker'а.

## 5. Скриншоты
5.1. Data View в Kibana.
<img width="1831" height="1028" alt="Снимок экрана 2026-11-18 162627" src="https://github.com/user-attachments/assets/abac608f-3941-4bf9-be22-b27af7ec2c0f" />

5.2. Поиск по `order_id`.
<img width="1924" height="918" alt="Снимок экрана 2026-11-18 164156" src="https://github.com/user-attachments/assets/4c4c9ebe-cf1c-44f2-9165-1820cb21ca77" />

5.3. Поиск по `request_id`.
<img width="1767" height="1037" alt="Снимок экрана 2026-11-18 164235" src="https://github.com/user-attachments/assets/de6bc049-9f27-4c75-b036-94e432639019" />

5.4. Рост времени обработки заказа. После увеличения задержки Worker'а до 5 секунд метрика p95 выросла пропорционально, подтверждая, что обработка замедлилась.
<img width="2200" height="938" alt="Снимок экрана 2026-11-18 164056" src="https://github.com/user-attachments/assets/228be05d-1792-4126-9582-4c0d0e75d896" />

5.5. Динамика очереди необработанных заказов. Пик в 9 заказов возник из-за того, что пропускная способность одного Worker'а (1 заказ / 5 сек) оказалась ниже скорости поступления запросов. Метрика наглядно показывает момент перегрузки системы.
<img width="2178" height="915" alt="Снимок экрана 2026-11-18 164041" src="https://github.com/user-attachments/assets/cfed396c-be8a-49b8-8874-d64177d6d376" />


## 6. Вывод
- **Метрики** (Prometheus) отвечают на вопрос «Что происходит с системой в целом?».
Пример: График rabbitmq_queue_messages_ready вырос — мы видим, что система перегружена, но не знаем, какой именно заказ страдает и почему.
Плюсы: Дешёвое хранение, агрегация, алертинг.
  
- **Логи** (ELK) отвечают на вопрос «Что случилось с конкретным запросом?».
Пример: По request_id: lab2-slow-12 мы восстановили таймлайн и увидели, что 53 секунды заказ лежал в очереди.
Плюсы: Детальный контекст, трассировка ошибок.
**Связка:** Метрики дают сигнал тревоги («очередь растёт»), а логи используются для root-cause анализа конкретного инцидента. Без request_id связать их невозможно.

## 📝 Лицензия

Учебный проект. Свободное использование в образовательных целях.




