"""Тесты для InMemoryAgentTransport."""

import pytest

from app.core.agent_transport import InMemoryAgentTransport


class TestInMemoryAgentTransport:
    @pytest.mark.asyncio
    async def test_enqueue_and_drain(self):
        """Команды накапливаются и отдаются по drain."""
        transport = InMemoryAgentTransport()

        await transport.send_pc_command("pc1", {"action": "lock_now"})
        await transport.send_pc_command("pc1", {"action": "add_time", "minutes": 30})

        commands = transport.drain("pc1")
        assert len(commands) == 2
        assert commands[0]["action"] == "lock_now"
        assert commands[1]["action"] == "add_time"

    @pytest.mark.asyncio
    async def test_drain_clears_queue(self):
        """drain очищает очередь."""
        transport = InMemoryAgentTransport()

        await transport.send_pc_command("pc1", {"action": "unlock"})
        transport.drain("pc1")

        commands = transport.drain("pc1")
        assert commands == []

    @pytest.mark.asyncio
    async def test_drain_unknown_pc_returns_empty(self):
        """drain для неизвестного ПК возвращает пустой список."""
        transport = InMemoryAgentTransport()

        commands = transport.drain("unknown_pc")
        assert commands == []

    @pytest.mark.asyncio
    async def test_separate_queues_per_pc(self):
        """Очереди для разных ПК независимы."""
        transport = InMemoryAgentTransport()

        await transport.send_pc_command("pc1", {"action": "lock_now"})
        await transport.send_pc_command("pc2", {"action": "unlock"})

        commands_pc1 = transport.drain("pc1")
        commands_pc2 = transport.drain("pc2")

        assert len(commands_pc1) == 1
        assert commands_pc1[0]["action"] == "lock_now"
        assert len(commands_pc2) == 1
        assert commands_pc2[0]["action"] == "unlock"
