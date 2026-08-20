from fastapi import FastAPI

from app.config import Settings
from app.core.mqtt_client import MqttService


class CurfewApp(FastAPI):
    mqtt: MqttService
    settings: Settings
