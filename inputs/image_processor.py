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


class ImageData(BaseModel):
    position: Position
    color: Color


class ImageProcessorInput(BaseModel, InputBaseMixin):
    image_data: ImageData
    intensity: float

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
                                        self.image_data.position.x,
                                        self.image_data.position.y,
                                        self.image_data.color.r,
                                        self.image_data.color.g,
                                        self.image_data.color.b,
                                    ] + [0 for _ in range(15)],
                                ),
                            ]
                        )
                    ]
                )
            ]
        )