from fastapi import Request

from app.core.mqtt_client import MqttService


def get_mqtt_service(request: Request) -> MqttService:
    return request.app.mqtt
