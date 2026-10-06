"""Транспорт для доставки команд агентам.

Сейчас — in-memory очередь: команды кладутся при lock_pc/unlock_pc/add_time,
выдаются при heartbeat агента через `drain`. При рестарте сервера непустая
очередь теряется — это ок, планировщик и resend по hb сами восстановят
состояние (desired_locked в БД, таймаут 120 с).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

logger = logging.getLogger(__name__)


class AgentTransport(Protocol):
    """Канал доставки команд агенту.

    `send_pc_command` — поставить команду в очередь (вызывается из pc_service).
    `drain` — забрать накопленные команды для ответа на heartbeat агента.
    """

    async def send_pc_command(self, pc_name: str, command: dict) -> None:
        ...

    def drain(self, pc_name: str) -> list[dict]:
        ...


class InMemoryAgentTransport:
    """Простейший транспорт: dict[pc_name, list[command]] + asyncio.Lock."""

    def __init__(self) -> None:
        self._queues: dict[str, list[dict]] = {}
        self._lock = asyncio.Lock()

    async def send_pc_command(self, pc_name: str, command: dict) -> None:
        async with self._lock:
            self._queues.setdefault(pc_name, []).append(command)
        logger.info("Enqueued command for %s: %s", pc_name, command.get("action"))

    def drain(self, pc_name: str) -> list[dict]:
        return self._queues.pop(pc_name, [])
