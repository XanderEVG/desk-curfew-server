from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

from app.config import Settings
from app.core import pc_service
from app.database import AsyncSessionLocal

logger = logging.getLogger(__name__)


class MqttService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.loop: asyncio.AbstractEventLoop | None = None

        self._publish_lock = asyncio.Lock()
        self._last_publish = 0.0

        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=settings.mqtt_client_id,
        )

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

    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.client.connect_async(
            self.settings.mqtt_host,
            self.settings.mqtt_port,
            keepalive=self.settings.mqtt_keepalive,
        )
        self.client.loop_start()
        logger.info(
            "MQTT client started: %s:%s",
            self.settings.mqtt_host,
            self.settings.mqtt_port,
        )

    async def stop(self) -> None:
        try:
            await self._publish_server_status(False)
        except Exception:
            logger.exception("Failed to publish offline status")

        self.client.loop_stop()
        self.client.disconnect()
        logger.info("MQTT client stopped")

    def _schedule_coroutine(self, coro) -> None:
        if self.loop is not None:
            asyncio.run_coroutine_threadsafe(coro, self.loop)

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            logger.error("MQTT connection failed: %s", reason_code)
            return

        prefix = self.settings.mqtt_topic_prefix

        subscriptions = (
            f"{prefix}/+/hb",
            f"{prefix}/+/status",
            f"{prefix}/+/event",
            f"{prefix}/server/cmd",
        )

        for topic in subscriptions:
            client.subscribe(topic, qos=1)
            logger.info("Subscribed to %s", topic)

        self._schedule_coroutine(self._publish_server_status(True))

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        logger.warning("MQTT disconnected: %s", reason_code)

    def _on_message(self, client, userdata, msg) -> None:
        if self.loop is None:
            return

        self._schedule_coroutine(self._handle_message(msg.topic, msg.payload))

    async def _handle_message(self, topic: str, payload_bytes: bytes) -> None:
        try:
            payload = json.loads(payload_bytes.decode("utf-8"))
        except Exception:
            logger.warning("Invalid JSON payload from topic %s", topic)
            return

        prefix = self.settings.mqtt_topic_prefix
        if not topic.startswith(prefix + "/"):
            return

        suffix = topic[len(prefix) + 1:]
        parts = suffix.split("/")

        if len(parts) < 2:
            return

        pc_name = parts[0]
        kind = parts[1]

        async with AsyncSessionLocal() as session:
            try:
                if pc_name == "server" and kind == "cmd":
                    await pc_service.handle_server_command(session, self.settings, payload)
                elif kind == "hb":
                    await pc_service.handle_heartbeat(session, self.settings, pc_name, payload)
                elif kind == "status":
                    await pc_service.handle_status(session, pc_name, payload)
                elif kind == "event":
                    await pc_service.handle_event(session, pc_name, payload)

                await session.commit()
            except Exception:
                await session.rollback()
                logger.exception("Failed to handle MQTT message from %s", topic)

    async def _publish_server_status(self, online: bool) -> None:
        payload = {
            "online": online,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        await self.publish_json(self._server_status_topic(), payload, retain=True)

    async def publish_json(
        self,
        topic: str,
        payload: dict,
        qos: int = 1,
        retain: bool = False,
    ) -> None:
        """
        Публикация с учётом rate limit для брокера
        """
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

    async def send_pc_command(self, pc_name: str, command: dict) -> None:
        topic = f"{self.settings.mqtt_topic_prefix}/{pc_name}/cmd"
        await self.publish_json(topic, command)
        logger.info("Command sent to %s: %s", pc_name, command)