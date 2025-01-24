from asyncio import sleep
from websockets import connect

class WebsocketClient:
    def __init__(self, uri):
        self.uri = uri
        self.websocket = None

    async def __aenter__(self):
        self.websocket = await connect(self.uri)
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        if self.websocket:
            await self.websocket.close()
            self.websocket = None

    async def send(self, message):
        if self.websocket:
            await self.websocket.send(message)

    async def receive(self):
        if self.websocket:
            return await self.websocket.recv()

    async def listen(self):
        while True:
            message = await self.receive()
            yield message

    async def structure_message(self, input):
        raise NotImplementedError("This method should be implemented in a subclass")
    
    async def send_to_device(self, message):
        raise NotImplementedError("This method should be implemented in a subclass")