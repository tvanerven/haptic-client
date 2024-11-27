import asyncio
import websockets
import json
import usb.core
from random import uniform, randint
import requests
import time
from asyncio import sleep
from websockets.sync.client import connect


class FrameConverter:

    def __init__(self, sentence: dict):
        self.sentence = sentence
        print("Sentence received: ", self.sentence)
        self._frames = None 
        self._raw = []
        self._parse_sentence()
        print("Converted to following: ", self._raw)
        #self.send_to_usb()
        super().__init__()

    def _parse_sentence(self):
        for word in self.sentence:
            self._parse_frames(self.sentence[word])
            #self._raw.append(sleep(500))

    def _parse_frames(self, word):
        for frame in word:
            for frame_nodes in frame['frame_nodes']:
                self._raw.append({
                    f'M,{frame_nodes["node_index"]}:{frame_nodes["intensity"]}'
                })
            self._raw.append(int(frame['duration']))

    def _get_usb(self):
        dev = usb.core.find(find_all=True)
        from IPython import embed; embed()
        if dev is None:
            raise ValueError("Device not found")

        dev.set_configuration()
        return dev

    def _get_interface(self, dev):
        return dev[0].interfaces()[0]

    def _get_endpoint(self, dev, interface):
        usb.util.claim_interface(dev, interface)
        return interface[0].bEndpointAddress

    def send_to_usb(self):
        device = self._get_usb()
        interface = self._get_interface(dev=device)
        endpoint = self._get_endpoint(dev=device, interface=interface)
        for data in self._raw:
            if isinstance(str, data):
                device.write(endpoint, bytes(data))
            if isinstance(int, data):
                sleep(data)
        usb.util.release_interface(device, interface)

from starlette.websockets import WebSocket, WebSocketDisconnect
import asyncio

def websocket_client():
    websocket_url = "ws://localhost:8000/ws/send/streamer/moorchyk1/secret1"  # Change this URL to your WebSocket server
    requests.post(url='http://localhost:8000/vest/pairing', params={'client': 'moorchyk1', 'secret': 'secret1'}, headers={'Authorization': 'Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ0ZXN0IiwiZXhwIjoxNzI5MjQ4OTc4fQ.pDx_skQ2VCdcNju1_PZMwk-7z_Nq_MXXmbkURewHiqo'})
    try:
        with connect(websocket_url) as websocket:
            first = uniform(1.0, 10.0)
            second = uniform(1.0, 10.0)
            intensity = randint(0,255)
            message = f"{first};{second};{intensity}"
            while True:
                data = websocket.send(message)
                time.sleep(0.001)
                first = uniform(1.0, 10.0)
                second = uniform(1.0, 10.0)
                intensity = randint(0,255)
                message = f"{first};{second};{intensity}"
    except WebSocketDisconnect:
        print("WebSocket server disconnected")

def main():
    websocket_client()

if __name__ == "__main__":
    asyncio.run(main())

