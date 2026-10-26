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
    # Off by default: tiling runs one model inference per region.
    tiles: bool = False


class VideoUrlRequest(BaseModel):
    url: str = Field(min_length=8, max_length=4_000)


class EditorialSourceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=8, max_length=2_000)
    tier: Literal["fact_check", "official", "research", "international", "news"]
    language: Literal["mn", "en", "mn,en", "other"] = "mn"
    note: str = Field(default="", max_length=600)
    added_by: str = Field(default="", max_length=100)


class EditorialSourceReviewRequest(BaseModel):
    reviewer: str = Field(min_length=1, max_length=100)
    note: str = Field(default="", max_length=600)


class EditorialReviewRequest(BaseModel):
    claim: str = Field(min_length=3, max_length=10_000)
    reviewer: str = Field(min_length=1, max_length=100)
    verdict: Literal[
        "True", "False", "Partly true", "Misleading", "Unproven",
        "Satire", "Fabricated", "Other",
    ]
    rationale: str = Field(min_length=10, max_length=4_000)
    source_ids: list[str] = Field(default_factory=list, max_length=10)
    evidence_urls: list[str] = Field(default_factory=list, max_length=10)
