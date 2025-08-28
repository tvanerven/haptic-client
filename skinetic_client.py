import asyncio
from time import sleep
from clients.spnclient_haptidesigner import SPNClient
from starlette.websockets import WebSocketDisconnect
from skinetic.skineticSDK import Skinetic


async def main():
    websocket_url = "ws://localhost:8000/ws/listen/hotrod"  # Change this URL to your WebSocket server
    skinetic = Skinetic()
    skinetic.connect(output_type=Skinetic.OutputType.USB)
    client = SPNClient(skinetic, websocket_url)

    try:
        async with client:  # Use the context manager to handle connection setup and teardown
            async for message in client.listen():  # Listen for messages in a loop
                #print(f"Received message: {message}")
                await client.process_messages(message)  # Process each received message
    except WebSocketDisconnect:
        print("WebSocket disconnected.")
    finally:
        print("Shutting down client.")


if __name__ == "__main__":
    asyncio.run(main())