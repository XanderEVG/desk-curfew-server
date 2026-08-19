from fastapi import Request


def get_mqtt_service(request: Request):
    return request.app.state.mqtt