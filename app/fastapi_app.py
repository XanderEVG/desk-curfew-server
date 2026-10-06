from fastapi import FastAPI

from app.config import Settings
from app.core.agent_transport import AgentTransport
from app.core.mqtt_client import MqttService


class CurfewApp(FastAPI):
    mqtt: MqttService
    agent_transport: AgentTransport
    settings: Settings
