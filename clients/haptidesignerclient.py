from generic import WebsocketClient
from inputs.borasvest import FrameConverter

class HaptiDesignerClient(WebsocketClient):

    async def connect(self, uri="wss://multiplexer.haptics.catdad.nl"):
        await super().connect(uri)
        for message in self.listen():
            await self.structure_message(message)

    async def structure_message(self, input: str):
        return FrameConverter(input)._raw