# Протокол агента desk-curfew (рабочая памятка)

Живой документ. Сюда записываем все договорённости по MQTT-протоколу
и семантике команд, чтобы позже собрать из него промт для разработки агента.

## Транспорт

- Брокер: srv2.clusterfly.ru, WebSocket `ws://...:9993/mqtt` (без TLS)
  или `wss://...:9994/mqtt` (TLS). Логин/пароль — учётка clusterfly.
- Префикс топиков: `user_<id>/curfew/kids/`.
- `{pc}` — имя ПК из БД (`evgeny_pc`, `andrey_pc`).

## Топики

| Топик | Направление | QoS | Retained | Назначение |
| --- | --- | --- | --- | --- |
| `{pc}/cmd` | сервер → агент | 1 | нет | Команды агенту |
| `{pc}/hb` | агент → сервер | 0 | нет | Heartbeat каждые 30 с |
| `{pc}/status` | агент → сервер | 1 | да | Текущий статус агента |
| `{pc}/event` | агент → сервер | 1 | нет | События жизненного цикла |
| `server/cmd` | внешнее → сервер | 1 | нет | Управление сервером (на будущее) |
| `server/status` | сервер → внешнее | 1 | да | Статус сервера |

## Команды сервер → агент (`{pc}/cmd`)

### `{"action": "lock_now", "reason": "..."}`
Немедленно заблокировать рабочий стол. После фактической блокировки
опубликовать `event locked` с reason.

### `{"action": "lock_in", "delay_seconds": N, "reason": "..."}`
Показать предупреждение с обратным отсчётом на N секунд
(событие `warning_shown`), по истечении — заблокировать и прислать `event locked`.

### `{"action": "unlock", "reason": "..."}`
Снять блокировку, прислать `event unlocked`.

### `{"action": "add_time", "minutes": N}`
ИНФОРМАЦИОННАЯ команда. Показать уведомление «+N минут»,
при желании обновить индикатор оставшегося времени на экране блокировки.
**Агент НЕ разблокирует сам**: сервер пересчитал бонус и, если нужно,
пришлёт `unlock` отдельной командой.

## Агент → сервер

### `{pc}/hb` каждые 30 с
`{"ts": "...Z", "active_user": "имя", "locked": false, "lock_reason": null}`
Сервер по hb считает время: `locked=true` → `locked_seconds`, иначе `active_seconds`.

### `{pc}/status` (retained)
`{"online": true, "locked": false, "user": "...", "ts": "..."}`
Шлётся при старте, остановке и смене состояния.

### `{pc}/event`
`{"event": "agent_started" | "agent_stopped" | "locked" | "unlocked" | "warning_shown", "reason": "..."}`

## Серверная семантика (учитывать в промте агента)

- Оффлайн: нет hb дольше 90 с → сервер помечает ПК оффлайн и не шлёт команды.
- Если сервер хочет блокировку, но `event locked` не пришёл за 120 с —
  сервер повторно шлёт `lock_now`. Агенту стоит быть идемпотентным:
  повторный `lock_now` в заблокированном состоянии — это no-op + `event locked`.
- Ручная блокировка действует до 00:00 серверной таймзоны (Asia/Yekaterinburg).
- Дневной лимит: `active_seconds >= лимит_дня * 60 + bonus_seconds`.
- `bonus_seconds` (подаренное время) сгорает в 00:00 и НЕ снимает
  блокировки по расписанию и ручные — только по лимиту.
- Лимит может отличаться по дням недели (`day_limits` у ПК).

## Управление сервером (`server/cmd`)

Внешние приложения управляют сервером, публикуя JSON в `server/cmd`:

{"action": "lock", "pc": "evgeny_pc", "reason": "manual", "delay_seconds": 0}
{"action": "unlock", "pc": "evgeny_pc"}
{"action": "add_time", "pc": "evgeny_pc", "minutes": 30}

- `lock` — аналог кнопки «Заблокировать» (reason/delay_seconds опциональны).
- `unlock` — разблокировка.
- `add_time` — бонусное время; НЕ снимает schedule/manual-блокировки.
- Неизвестный ПК или action → warning в лог, действие не выполняется.

## Статус сервера (`server/status`, retained, обновление каждые 60 с)

{
  "online": true,
  "ts": "2026-10-05T12:00:00+00:00",
  "pcs": {
    "evgeny_pc": {
      "display_name": "Компьютер Евгения",
      "is_online": true,
      "is_locked": false,
      "lock_reason": null,
      "desired_locked": false,
      "desired_lock_reason": null,
      "manual_lock_until": null,
      "last_seen_at": "2026-10-05T11:59:30+00:00",
      "last_active_user": "kid1",
      "active_seconds": 3600,
      "locked_seconds": 0,
      "bonus_seconds": 1800,
      "limit_seconds": 9000
    }
  }
}