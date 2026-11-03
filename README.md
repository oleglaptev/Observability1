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

## 📝 Лицензия

Учебный проект. Свободное использование в образовательных целях.
