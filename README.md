# desk-curfew-server

Серверная часть **desk-curfew** — системы ограничения времени работы
детских компьютеров.

Сервер управляет расписанием, считает активное время по heartbeat-ам
агентов, отправляет команды блокировки/разблокировки через MQTT
и предоставляет веб-интерфейс для администрирования.

## Архитектура

Обмен между агентами и сервером идёт **только через MQTT**.

## MQTT-топики


| Топик           | Направление      | Назначение                          |
|-----------------|------------------|-------------------------------------|
| `{pc}/cmd`      | сервер → агент   | Команды (lock, unlock, shutdown)    |
| `{pc}/status`   | агент → сервер   | Retained-статус агента              |
| `{pc}/event`    | агент → сервер   | События (locked, unlocked, warning) |
| `{pc}/hb`       | агент → сервер   | Heartbeat каждые 30 сек             |
| `server/cmd`    | внешнее → сервер | Управление сервером (смена конфига) |
| `server/status` | сервер → внешнее | Retained-статус сервера             |

Где `{pc}` — `evgeny_pc` или `andrey_pc`.

## Стек

- Python 3.12
- FastAPI (async) + Uvicorn
- SQLAlchemy 2.0 (async для API, sync для Alembic)
- Alembic (миграции)
- PostgreSQL 16
- paho-mqtt (MQTT-клиент)
- Jinja2 + HTMX (веб-интерфейс)
- Docker + docker-compose
- pydantic-settings (конфиг из .env)

## Быстрый старт

### 1. Переменные окружения
```bash
cp .env.example .env
```


### 2.  Запуск
```bash
docker compose up -d --build
```
Сервис поднимется на порту 8000.

### 3. Миграции
```bash
docker compose exec app alembic upgrade head
```


### 4. Веб-интерфейс
Открыть в браузере из локальной сети:
`http://<ip-сервера>:8000`

DNS-имя прописывается на роутере, например curfew.home.local.

#### API

| Метод  | Путь                          | Описание              |
|--------|-------------------------------|-----------------------|
| `GET`  | `/`                           | Дашборд               |
| `GET`  | `/schedule`                   | Расписание            |
| `POST` | `/schedule/{pc_name}`         | Обновить расписание   |
| `POST` | `/control/{pc_name}/lock`     | Заблокировать ПК      |
| `POST` | `/control/{pc_name}/unlock`   | Разблокировать ПК     |
| `POST` | `/control/{pc_name}/add-time` | Добавить время        |
| `GET`  | `/history/{pc_name}`          | История использования |
| `GET`  | `/api/pc/{pc_name}/status`    | JSON-статус ПК        |
| `GET`  | `/health`                     | Health check          |

## Лицензия
MIT

## Планировщик (scheduler)

Для каждого активного ПК выполняется такой алгоритм:
```
Шаг 1. Проверка онлайн-статуса
   │
   ├─ last_seen_at старше 90 секунд?
   │     └─ ДА → пометить ПК как оффлайн
   │
   ├─ ПК оффлайн?
   │     └─ ДА → пропустить (не управлять выключенным ПК)
   │
Шаг 2. Проверка дневного лимита
   │
   ├─ active_seconds >= daily_limit_minutes × 60?
   │     └─ ДА → over_limit = True
   │
Шаг 3. Проверка расписания
   │
   ├─ Текущее время попадает в разрешённый слот?
   │     └─ ДА → allowed = True
   │     └─ НЕТ → allowed = False
   │
Шаг 4. Принятие решения
   │
   ├─ should_lock = (НЕ allowed) ИЛИ over_limit
   │
Шаг 5. Выполнение решения
   │
   ├─ Если should_lock и ПК ещё не desired_locked:
   │     └─ Отправить lock_pc с grace period
   │
   ├─ Если НЕ should_lock и ПК desired_locked
   │  (с причиной schedule или daily_limit):
   │     └─ Отправить unlock_pc
   │
   └─ Если ПК desired_locked, но фактически не заблокирован,
      и прошло > 120 сек с последней команды:
         └─ Повторно отправить lock_now
```