from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import unquote, urlparse

import httpx

DIVAR_URL_PATTERN = re.compile(
    r"https?://(?:www\.)?divar\.ir/v/[a-zA-Z0-9_-]+",
    re.IGNORECASE,
)
GENERIC_URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

DIVAR_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


@dataclass
class ListingMeta:
    title: Optional[str] = None
    image_url: Optional[str] = None
    price: Optional[str] = None
    district: Optional[str] = None
    city: Optional[str] = None
    location: Optional[str] = None
    description: Optional[str] = None
    specs: dict[str, str] = field(default_factory=dict)
    images: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


def extract_url(text: str) -> Optional[str]:
    divar_match = DIVAR_URL_PATTERN.search(text)
    if divar_match:
        return divar_match.group(0).rstrip(".,)")
    generic = GENERIC_URL_PATTERN.search(text)
    if generic:
        return generic.group(0).rstrip(".,)")
    return None


def is_divar_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return "divar.ir" in host


def extract_divar_token(url: str) -> Optional[str]:
    if not is_divar_url(url):
        return None
    path = unquote(urlparse(url).path)
    if not path.startswith("/v/"):
        return None
    slug = path[3:].strip("/")
    if not slug:
        return None
    return slug.split("/")[-1]


def _iter_widgets(payload: dict):
    for section in payload.get("sections") or []:
        for widget in section.get("widgets") or []:
            yield widget


def _pick_title(payload: dict) -> Optional[str]:
    seo = payload.get("seo") or {}
    web_info = seo.get("web_info") or {}
    schema = seo.get("post_seo_schema") or {}
    share = payload.get("share") or {}
    for candidate in (
        web_info.get("title"),
        schema.get("name"),
        share.get("title"),
        seo.get("title"),
    ):
        if candidate:
            cleaned = re.sub(r"\s*[-|]\s*دیوار.*$", "", str(candidate), flags=re.I).strip()
            if cleaned:
                return cleaned
    return None


def _pick_images(payload: dict) -> list[str]:
    images: list[str] = []
    seen: set[str] = set()

    seo = payload.get("seo") or {}
    schema = (seo.get("post_seo_schema") or {})
    for candidate in (seo.get("image_url"), schema.get("image")):
        if candidate and candidate not in seen:
            images.append(str(candidate))
            seen.add(str(candidate))

    for widget in _iter_widgets(payload):
        if widget.get("widget_type") != "IMAGE_CAROUSEL":
            continue
        items = (widget.get("data") or {}).get("items") or []
        for item in items:
            url = (item.get("image") or {}).get("url")
            if url and url not in seen:
                images.append(str(url))
                seen.add(str(url))

    return images


def _pick_description(payload: dict) -> Optional[str]:
    parts: list[str] = []
    for widget in _iter_widgets(payload):
        if widget.get("widget_type") != "DESCRIPTION_ROW":
            continue
        text = (widget.get("data") or {}).get("text")
        if text:
            parts.append(str(text).strip())
    if not parts:
        schema = (payload.get("seo") or {}).get("post_seo_schema") or {}
        if schema.get("description"):
            parts.append(str(schema["description"]).strip())
    return "\n\n".join(parts) if parts else None


def _pick_specs(payload: dict) -> dict[str, str]:
    specs: dict[str, str] = {}
    skip_titles = {"تصویر‌ها برای همین ملک است؟"}

    for widget in _iter_widgets(payload):
        widget_type = widget.get("widget_type")
        data = widget.get("data") or {}

        if widget_type == "UNEXPANDABLE_ROW":
            title = str(data.get("title") or "").strip()
            value = str(data.get("value") or "").strip()
            if title and value and title not in skip_titles:
                specs[title] = value
            continue

        if widget_type == "GROUP_INFO_ROW":
            for item in data.get("items") or []:
                title = str(item.get("title") or "").strip()
                value = str(item.get("value") or "").strip()
                if title and value:
                    specs[title] = value

    schema = (payload.get("seo") or {}).get("post_seo_schema") or {}
    floor_size = (schema.get("floorSize") or {}).get("value")
    if floor_size and "متراژ" not in specs:
        specs["متراژ"] = str(floor_size)
    rooms = schema.get("numberOfRooms")
    if rooms and "اتاق" not in specs:
        specs["اتاق"] = str(rooms)

    return specs


def _pick_price(specs: dict[str, str]) -> Optional[str]:
    for key, value in specs.items():
        if any(word in key for word in ("قیمت", "رهن", "اجاره", "ودیعه")):
            return value
    return None


def _pick_tags(payload: dict) -> list[str]:
    tags: list[str] = []
    for widget in _iter_widgets(payload):
        if widget.get("widget_type") != "WRAPPER_ROW":
            continue
        chips = ((widget.get("data") or {}).get("chip_list") or {}).get("chips") or []
        for chip in chips:
            title = chip.get("title") or chip.get("text")
            if title:
                tags.append(str(title).strip())
    return tags


def _pick_location(payload: dict) -> tuple[Optional[str], Optional[str], Optional[str]]:
    seo = payload.get("seo") or {}
    web_info = seo.get("web_info") or {}
    district = web_info.get("district_persian")
    city = web_info.get("city_persian")
    parts = [p for p in (district, city) if p]
    location = "، ".join(parts) if parts else None
    return district, city, location


def _parse_divar_payload(payload: dict) -> ListingMeta:
    district, city, location = _pick_location(payload)
    specs = _pick_specs(payload)
    images = _pick_images(payload)
    return ListingMeta(
        title=_pick_title(payload),
        image_url=images[0] if images else None,
        price=_pick_price(specs),
        district=district,
        city=city,
        location=location,
        description=_pick_description(payload),
        specs=specs,
        images=images,
        tags=_pick_tags(payload),
    )


async def _fetch_divar_api(token: str) -> Optional[ListingMeta]:
    try:
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
            response = await client.get(
                f"https://api.divar.ir/v8/posts-v2/web/{token}",
                headers=DIVAR_HEADERS,
            )
            if response.status_code != 200:
                return None
            return _parse_divar_payload(response.json())
    except Exception:
        return None


async def _fetch_open_graph(url: str) -> ListingMeta:
    try:
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
            response = await client.get(
                url,
                headers={"User-Agent": DIVAR_HEADERS["User-Agent"]},
            )
            if response.status_code != 200:
                return ListingMeta()
            html = response.text
    except Exception:
        return ListingMeta()

    meta = ListingMeta()

    title_match = re.search(r"<title[^>]*>([^<]+)</title>", html, re.I)
    if title_match:
        meta.title = re.sub(
            r"\s*[-|]\s*دیوار.*$", "", title_match.group(1).strip(), flags=re.I
        ).strip()

    for pattern in (
        r'<meta property="og:image" content="([^"]+)"',
        r'<meta name="twitter:image" content="([^"]+)"',
    ):
        image_match = re.search(pattern, html, re.I)
        if image_match:
            meta.image_url = image_match.group(1).strip()
            meta.images = [meta.image_url]
            break

    og_title = re.search(r'<meta property="og:title" content="([^"]+)"', html, re.I)
    if og_title:
        meta.title = og_title.group(1).strip()

    og_desc = re.search(r'<meta property="og:description" content="([^"]+)"', html, re.I)
    if og_desc:
        meta.description = og_desc.group(1).strip()

    return meta


async def fetch_listing_meta(url: str) -> ListingMeta:
    if is_divar_url(url):
        token = extract_divar_token(url)
        if token:
            meta = await _fetch_divar_api(token)
            if meta and (meta.title or meta.image_url or meta.description):
                return meta

    fallback = await _fetch_open_graph(url)
    if fallback.title or fallback.image_url or fallback.description:
        return fallback

    return ListingMeta(title=url.rstrip("/").split("/")[-1] or "Untitled listing")


def dumps_json(data) -> str:
    return json.dumps(data, ensure_ascii=False)


def loads_json(raw: Optional[str], default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


async def fetch_divar_title(url: str) -> Optional[str]:
    meta = await fetch_listing_meta(url)
    return meta.title
