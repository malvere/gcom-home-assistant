"""Typed representations of the observed API fields."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True, slots=True)
class Tokens:
    access_token: str = field(repr=False)
    refresh_token: str | None = field(default=None, repr=False)
    token_type: str = "Bearer"
    expires_at: datetime | None = field(default=None, repr=False)

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class Device:
    id: str
    name: str = field(repr=False)
    type: str | None = None
    description: str | None = field(default=None, repr=False)
    entrance_ids: tuple[str, ...] = field(default=(), repr=False)


@dataclass(frozen=True, slots=True)
class Entrance:
    id: str
    name: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class EntranceGroup:
    id: str
    name: str = field(repr=False)
    entrances: tuple[Entrance, ...] = field(default=(), repr=False)


@dataclass(frozen=True, slots=True)
class Facility:
    id: str
    name: str = field(repr=False)
    building_id: str | None = None
    floor: str | None = field(default=None, repr=False)
    extension: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class AccessCode:
    id: str
    code: str = field(repr=False)
    expired: str | None = field(default=None, repr=False)
    facility_id: str | None = None
