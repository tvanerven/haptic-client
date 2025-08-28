import asyncio
import websockets
import json
import serial
import time
from asyncio import sleep
from websockets.sync.client import connect
from inputs.image_processor import ImageProcessorInput, HapticProcessorInput


class ColorConverter:

    def __init__(self, colordata: dict):
        self.color = colordata
        print("color received: ", self.color)
        self._data = []
        self._rawstring = ""
        self._parse_colors()
        print("Converted to following: ", self._data)
        self.device = self.get_serial_device(port='/dev/ttyACM0', baudrate=9600)
        self.send_serial_data(self.device, self._data)
        super().__init__()

    def get_serial_device(self, port, baudrate=9600):
        ser = serial.Serial(port, baudrate, timeout=1)  # Set a timeout for reading
        if not ser.is_open:
            ser.open()
        return ser
    
    def _parse_colors(self):
        print("Color: ", self.color)
        # color = ImageProcessorInput(color)

        self._data.append(f"[L,0-15:{self.color['intensity']}]")
        self._data.append(160)

    def send_serial_data(self, serial_device, data):
        for item in data:
            if isinstance(item, str):
                try:
                    encoded_data = item.encode('utf-8')
                    max_packet_size = 64  # Typical max packet size for USB serial communication
                    for i in range(0, len(encoded_data), max_packet_size):
                        chunk = encoded_data[i:i + max_packet_size]
                        print(f"Sending chunk: {chunk}")
                        serial_device.write(chunk)
                        serial_device.flush()  # Ensure the data is sent
                        time.sleep(0.1)  # Add a small delay between chunks
                    print("Data sent successfully")
                except serial.SerialTimeoutException as e:
                    print(f"Error sending data: {e}")
            if isinstance(item, int):
                time.sleep(item / 1000)  # Convert milliseconds to seconds
                stop_instruction = "[L,all:0]".encode('utf-8')
                serial_device.write(stop_instruction)
                serial_device.flush()
                time.sleep(0.1)
                print("Stop instruction sent successfully")

from starlette.websockets import WebSocket, WebSocketDisconnect
import asyncio

def websocket_client():
    websocket_url = "ws://localhost:8000/ws/listen/test"  # Change this URL to your WebSocket server
    try:
        with connect(websocket_url) as websocket:
            while True:
                data = websocket.recv()
                print(data)
                json_data = json.loads(data)
                ColorConverter(colordata=json_data)
    except WebSocketDisconnect:
        print("WebSocket server disconnected")

def main():
    websocket_client()

if __name__ == "__main__":
    asyncio.run(main())

