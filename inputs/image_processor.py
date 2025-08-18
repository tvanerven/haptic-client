from pydantic import BaseModel, Field
from typing import List
from inputs import InputBaseMixin
from outputs.schemas import Output, Sample, SpatKeyFrame, Track


class Position(BaseModel):
    x: int
    y: int


class Color(BaseModel):
    r: int
    g: int
    b: int

class FrameEvent(BaseModel):
    order: int
    node_index: int
    intensity: int
    duration: int  # ms


class ImageProcessorInput(BaseModel, InputBaseMixin):
    position: Position
    color: Color

    def format(self) -> Output:
        return Output(
            tracks=[
                Track(
                    name="Image",
                    samples=[
                        Sample(
                            spatKeyframes=[
                                SpatKeyFrame(
                                    weights=[
                                        self.position.x,
                                        self.position.y,
                                        self.color.r,
                                        self.color.g,
                                        self.color.b,
                                    ] + [0 for _ in range(15)],
                                ),
                            ]
                        )
                    ]
                )
            ]
        )

class HapticProcessorInput(BaseModel):
    """
    Takes a list of frame events (order/node_index/intensity/duration) and
    converts them into your generic Output schema:

    - Each event becomes one SpatKeyFrame.
    - SpatKeyFrame.weights is length-20; we place intensity at weights[node_index].
    - Timestamps are kept at 0.0 (you can extend to accumulate real timings later).
    - We keep one Track/one Sample containing the keyframes sequence.
    """
    frame_list: List[FrameEvent] = Field(default_factory=list)

    def format(self) -> Output:
        keyframes: List[SpatKeyFrame] = []

        for ev in sorted(self.frame_list, key=lambda e: e.order):
            # Build a 20-length weight vector
            weights = [0] * 20
            if 0 <= ev.node_index < 20:
                # Clamp intensity into expected range (0..255 or 0..100 depending on your convention)
                val = max(0, min(255, int(ev.intensity)))
                weights[ev.node_index] = val

            # Timestamping: keep 0.0 for now; you can accumulate from durations if desired
            kf = SpatKeyFrame(
                annotation=f"node={ev.node_index},intensity={ev.intensity},dur={ev.duration}",
                timestamp=0.0,
                weights=weights,
            )
            keyframes.append(kf)

        sample = Sample(
            annotation="SPN sample",
            timestamp=0.0,
            signalIndex=0,
            speed=1.0,
            repeatCount=1,
            preDelay=0.0,
            postDelay=0.0,
            maxDuration=1.0,   # you can compute this from durations if your runtime uses it
            repeatSpat=False,
            spatKeyframes=keyframes,
        )

        track = Track(
            name="SPN",
            volume=100,
            samples=[sample],
        )

        return Output(tracks=[track])