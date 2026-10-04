# desk-curfew-server

Серверная часть desk-curfew — системы ограничения времени работы
детских компьютеров.

Сервер управляет расписанием, считает активное время по heartbeat-ам
агентов, отправляет команды блокировки/разблокировки через MQTT
и предоставляет веб-интерфейс для администрирования.

## Архитектура

Обмен между агентами и сервером идёт только через MQTT.

### MQTT-топики

| Топик | Направление | Назначение |
| --- | --- | --- |
| `{pc}/cmd` | сервер → агент | Команды (lock, unlock, shutdown) |
| `{pc}/status` | агент → сервер | Retained-статус агента |
| `{pc}/event` | агент → сервер | События (locked, unlocked, warning) |
| `{pc}/hb` | агент → сервер | Heartbeat каждые 30 сек |
| `server/cmd` | внешнее → сервер | Управление сервером (смена конфига) |
| `server/status` | сервер → внешнее | Retained-статус сервера |

Где `{pc}` — `evgeny_pc` или `andrey_pc`.

## Стек

- Python 3.14
- FastAPI (async) + Uvicorn
- SQLAlchemy 2.0 (async для API, sync для Alembic)
- Alembic (миграции)
- PostgreSQL
- paho-mqtt (MQTT-клиент)
- Jinja2 + HTMX (веб-интерфейс)
- Docker + docker-compose
- pydantic-settings (конфиг из .env)

## Быстрый старт

1. Переменные окружения

```bash
cp .env.example .env
```

2. Запуск

```bash
docker compose up -d --build
```

Сервис поднимется на порту 8000.

3. Миграции

```bash
docker compose exec app alembic upgrade head
```

4. Инициализация: админ + фикстуры ПК

```bash
docker compose run --rm app python -m app.cli init
```

Команда запросит пароль для `admin` и загрузит ПК из `fixtures/pcs.yaml`.

5. Веб-интерфейс

Открыть в браузере из локальной сети:

`http://<ip-сервера>:8000`

DNS-имя прописывается на роутере, например curfew.home.local.

## CLI-команды

Запускаются локально как `python -m app.cli <команда>`
или в Docker как `docker compose run --rm app python -m app.cli <команда>`.

| Команда | Описание |
| --- | --- |
| `init` | Полная инициализация: создаёт пользователя `admin` (если нет) и загружает фикстуры ПК из `fixtures/pcs.yaml` |
| `seed` | Загружает (или перезагружает) ПК и расписания из `fixtures/pcs.yaml` |
| `create-user USERNAME` | Создаёт нового пользователя; пароль запрашивается интерактивно (скрытый ввод с подтверждением) |

Примеры:

```bash
# Полная инициализация (админ + фикстуры)
python -m app.cli init

# Только фикстуры ПК
python -m app.cli seed

# Создать пользователя (пароль спросит интерактивно)
python -m app.cli create-user admin

# Список всех команд и справки
python -m app.cli --help
```

## API

| Метод | Путь | Описание |
| --- | --- | --- |
| GET | `/health` | Health check |
| POST | `/auth/login` | Логин (JSON), устанавливает cookie |
| POST | `/auth/logout` | Выход |
| GET | `/auth/me` | Текущий пользователь |
| GET | `/` | Дашборд (веб) |
| GET | `/login` | Страница входа (веб) |
| GET | `/api/pcs` | Список ПК с использованием за сегодня |
| GET | `/api/pcs/{name}` | ПК + использование за сегодня |
| PATCH | `/api/pcs/{name}` | Обновить настройки ПК |
| GET | `/api/pcs/{name}/usage` | История использования по дням |
| GET | `/api/pcs/{name}/events` | События агента |
| GET | `/api/pcs/{name}/commands` | История команд |
| GET | `/api/pcs/{name}/schedule` | Расписание ПК |
| PUT | `/api/pcs/{name}/schedule` | Полностью заменить расписание |
| POST | `/api/pcs/{name}/lock` | Заблокировать ПК |
| POST | `/api/pcs/{name}/unlock` | Разблокировать ПК |
| POST | `/api/pcs/{name}/add-time` | Добавить время |
| GET | `/api/stats` | Общая статистика |

Все эндпоинты `/api/*` требуют авторизации (cookie-сессия).

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

Ручная блокировка (`manual`) не снимается планировщиком — только вручную
или автоматически в 00:00 (истечение `manual_lock_until`).

## Тесты

```bash
docker compose --profile test up -d test-db
pytest tests/integration/ -v
```

## Лицензия

MIT