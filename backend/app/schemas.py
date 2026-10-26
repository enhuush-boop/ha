from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

InputLanguage = Literal["auto", "mn_cyrl", "mn_latn", "en"]


class FactCheckRequest(BaseModel):
    claim: str = Field(min_length=3, max_length=10_000)
    language: InputLanguage = "auto"


class TextRequest(BaseModel):
    text: str = Field(min_length=20, max_length=100_000)
    language: InputLanguage = "auto"


class UrlRequest(BaseModel):
    url: str = Field(min_length=4, max_length=4_000)


class ImageUrlRequest(BaseModel):
    url: str = Field(min_length=8, max_length=4_000)
