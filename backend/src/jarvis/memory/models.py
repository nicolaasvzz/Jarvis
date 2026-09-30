"""Typed records returned by the Memory store."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class Message(BaseModel):
    """One turn of conversation held in memory."""

    role: str
    content: str
    created_at: datetime


class Preference(BaseModel):
    """A learned key/value setting (e.g. ``photos_folder`` → ``D:/Photos``)."""

    key: str
    value: str
    updated_at: datetime


class Fact(BaseModel):
    """A remembered note, optionally tagged for retrieval."""

    id: int
    content: str
    tag: str | None
    created_at: datetime
