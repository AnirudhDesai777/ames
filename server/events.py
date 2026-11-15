"""Ames events carried by RTVI server messages."""

from pipecat.processors.frameworks.rtvi.models import ServerMessage
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection


class Events:
    def __init__(self, connection: SmallWebRTCConnection):
        self.connection = connection

    def send(self, data: dict):
        self.connection.send_app_message(ServerMessage(data=data).model_dump())

    def transcript(self, role: str, text: str, final: bool, turn_id: int, rateable=False):
        data = dict(type="transcript", role=role, text=text, final=final, turn_id=turn_id)
        self.send(data | {"rateable": True} if rateable else data)

    def speaking(self, who: str):
        self.send(dict(type="speaking", who=who))

    def notice(self, code: str, text: str):
        self.send(dict(type="notice", code=code, text=text))
