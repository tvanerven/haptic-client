import asyncio
import websockets
import json
import serial
import time
from asyncio import sleep
from websockets.sync.client import connect


class FrameConverter:

    def __init__(self, data: list):
        self._data = data
        self.device = self.get_serial_device(port='/dev/ttyACM0', baudrate=9600)
        self.send_serial_data(self.device, self._data)
        super().__init__()

    def get_serial_device(self, port, baudrate=9600):
        ser = serial.Serial(port, baudrate, timeout=1)  # Set a timeout for reading
        if not ser.is_open:
            ser.open()
        return ser

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
                print(f"Sleeping for {item} ms")