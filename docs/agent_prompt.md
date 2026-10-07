# Промт для создания агента desk-curfew

Ты — senior Python desktop-разработчик. Задача: написать агент desk-curfew —
клиентское приложение для детских ПК (Windows 10/11) родительского контроля.
Серверная часть (FastAPI + PostgreSQL) уже готова и работает; агент
общается с сервером **по HTTP** (поллинг heartbeat). К чату приложен
`docs/agent_http_protocol.md` — это источник правды по протоколу; следуй
ему строго, не выдумывай свой протокол. Отвечай на русском. Код — полными
файлами, с type hints, ruff (line-length 120).

## Контекст

Агент поллит `POST /api/agent/hb` каждые **10 секунд** с Bearer-токеном.
В ответе сервер возвращает массив `commands` — агент выполняет их.
Сервер находится в LAN, обращение по HTTP (без TLS). Имя ПК и токен —
из `config.ini`.

Подробный протокол: `docs/agent_http_protocol.md`.
Семантика команд и событий: `docs/agent_protocol.md`.

## Стек и архитектура

- Python 3.12+, PySide6, requests (или httpx), ctypes.
- ОДИН процесс: HTTP-поллинг в отдельном потоке, мост в Qt через сигналы/очередь.
- LL-хук клавиатуры — в отдельном потоке со своим message loop.
- Упаковка: PyInstaller onefile + `--uac-admin`.
- Конфиг: `config.ini` рядом с exe:
  ```ini
  [agent]
  pc_name = evgeny_pc
  server_url = http://192.168.1.100:8000
  agent_token = <raw_token>
  heartbeat_interval = 10

  [security]
  pin_hash =
  fail_open_hours = 12
  ```
- Агент рассчитан на запуск elevated (Task Scheduler `/sc onlogon /rl HIGHEST`),
  но НЕ должен падать без повышения.

## Функциональность (MVP, строго по протоколу)

1. **HTTP-поллинг:**
   - `POST /api/agent/hb` каждые 10 секунд с `Authorization: Bearer <token>`.
   - Тело: `{"locked", "lock_reason", "idle_seconds", "active_user", "events"}`.
   - Ответ: `{"commands": [...], "server_time": "..."}`.
   - Автореконнект при ошибках (exponential backoff, максимум 30 с).
   - События накапливаются между hb и отправляются массивом `events`.

2. **Heartbeat каждые 10 с:**
   - `active_user` — имя пользователя Windows (`os.getlogin()` или `ctypes`).
   - `locked` — фактически показан экран блокировки.
   - `idle_seconds` — секунды с последнего ввода через `GetLastInputInfo` (ctypes).
   - `events` — массив накопленных событий (опустошается после отправки).

3. **Команды из ответа сервера:**
   - `lock_now {reason, info}` — немедленно показать BSOD; идемпотентно
     (повторный `lock_now` = no-op + event `locked`);
   - `lock_in {delay_seconds, reason, info}` — тост с обратным отсчётом,
     event `warning_shown`, по истечении — блокировка + event `locked`;
   - `unlock` — скрыть экран, event `unlocked`;
   - `add_time {minutes}` — ТОЛЬКО тост «+N минут», агент не разблокирует сам.

4. **Экран блокировки (BSOD-стиль, PySide6):**
   - Полноэкранный, без рамки, topmost, поверх таскбара; `WS_EX_TOOLWINDOW`
     (скрыт из Alt+Tab/таскбара); игнор `WM_CLOSE`/`Alt+F4`.
   - LL-хук в блокировке глотает: Win, Alt+Tab, Win+D, Ctrl+Esc, Alt+F4.
   - Контент рисуется из блока `info` (сервер считает — агент рисует):
     «:(», `reason_ru`, `display_name`, текущие время/дата, «Разблокировка: …»
     (`unlocks_at`/`next_window`), стоп-код `CURFEW_DAILY_LIMIT` / `CURFEW_SCHEDULE` /
     `CURFEW_MANUAL`, «попроси родителей добавить времени 🙂».
     Если `info` нет — аккуратные дефолтные тексты.
   - Шрифты системные: Segoe UI Light («:(», заголовки), Segoe UI (текст),
     Consolas (стоп-код). Весь текст на русском.
   - HTTP-обработка не должна замирать, пока экран показан.

5. **Аварийный доступ:**
   - Ctrl+Alt+Shift+F12 (ловится хуком даже в блокировке) → topmost PIN-пад.
   - `pin_hash` в `config.ini`; если пуст — при первом запуске попросить создать
     PIN (и записать хеш).
   - Верный PIN → меню «Разблокировать / Закрыть агент / Отмена».
   - Неверный → тихо закрыть + event `pin_attempt`.

6. **Fail-open:** нет связи с сервером `fail_open_hours` подряд (дефолт 12, 0=выкл)
   и агент заблокирован → разблокировать + event `fail_open`.

7. **Тосты:** маленькие topmost-окна в углу с `WS_EX_NOACTIVATE` (не крадут фокус):
   отсчёт `lock_in`, «+N минут», «Разблокировано». Авто-скрытие.

8. **Логирование:** rotating-файл рядом с exe + консоль. Логировать команды,
   события, ошибки HTTP.

## События (отправляются в массиве `events`)

| Событие | Когда отправлять |
| --- | --- |
| `agent_started` | При старте агента |
| `agent_stopped` | При корректной остановке |
| `locked` | После фактической блокировки (lock_now / lock_in) |
| `unlocked` | После фактической разблокировки |
| `warning_shown` | Показан тост lock_in с обратным отсчётом |
| `pin_attempt` | Неверный PIN на секретной комбинации |
| `fail_open` | Автоматическая разблокировка при потере связи |

Формат: `{"event": "...", "reason": "..."}` — `reason` опционален.

## НЕ делать в v1

watchdog-пара, Windows-служба, `BlockInput`, скрытие процесса, групповые
политики, автообновление.

## Порядок работы

1. Сначала предложи структуру репозитория и разбивку по модулям:
   ```
   config.py        — загрузка config.ini
   http_client.py   — HTTP-поллинг, отправка hb, приём команд
   idle.py          — GetLastInputInfo через ctypes
   hooks.py         — LL-хук клавиатуры
   ui/lock_screen.py — BSOD-экран
   ui/toast.py      — тосты
   ui/pin_dialog.py — PIN-пад
   events.py        — очередь событий
   main.py          — точка входа, инициализация Qt + потоки
   ```
   Дождись согласия.

2. Затем реализуй шагами:
   - **(a)** Конфиг + HTTP-клиент + hb/events (без UI). Проверка: curl к серверу.
   - **(b)** Тосты. Проверка: trigger из CLI.
   - **(c)** BSOD + LL-хук. Проверка: ручная блокировка через `lock_now`.
   - **(d)** PIN + fail-open. Проверка: отключить сервер, ждать fail_open_hours.
   - **(e)** Упаковка PyInstaller + установочный скрипт:
     ```batch
     schtasks /create /sc onlogon /rl HIGHEST /tn "desk-curfew" /tr "\"C:\path\to\agent.exe\""
     ```
     Проверка `EnableLUA=1` перед установкой.

   Каждый шаг — полные файлы + как проверить (в идеале без детского ПК:
   локальная проверка на своей машине, curl/Postman для имитации сервера).

3. Учти:
   - ПК ребёнка может перезагружаться — после старта агент просто шлёт hb,
     сервер сам дошлёт `lock` в ответе, если должен.
   - Агент должен быть идемпотентным: повторный `lock_now` в заблокированном
     состоянии — no-op + event `locked`.
   - `idle_seconds` — это секунды без ввода от `GetLastInputInfo`, НЕ порог.
     Порог (120 с) проверяется на сервере.

## Тестирование без реального сервера

Для локальной разработки можно использовать mock-сервер:

```python
# mock_server.py — минимальный FastAPI-сервер для тестов
from fastapi import FastAPI, Header
from pydantic import BaseModel

app = FastAPI()

class Heartbeat(BaseModel):
    locked: bool = False
    lock_reason: str | None = None
    idle_seconds: int = 0
    active_user: str | None = None
    events: list = []

@app.post("/api/agent/hb")
async def heartbeat(payload: Heartbeat, authorization: str = Header(None)):
    print(f"HB: {payload}, auth: {authorization}")
    return {
        "commands": [],  # можно добавить команды для теста
        "server_time": "2026-10-06T12:00:00+00:00"
    }
```

Запуск: `uvicorn mock_server:app --host 0.0.0.0 --port 8000`

## Документация

- **HTTP-протокол:** `docs/agent_http_protocol.md` — формат hb, авторизация, команды
- **Семантика:** `docs/agent_protocol.md` — поведение команд, `info` блок, события
- **Сервер:** `README.md` — как запустить сервер, веб-интерфейс
