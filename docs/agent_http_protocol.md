# HTTP-протокол агента desk-curfew

Описание контракта между сервером и агентом на детских ПК.
Агент общается с сервером **только по HTTP**, один эндпоинт — `POST /api/agent/hb`.

## Транспорт

- Протокол: HTTP (в LAN без TLS; при выносе сервера в интернет — HTTPS через домен).
- Метод: `POST /api/agent/hb`
- Авторизация: `Authorization: Bearer <agent_token>`
- Интервал: агент шлёт heartbeat каждые **10 секунд** (серверный `heartbeat_interval` = 30, но агент может чаще — сервер адаптируется по факту).
- Content-Type: `application/json`

## Авторизация

Токен выдаётся в веб-интерфейсе на странице ПК (кнопка «Сгенерировать токен»).
Показывается **один раз**, хранится в `config.ini` агента.

- Сервер хранит bcrypt-хеш в `pcs.agent_token_hash`.
- Агент кладёт raw-токен в `Authorization: Bearer <token>`.
- Неверный/отсутствующий токен → `401 Unauthorized`.
- Токен привязан к конкретному ПК; один токен = один ПК.

## Heartbeat: агент → сервер

```json
POST /api/agent/hb
Authorization: Bearer <agent_token>

{
  "locked": false,
  "lock_reason": null,
  "idle_seconds": 0,
  "active_user": "kid1",
  "events": [
    {"event": "locked", "reason": "daily_limit"},
    {"event": "pin_attempt"},
    {"event": "fail_open"}
  ]
}
```

### Поля

| Поле | Тип | Обязательно | Описание |
| --- | --- | --- | --- |
| `locked` | bool | да | Заблокирован ли сейчас рабочий стол агентом |
| `lock_reason` | string \| null | да | Причина блокировки (если `locked=true`): `daily_limit`, `schedule`, `manual` |
| `idle_seconds` | int | да | Секунд без ввода (мышь/клавиатура), `GetLastInputInfo` |
| `active_user` | string \| null | нет | Имя залогиненного пользователя Windows |
| `events` | array | да | События с момента прошлого hb (может быть пустым) |

### События в массиве `events`

| event | Описание |
| --- | --- |
| `agent_started` | Агент запустился |
| `agent_stopped` | Агент останавливается (корректно) |
| `locked` | Факт блокировки (после `lock_now` / `lock_in`) |
| `unlocked` | Факт разблокировки |
| `warning_shown` | Показан тост `lock_in` с обратным отсчётом |
| `pin_attempt` | Неверный PIN на секретной комбинации |
| `fail_open` | Автоматическая разблокировка при потере связи |

Каждое событие: `{"event": "...", "reason": "..."}` — `reason` опционален (null допустим).

## Heartbeat: сервер → агент (ответ)

```json
200 OK

{
  "commands": [
    {
      "action": "lock_in",
      "delay_seconds": 300,
      "reason": "schedule",
      "info": {
        "display_name": "Компьютер Евгения",
        "reason": "schedule",
        "reason_ru": "Сейчас по расписанию — перерыв",
        "locked_at": "2026-10-06T19:42:00+05:00",
        "unlocks_at": "2026-10-06T20:00:00+05:00",
        "next_window": "20:00–21:00"
      }
    },
    {
      "action": "add_time",
      "minutes": 30
    }
  ],
  "server_time": "2026-10-06T14:42:00+00:00"
}
```

### Поля ответа

| Поле | Тип | Описание |
| --- | --- | --- |
| `commands` | array | Команды для выполнения (может быть пустым) |
| `server_time` | string (ISO 8601) | Текущее время сервера (UTC) |

### Коды ответа

| Код | Значение |
| --- | --- |
| 200 | Успех, команды в `commands` |
| 401 | Неверный/отсутствующий токен или токен для неактивного ПК |

## Команды сервер → агент (через `commands`)

Формат команд идентичен MQTT-протоколу (см. `docs/agent_protocol.md`),
меняется только транспорт доставки:

### `lock_now`
```json
{"action": "lock_now", "reason": "...", "info": {...}}
```
Немедленно заблокировать. После блокировки прислать `event locked` в следующем hb.

### `lock_in`
```json
{"action": "lock_in", "delay_seconds": N, "reason": "...", "info": {...}}
```
Показать предупреждение на N секунд (`event warning_shown`), затем заблокировать.

### `unlock`
```json
{"action": "unlock", "reason": "..."}
```
Снять блокировку, прислать `event unlocked`.

### `add_time`
```json
{"action": "add_time", "minutes": N}
```
Информационная: показать «+N минут». Агент НЕ разблокирует — сервер при
необходимости пришлёт `unlock` отдельной командой в следующем hb.

### Блок `info` (данные для экрана блокировки)

Идентичен MQTT-версии — см. `docs/agent_protocol.md`.

## Учёт времени на сервере

Сервер обрабатывает каждый hb:

1. Обновляет `is_online`, `last_seen_at`, `last_active_user`.
2. `idle_seconds >= порога (120 с)` → `is_idle = true`, капает `idle_seconds`.
3. `locked = true` → капает `locked_seconds`.
4. Иначе → капает `active_seconds` (тратится дневной лимит).
5. Обрабатывает `events` (обновляет `is_locked` по `locked`/`unlocked`).
6. Если `desired_locked=true`, но агент шлёт `locked=false` и последняя
   команда старше 120 с → немедленно кладёт `lock_now` в очередь
   (следующий hb заберёт).

## Оффлайн

Нет hb дольше **90 секунд** → сервер помечает ПК оффлайн и не шлёт команды.
Восстановление связи — автоматическое (по следующему hb).

## Resend

Сервер хочет блокировку (`desired_locked=true`), но агент шлёт `locked=false`:
- По hb — немедленный resend `lock_now` (если `last_command_at` старше 120 с).
- По планировщику (тик 30 с) — resend, если `last_command_at` старше 120 с.

Агент должен быть идемпотентным: повторный `lock_now` в заблокированном
состоянии — no-op + `event locked`.

## Пример полного цикла

```
Агент → Сервер:
  POST /api/agent/hb
  Authorization: Bearer abc123...
  {"locked": false, "lock_reason": null, "idle_seconds": 0,
   "active_user": "kid1", "events": []}

Сервер → Агент:
  200 OK
  {"commands": [
    {"action": "lock_in", "delay_seconds": 300, "reason": "schedule",
     "info": {"display_name": "...", "reason": "schedule", ...}}
  ], "server_time": "..."}

Агент:
  - Показывает тост «Через 5 минут заблокируется»
  - Шлёт event warning_shown в следующем hb

Через 300 с:
  - Блокирует рабочий стол
  - Шлёт event locked в следующем hb

Агент → Сервер:
  POST /api/agent/hb
  {"locked": true, "lock_reason": "schedule", "idle_seconds": 0,
   "active_user": "kid1", "events": [
     {"event": "warning_shown"},
     {"event": "locked", "reason": "schedule"}
   ]}

Сервер → Агент:
  200 OK
  {"commands": [], "server_time": "..."}
```

## Безопасность

- Токен — `secrets.token_urlsafe(32)`, 43 символа.
- Хранится в `config.ini` агента (файл доступен только админу Windows).
- Передача по HTTP в LAN — приемлемо; при выносе сервера наружу — только HTTPS.
- При компрометации токена — перегенерировать в веб-интерфейсе.
