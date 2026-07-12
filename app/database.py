from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum, String, Text, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app.config import settings


class ListingStatus(str, enum.Enum):
    NEW = "new"
    NEED_CALL = "need_call"
    NO_ANSWER = "no_answer"
    WAITLIST = "waitlist"
    IN_TALK = "in_talk"
    REJECTED = "rejected"
    BOUGHT = "bought"


STATUS_LABELS = {
    ListingStatus.NEW: "جدید",
    ListingStatus.NEED_CALL: "تماس بگیر",
    ListingStatus.NO_ANSWER: "جواب نداد",
    ListingStatus.WAITLIST: "لیست انتظار",
    ListingStatus.IN_TALK: "در حال پیگیری",
    ListingStatus.REJECTED: "رد شد",
    ListingStatus.BOUGHT: "خریدم",
}

KANBAN_ORDER = [
    ListingStatus.NEW,
    ListingStatus.NEED_CALL,
    ListingStatus.NO_ANSWER,
    ListingStatus.WAITLIST,
    ListingStatus.IN_TALK,
    ListingStatus.REJECTED,
    ListingStatus.BOUGHT,
]


class Base(DeclarativeBase):
    pass


class Listing(Base):
    __tablename__ = "listings"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(String(500), unique=True)
    title: Mapped[str] = mapped_column(String(300), default="Untitled listing")
    image_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    price: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    location: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    specs_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    images_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tags_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[ListingStatus] = mapped_column(
        Enum(ListingStatus, native_enum=False, length=32),
        default=ListingStatus.NEW,
    )
    source_chat_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    last_called_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


connect_args: dict = {}
engine_kwargs: dict = {}
_db_url = settings.resolved_database_url
if _db_url.startswith("sqlite"):
    connect_args["check_same_thread"] = False
else:
    engine_kwargs["pool_pre_ping"] = True
    engine_kwargs["pool_recycle"] = 280

engine = create_engine(_db_url, connect_args=connect_args, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _migrate_listing_columns()


def _migrate_listing_columns() -> None:
    inspector = inspect(engine)
    if "listings" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("listings")}
    with engine.begin() as conn:
        if "image_url" not in columns:
            conn.execute(text("ALTER TABLE listings ADD COLUMN image_url VARCHAR(500)"))
        if "price" not in columns:
            conn.execute(text("ALTER TABLE listings ADD COLUMN price VARCHAR(120)"))
        if "location" not in columns:
            conn.execute(text("ALTER TABLE listings ADD COLUMN location VARCHAR(200)"))
        if "description" not in columns:
            conn.execute(text("ALTER TABLE listings ADD COLUMN description TEXT"))
        if "specs_json" not in columns:
            conn.execute(text("ALTER TABLE listings ADD COLUMN specs_json TEXT"))
        if "images_json" not in columns:
            conn.execute(text("ALTER TABLE listings ADD COLUMN images_json TEXT"))
        if "tags_json" not in columns:
            conn.execute(text("ALTER TABLE listings ADD COLUMN tags_json TEXT"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
