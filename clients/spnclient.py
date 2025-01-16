from generic import WebsocketClient
from skinetic.skineticSDK import Skinetic

class SPNClient(WebsocketClient):

    def __init__(self, skinetic: Skinetic):
        self.skinetic = skinetic
        super().__init__()
    
    async def connect(self, uri="wss://multiplexer.haptics.catdad.nl"):
        await super().connect(uri)
        
    async def process_messages(self):
        async for message in self.websocket.listen():
            converted_message = await self.structure_message(message)
            await self.send_to_device(converted_message)

    async def structure_message(self, input: ImageProcessorInput):
        return input.format()
    
    async def send_to_device(self, message):
        if self.skinetic.ConnectionState == self.skinetic.ConnectionState.Connected:
            pattern_id = self.skinetic.load_pattern_json(message)
            self.skinetic.play_effect(pattern_id)
        




