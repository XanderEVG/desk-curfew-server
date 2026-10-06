from fastapi import Request

from app.core.agent_transport import AgentTransport
from app.core.mqtt_client import MqttService


def get_mqtt_service(request: Request) -> MqttService:
    return request.app.mqtt


def get_agent_transport(request: Request) -> AgentTransport:
    return request.app.agent_transport
