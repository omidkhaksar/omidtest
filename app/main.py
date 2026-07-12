from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session
from typing import Optional

from app.config import settings
from app.database import KANBAN_ORDER, Listing, ListingStatus, STATUS_LABELS, get_db, init_db
from app.divar import dumps_json, fetch_listing_meta, loads_json
from app.schemas import (
    BoardMeta,
    KanbanBoard,
    KanbanColumn,
    ListingCreate,
    ListingOut,
    ListingPreview,
    ListingUpdate,
    StatusMove,
    listing_to_out,
)
from app.services import (
    create_listing,
    find_listing,
    get_listing_by_url,
    list_listings as query_listings,
    update_listing_status,
)
from app.telegram_webhook import get_application, process_webhook_update, start_telegram_webhook, stop_telegram_webhook


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    await start_telegram_webhook()
    yield
    await stop_telegram_webhook()


app = FastAPI(title="House Hunt Tracker", version="1.0.0", lifespan=lifespan)


@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {
        "ok": True,
        "telegram_webhook": settings.use_webhook and get_application() is not None,
        "database": "postgres" if settings.resolved_database_url.startswith("postgresql") else "sqlite",
    }


@app.post("/api/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: Optional[str] = Header(None),
):
    if not get_application():
        raise HTTPException(status_code=503, detail="Telegram webhook not configured")
    if settings.telegram_webhook_secret:
        if x_telegram_bot_api_secret_token != settings.telegram_webhook_secret:
            raise HTTPException(status_code=403, detail="Invalid webhook secret")
    await process_webhook_update(await request.json())
    return {"ok": True}


@app.get("/api/meta", response_model=BoardMeta)
def meta():
    return BoardMeta()


@app.get("/api/listings/lookup", response_model=ListingOut)
def lookup_listing(q: str, db: Session = Depends(get_db)):
    listing = find_listing(db, q)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    return listing_to_out(listing)


@app.get("/api/listings", response_model=list[ListingOut])
def list_all_listings(
    status: Optional[ListingStatus] = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    if limit < 1 or limit > 100:
        limit = 50
    if status:
        rows = query_listings(db, status=status, limit=limit)
    else:
        rows = db.query(Listing).order_by(Listing.updated_at.desc()).limit(limit).all()
    return [listing_to_out(x) for x in rows]


@app.get("/api/kanban", response_model=KanbanBoard)
def kanban_board(db: Session = Depends(get_db)):
    listings = db.query(Listing).order_by(Listing.updated_at.desc()).all()
    grouped: dict[ListingStatus, list[ListingOut]] = {s: [] for s in KANBAN_ORDER}
    for listing in listings:
        grouped[listing.status].append(listing_to_out(listing))
    columns = [
        KanbanColumn(status=s, label=STATUS_LABELS[s], listings=grouped[s])
        for s in KANBAN_ORDER
    ]
    return KanbanBoard(columns=columns, total=len(listings))


@app.get("/api/preview", response_model=ListingPreview)
async def preview_listing(url: str):
    meta = await fetch_listing_meta(url)
    return ListingPreview(
        url=url,
        title=meta.title,
        image_url=meta.image_url,
        price=meta.price,
        location=meta.location,
        description=meta.description,
        specs=meta.specs,
        images=meta.images,
        tags=meta.tags,
        district=meta.district,
        city=meta.city,
    )


@app.post("/api/listings", response_model=ListingOut, status_code=201)
async def add_listing(payload: ListingCreate, db: Session = Depends(get_db)):
    existing = get_listing_by_url(db, payload.url)
    if existing:
        raise HTTPException(status_code=409, detail="Listing already saved")

    meta = await fetch_listing_meta(payload.url)
    title = payload.title or meta.title or "Untitled listing"
    image_url = meta.image_url
    price = meta.price

    listing = create_listing(
        db,
        url=payload.url,
        title=title,
        image_url=image_url,
        price=price,
        location=meta.location,
        description=meta.description,
        specs=meta.specs,
        images=meta.images,
        tags=meta.tags,
        notes=payload.notes,
        source_chat_id=payload.source_chat_id,
    )
    return listing_to_out(listing)


@app.get("/api/listings/{listing_id}", response_model=ListingOut)
def get_listing(listing_id: int, db: Session = Depends(get_db)):
    listing = db.query(Listing).filter(Listing.id == listing_id).first()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    return listing_to_out(listing)


@app.patch("/api/listings/{listing_id}", response_model=ListingOut)
def patch_listing(
    listing_id: int, payload: ListingUpdate, db: Session = Depends(get_db)
):
    listing = db.query(Listing).filter(Listing.id == listing_id).first()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    if payload.title is not None:
        listing.title = payload.title
    if payload.notes is not None:
        listing.notes = payload.notes
    if payload.status is not None:
        mark_called = payload.status in {
            ListingStatus.NO_ANSWER,
            ListingStatus.IN_TALK,
        }
        updated = update_listing_status(
            db,
            listing_id,
            payload.status,
            notes=payload.notes if payload.notes is not None else listing.notes,
            mark_called=mark_called,
        )
        return listing_to_out(updated)

    db.commit()
    db.refresh(listing)
    return listing_to_out(listing)


@app.post("/api/listings/{listing_id}/move", response_model=ListingOut)
def move_listing(
    listing_id: int, payload: StatusMove, db: Session = Depends(get_db)
):
    mark_called = payload.status in {ListingStatus.NO_ANSWER, ListingStatus.IN_TALK}
    listing = update_listing_status(
        db, listing_id, payload.status, mark_called=mark_called
    )
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    return listing_to_out(listing)


@app.post("/api/listings/{listing_id}/refresh", response_model=ListingOut)
async def refresh_listing(listing_id: int, db: Session = Depends(get_db)):
    listing = db.query(Listing).filter(Listing.id == listing_id).first()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    meta = await fetch_listing_meta(listing.url)
    listing.title = meta.title or listing.title
    listing.image_url = meta.image_url or listing.image_url
    listing.price = meta.price or listing.price
    listing.location = meta.location or listing.location
    listing.description = meta.description or listing.description
    listing.specs_json = dumps_json(meta.specs or loads_json(listing.specs_json, {}))
    listing.images_json = dumps_json(meta.images or loads_json(listing.images_json, []))
    listing.tags_json = dumps_json(meta.tags or loads_json(listing.tags_json, []))
    db.commit()
    db.refresh(listing)
    return listing_to_out(listing)


@app.delete("/api/listings/{listing_id}")
def delete_listing(listing_id: int, db: Session = Depends(get_db)):
    listing = db.query(Listing).filter(Listing.id == listing_id).first()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    db.delete(listing)
    db.commit()
    return {"deleted": True}


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def dashboard():
    return FileResponse("static/index.html")
