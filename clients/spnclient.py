import json
from clients.generic import WebsocketClient
from inputs.image_processor import ImageProcessorInput
from outputs.schemas import Output
from skinetic.skineticSDK import Skinetic


class SPNClient(WebsocketClient):
    def __init__(self, skinetic: Skinetic, uri="wss://multiplexer.haptics.catdad.nl"):
        self.skinetic = skinetic
        super().__init__(uri)
        
    async def process_messages(self):
        async for message in self.listen():
            message = ImageProcessorInput(**json.loads(message))
            converted_message: Output = await self.structure_message(message)
            await self.send_to_device(converted_message)

    async def structure_message(self, input: ImageProcessorInput) -> Output:
        return input.format()
    
    async def send_to_device(self, message: Output):
        if self.skinetic.ConnectionState == self.skinetic.ConnectionState.Connected:
            pattern_id = self.skinetic.load_pattern_json(message.model_dump_json())
            self.skinetic.play_effect(pattern_id)
        else:
            print(message.model_dump())

