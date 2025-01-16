from typing import Optional
from pydantic import BaseModel, Field


class SpatKeyFrame(BaseModel):
    annotation: Optional[str]
    timestamp: float = 0.0
    weights: list[int] = Field(min_length=20, max_length=20)


class Sample(BaseModel):
    annotation: Optional[str]
    timestamp: float = 0.0
    signalIndex: int = 0 
    speed: float = 1.0
    repeatCount: int = 1
    preDelay: float = 0.0
    postDelay: float = 0.0
    maxDuration: float = 1.0
    repeatSpat: bool = False
    spatKeyframes: list[SpatKeyFrame]


class Track(BaseModel):
    name: str
    volume: int = 100
    samples: list[Sample]


class Output(BaseModel):
    tracks: list[Track]