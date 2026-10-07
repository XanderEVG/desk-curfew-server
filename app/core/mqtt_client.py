"""MQTT-клиент для внешних интеграций.

Отвечает ТОЛЬКО за:
  - публикацию `server/status` (retained, обновление каждые 60 с);
  - подписку на `server/cmd` (внешнее управление: lock/unlock/add_time).

Агенты общаются с сервером по HTTP (`/api/agent/hb`), транспорт для команд —
`AgentTransport` (in-memory очередь).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import suppress
from datetime import UTC, datetime

import paho.mqtt.client as mqtt

from app.config import Settings
from app.core import pc_service
from app.core.agent_transport import AgentTransport
from app.database import AsyncSessionLocal

logger = logging.getLogger(__name__)


class MqttService:
    def __init__(self, settings: Settings, transport: AgentTransport):
        self.settings = settings
        self.transport = transport
        self.loop: asyncio.AbstractEventLoop | None = None
        self._publish_lock = asyncio.Lock()
        self._last_publish = 0.0
        self._status_task: asyncio.Task | None = None
        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=settings.mqtt_client_id,
            transport=settings.mqtt_transport,
        )
        if settings.mqtt_path:
            self.client.ws_set_options(path=settings.mqtt_path)
        self.client.username_pw_set(
            settings.mqtt_username,
            settings.mqtt_password,
        )
        if settings.mqtt_use_ssl:
            self.client.tls_set()
        self.client.will_set(
            self._server_status_topic(),
            json.dumps({"online": False}),
            qos=1,
            retain=True,
        )
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect

    def _server_status_topic(self) -> str:
        return f"{self.settings.mqtt_topic_prefix}/server/status"

    def _server_cmd_topic(self) -> str:
        return f"{self.settings.mqtt_topic_prefix}/server/cmd"

    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.client.connect_async(
            self.settings.mqtt_host,
            self.settings.mqtt_port,
            keepalive=self.settings.mqtt_keepalive,
        )
        self.client.loop_start()
        self._status_task = asyncio.create_task(self._status_loop())
        logger.info(
            "MQTT client started: %s:%s",
            self.settings.mqtt_host,
            self.settings.mqtt_port,
        )

    async def stop(self) -> None:
        if self._status_task is not None:
            self._status_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._status_task
            self._status_task = None
        try:
            await self._publish_server_status(False)
        except Exception:
            logger.exception("Failed to publish offline status")
        self.client.loop_stop()
        self.client.disconnect()
        logger.info("MQTT client stopped")

    async def _status_loop(self) -> None:
        """Раз в 60 сек обновляет retained-статус, чтобы внешние
        приложения видели актуальную сводку по ПК."""
        while True:
            await asyncio.sleep(60)
            try:
                await self._publish_server_status(True)
            except Exception:
                logger.exception("Failed to refresh server status")

    def _schedule_coroutine(self, coro) -> None:
        if self.loop is not None:
            asyncio.run_coroutine_threadsafe(coro, self.loop)

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            logger.error("MQTT connection failed: %s", reason_code)
            return
        client.subscribe(self._server_cmd_topic(), qos=1)
        logger.info("Subscribed to %s", self._server_cmd_topic())
        self._schedule_coroutine(self._publish_server_status(True))

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        logger.warning("MQTT disconnected: %s", reason_code)

    def _on_message(self, client, userdata, msg) -> None:
        if self.loop is None:
            return
        self._schedule_coroutine(self._handle_message(msg.topic, msg.payload))

    async def _handle_message(self, topic: str, payload_bytes: bytes) -> None:
        try:
            payload_preview = payload_bytes.decode("utf-8")
        except UnicodeDecodeError:
            payload_preview = repr(payload_bytes)
        logger.debug("MQTT <<< %s: %s", topic, payload_preview)

        try:
            payload = json.loads(payload_bytes.decode("utf-8"))
        except Exception:
            logger.warning("Invalid JSON payload from topic %s", topic)
            return

        if topic == self._server_cmd_topic():
            async with AsyncSessionLocal() as session:
                try:
                    await pc_service.handle_server_command(session, self.transport, self.settings, payload)
                    await session.commit()
                except Exception:
                    await session.rollback()
                    logger.exception("Failed to handle server command")
            return

        logger.debug("Ignoring unexpected topic %s", topic)

    async def _publish_server_status(self, online: bool = True) -> None:
        if online:
            async with AsyncSessionLocal() as session:
                payload = await pc_service.build_server_status(session, self.settings)
        else:
            payload = {"online": False, "ts": datetime.now(UTC).isoformat()}
        await self.publish_json(self._server_status_topic(), payload, retain=True)

    async def publish_json(
        self,
        topic: str,
        payload: dict,
        qos: int = 1,
        retain: bool = False,
    ) -> None:
        """Публикация с учётом rate limit для брокера."""
        logger.debug("MQTT >>> %s: %s", topic, json.dumps(payload, ensure_ascii=False))
        async with self._publish_lock:
            now = time.monotonic()
            wait = self.settings.mqtt_publish_delay - (now - self._last_publish)
            if wait > 0:
                await asyncio.sleep(wait)
            message = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            await asyncio.to_thread(
                self.client.publish,
                topic,
                message,
                qos=qos,
                retain=retain,
            )
            self._last_publish = time.monotonic()
