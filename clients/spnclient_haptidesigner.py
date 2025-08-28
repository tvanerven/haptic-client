import json
from clients.generic import WebsocketClient
from inputs.image_processor import HapticProcessorInput
from inputs.haptidesigner import FrameConverter
from outputs.schemas import Output
from skinetic.skineticSDK import Skinetic


class SPNClient(WebsocketClient):
    def __init__(self, skinetic: Skinetic, uri="ws:/localhost:8000/ws/listen/blackrod"):
        self.skinetic = skinetic
        self.uri = uri
        super().__init__(uri)

    async def connect(self, uri):
        async with super().connect(uri):
            print("Connected to WebSocket server.")
            for message in self.listen():
                await self.process_messages()
        
    async def process_messages(self, message):
        frames = FrameConverter(json.loads(message))._skinetic
        message = HapticProcessorInput(frame_list=frames)
        converted_message: Output = await self.structure_message(message)
        await self.send_to_device(converted_message)

    async def structure_message(self, input: HapticProcessorInput) -> Output:
        return input.format()
    
    async def send_to_device(self, message: Output):
        if self.skinetic.get_connection_state() == self.skinetic.ConnectionState.Connected:
            print("Message: ", message.model_dump_json())
            pattern_id = self.skinetic.load_pattern_json(message.model_dump_json())
            self.skinetic.play_effect(pattern_id)
            self.skinetic.unload_pattern(pattern_id)
        else:
            print(message.model_dump())

