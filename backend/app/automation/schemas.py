from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SeriesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    brief: str = Field(min_length=8, max_length=8000)
    model: str | None = Field(default=None, max_length=160)
    timezone: str = Field(default="UTC", min_length=1, max_length=64)
    publish_times: list[str] = Field(default_factory=lambda: ["09:00"], min_length=1, max_length=3)
    enabled: bool = True
    publish_mode: Literal["review", "auto"] = "review"
    privacy: Literal["private", "unlisted", "public"] = "public"
    language: str = Field(default="en", min_length=2, max_length=16)
    voice: str = Field(default="en-US-AriaNeural", min_length=2, max_length=80)
    made_for_kids: bool = False
    contains_synthetic_media: bool = False

    @field_validator("timezone")
    @classmethod
    def timezone_exists(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value

    @field_validator("publish_times")
    @classmethod
    def valid_times(cls, values: list[str]) -> list[str]:
        if not values or len(values) > 3:
            raise ValueError("publish_times must contain between 1 and 3 times")
        if any(len(value) != 5 or value[2] != ":" or not value[:2].isdigit() or not value[3:].isdigit() for value in values):
            raise ValueError("publish times must use HH:MM")
        if any(int(value[:2]) > 23 or int(value[3:]) > 59 for value in values):
            raise ValueError("publish times must use HH:MM")
        if len(set(values)) != len(values):
            raise ValueError("publish_times must be distinct")
        return sorted(values)


class SeriesPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    brief: str | None = Field(default=None, min_length=8, max_length=8000)
    model: str | None = Field(default=None, max_length=160)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    publish_times: list[str] | None = Field(default=None, min_length=1, max_length=3)
    enabled: bool | None = None
    publish_mode: Literal["review", "auto"] | None = None
    privacy: Literal["private", "unlisted", "public"] | None = None
    language: str | None = Field(default=None, min_length=2, max_length=16)
    voice: str | None = Field(default=None, min_length=2, max_length=80)
    made_for_kids: bool | None = None
    contains_synthetic_media: bool | None = None

    @field_validator("timezone")
    @classmethod
    def timezone_exists(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ValueError("timezone must be a valid IANA timezone") from exc
        return value

    @field_validator("publish_times")
    @classmethod
    def valid_times(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        return SeriesInput.valid_times(values)

    @model_validator(mode="after")
    def reject_null_required_fields(self):
        for field in (
            "name",
            "brief",
            "timezone",
            "publish_times",
            "enabled",
            "publish_mode",
            "privacy",
            "language",
            "voice",
            "made_for_kids",
            "contains_synthetic_media",
        ):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class EventOut(BaseModel):
    type: str
    message: str
    created_at: datetime


class RunOut(BaseModel):
    id: str
    series_id: str
    status: str
    stage: str
    title: str | None = None
    description: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    youtube_video_id: str | None = None
    youtube_url: str | None = None
    media_url: str | None = None
    events: list[EventOut] | None = None
    manifest: dict | None = None


class ManifestScene(BaseModel):
    model_config = ConfigDict(extra="forbid")

    narration: str = Field(min_length=8, max_length=1200)
    visual: str = Field(min_length=4, max_length=600)
    caption: str | None = Field(default=None, max_length=180)
    palette: str | None = Field(default=None, max_length=80)
    setting: Literal["town", "forest", "space", "ocean", "room"] | None = None
    action: Literal["walk", "run", "discover", "talk", "celebrate", "escape", "wait"] | None = None
    emotion: Literal["happy", "worried", "surprised", "determined"] | None = None
    prop: Literal["clock", "key", "door", "star", "bridge", "none"] | None = None


class VideoManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=5000)
    scenes: list[ManifestScene] = Field(min_length=5, max_length=8)
    tags: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def narration_length(self):
        count = sum(len(scene.narration.split()) for scene in self.scenes)
        if not 80 <= count <= 130:
            raise ValueError("the complete narration must contain 80 to 130 words")
        return self
