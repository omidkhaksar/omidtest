from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.database import KANBAN_ORDER, ListingStatus, STATUS_LABELS
from app.divar import loads_json


class ListingCreate(BaseModel):
    url: str
    title: Optional[str] = None
    notes: str = ""
    source_chat_id: Optional[str] = None


class ListingUpdate(BaseModel):
    title: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[ListingStatus] = None


class ListingOut(BaseModel):
    id: int
    url: str
    title: str
    image_url: Optional[str] = None
    price: Optional[str] = None
    location: Optional[str] = None
    description: Optional[str] = None
    specs: dict[str, str] = Field(default_factory=dict)
    images: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    notes: str
    status: ListingStatus
    status_label: str
    source_chat_id: Optional[str]
    created_at: datetime
    updated_at: datetime
    last_called_at: Optional[datetime]

    class Config:
        from_attributes = True


class KanbanColumn(BaseModel):
    status: ListingStatus
    label: str
    listings: list[ListingOut]


class KanbanBoard(BaseModel):
    columns: list[KanbanColumn]
    total: int


def listing_to_out(listing) -> ListingOut:
    return ListingOut(
        id=listing.id,
        url=listing.url,
        title=listing.title,
        image_url=listing.image_url,
        price=listing.price,
        location=listing.location,
        description=listing.description,
        specs=loads_json(listing.specs_json, {}),
        images=loads_json(listing.images_json, []),
        tags=loads_json(listing.tags_json, []),
        notes=listing.notes,
        status=listing.status,
        status_label=STATUS_LABELS[listing.status],
        source_chat_id=listing.source_chat_id,
        created_at=listing.created_at,
        updated_at=listing.updated_at,
        last_called_at=listing.last_called_at,
    )


class ListingPreview(BaseModel):
    url: str
    title: Optional[str] = None
    image_url: Optional[str] = None
    price: Optional[str] = None
    location: Optional[str] = None
    description: Optional[str] = None
    specs: dict[str, str] = Field(default_factory=dict)
    images: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    district: Optional[str] = None
    city: Optional[str] = None


class StatusMove(BaseModel):
    status: ListingStatus


class BoardMeta(BaseModel):
    statuses: list[dict[str, str]] = Field(
        default_factory=lambda: [
            {"value": s.value, "label": STATUS_LABELS[s]} for s in KANBAN_ORDER
        ]
    )
