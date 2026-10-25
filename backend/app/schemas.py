from __future__ import annotations

from pydantic import BaseModel, Field


class FactCheckRequest(BaseModel):
    claim: str = Field(min_length=3, max_length=10_000)


class TextRequest(BaseModel):
    text: str = Field(min_length=20, max_length=100_000)


class UrlRequest(BaseModel):
    url: str = Field(min_length=4, max_length=4_000)


class ImageUrlRequest(BaseModel):
    url: str = Field(min_length=8, max_length=4_000)
