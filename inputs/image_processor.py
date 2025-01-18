from pydantic import BaseModel
from inputs import InputBaseMixin
from outputs.schemas import Output, Sample, SpatKeyFrame, Track


class Position(BaseModel):
    x: int
    y: int


class Color(BaseModel):
    r: int
    g: int
    b: int


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