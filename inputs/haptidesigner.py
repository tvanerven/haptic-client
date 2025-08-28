import asyncio
import usb.core
from asyncio import sleep
from starlette.websockets import WebSocketDisconnect
from websockets.sync.client import connect


class FrameConverter:

    def __init__(self, sentence: dict):
        self.sentence = sentence
        self._frames = None 
        self._raw = []
        self._skinetic = []
        self._parse_sentence()
        super().__init__()

    def _parse_sentence(self):
        for word in self.sentence:
            self._parse_frames(self.sentence[word])
            #self._raw.append(sleep(500))

    def _parse_frames(self, word):
        for frame in word:
            for frame_nodes in frame['frame_nodes']:
                self._raw.append({
                    f'L,{frame_nodes["node_index"]}:{frame_nodes["intensity"]}'
                })
                self._skinetic.append({
                        'order': frame['order'],
                        'node_index': frame_nodes['node_index'],
                        'intensity': frame_nodes['intensity'],
                        'duration': frame['duration']
                })
            self._raw.append(int(frame['duration']))