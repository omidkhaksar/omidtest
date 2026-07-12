from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.database import Listing, ListingStatus
from app.divar import dumps_json, extract_url, is_divar_url

__all__ = ["extract_url", "is_divar_url", "create_listing", "get_listing_by_url", "update_listing_status"]


def create_listing(
    db: Session,
    *,
    url: str,
    title: str = "Untitled listing",
    image_url: Optional[str] = None,
    price: Optional[str] = None,
    location: Optional[str] = None,
    description: Optional[str] = None,
    specs: Optional[dict[str, str]] = None,
    images: Optional[list[str]] = None,
    tags: Optional[list[str]] = None,
    notes: str = "",
    source_chat_id: Optional[str] = None,
) -> Listing:
    listing = Listing(
        url=url,
        title=title,
        image_url=image_url,
        price=price,
        location=location,
        description=description,
        specs_json=dumps_json(specs or {}),
        images_json=dumps_json(images or []),
        tags_json=dumps_json(tags or []),
        notes=notes,
        source_chat_id=source_chat_id,
        status=ListingStatus.NEW,
    )
    db.add(listing)
    db.commit()
    db.refresh(listing)
    return listing


def get_listing_by_url(db: Session, url: str) -> Optional[Listing]:
    return db.query(Listing).filter(Listing.url == url).first()


def find_listing(db: Session, query: str) -> Optional[Listing]:
    query = query.strip()
    if not query:
        return None
    if query.isdigit():
        return db.query(Listing).filter(Listing.id == int(query)).first()

    url = extract_url(query)
    if url:
        listing = get_listing_by_url(db, url)
        if listing:
            return listing

    token = query.rstrip("/").split("/")[-1]
    if token:
        listing = (
            db.query(Listing)
            .filter(Listing.url.contains(token))
            .order_by(Listing.updated_at.desc())
            .first()
        )
        if listing:
            return listing

    return (
        db.query(Listing)
        .filter(Listing.title.contains(query))
        .order_by(Listing.updated_at.desc())
        .first()
    )


def list_listings(
    db: Session,
    *,
    status: Optional[ListingStatus] = None,
    limit: int = 10,
) -> list[Listing]:
    q = db.query(Listing)
    if status:
        q = q.filter(Listing.status == status)
    return q.order_by(Listing.updated_at.desc()).limit(limit).all()


def update_listing_status(
    db: Session,
    listing_id: int,
    status: ListingStatus,
    *,
    notes: Optional[str] = None,
    mark_called: bool = False,
) -> Optional[Listing]:
    listing = db.query(Listing).filter(Listing.id == listing_id).first()
    if not listing:
        return None
    listing.status = status
    if notes is not None:
        listing.notes = notes
    if mark_called or status in {ListingStatus.NO_ANSWER, ListingStatus.IN_TALK}:
        listing.last_called_at = datetime.utcnow()
    listing.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(listing)
    return listing
