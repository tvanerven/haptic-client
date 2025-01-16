from asyncio import sleep
from websockets.sync.client import connect

class WebsocketClient:
    async def connect(self, uri):
        self.websocket = await connect(uri)

    async def send(self, message):
        await self.websocket.send(message)

    async def receive(self):
        return await self.websocket.recv()
    
    async def disconnect(self):
        await self.websocket.close()

    async def listen(self):
        while True:
            message = await self.receive()
            yield message

    async def structure_message(self, input):
        raise NotImplementedError("This method should be implemented in a subclass")
    
    async def send_to_device(self, message):
        raise NotImplementedError("This method should be implemented in a subclass")