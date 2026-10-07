"""
Aqar.fm MCP Server
- Remote (streamable-http) for Claude custom connector: python server.py
- Local (stdio) for Claude Desktop:                       python server.py --stdio
"""
import re
import sys
import os
import time
from urllib.parse import quote, unquote

import httpx
from bs4 import BeautifulSoup
from mcp.server.fastmcp import FastMCP

BASE = "https://sa.aqar.fm"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept-Language": "ar,en;q=0.8",
}
LISTING_ID = re.compile(r"-(\d{5,})/?$")
PRICE = re.compile(r"([\d,]+)\s*\S?\s*/\s*(سنوي|شهري|يومي)")
AREA = re.compile(r"([\d,]+)\s*م²")

mcp = FastMCP("aqar", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))


def _seg(s: str) -> str:
    return quote(s.strip().replace(" ", "-"))


def _build_url(category, city, region, district, filter_, page) -> str:
    parts = [category, city, region, district, filter_]
    path = "/".join(_seg(p) for p in parts if p)
    if page and page > 1:
        path += f"/{page}"
    return f"{BASE}/{path}"


def _fetch(url: str) -> str:
    with httpx.Client(headers=HEADERS, timeout=20, follow_redirects=True) as c:
        r = c.get(url)
        r.raise_for_status()
        return r.text


def _parse_listings(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = unquote(a["href"])
        m = LISTING_ID.search(href)
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        text = a.get_text(" ", strip=True)
        price = PRICE.search(text)
        area = AREA.search(text)
        nums_after_area = []
        if area:
            tail = text[area.end(): area.end() + 40]
            nums_after_area = re.findall(r"-\s*(\d+)", tail)[:3]
        out.append({
            "id": m.group(1),
            "url": href if href.startswith("http") else BASE + href,
            "price": int(price.group(1).replace(",", "")) if price else None,
            "period": price.group(2) if price else None,
            "area_m2": int(area.group(1).replace(",", "")) if area else None,
            "rooms": int(nums_after_area[0]) if len(nums_after_area) > 0 else None,
            "baths": int(nums_after_area[1]) if len(nums_after_area) > 1 else None,
            "description": text[:600],
        })
    return out


@mcp.tool()
def search_listings(
    district: str = "حي المونسية",
    region: str = "شرق الرياض",
    city: str = "الرياض",
    category: str = "شقق للإيجار",
    filter: str = "",
    page: int = 1,
    max_price: int | None = None,
    min_rooms: int | None = None,
) -> dict:
    """Search Aqar.fm listings.
    category: شقق للإيجار | شقق للبيع | فلل للإيجار | فلل للبيع | دور للإيجار | استوديوهات للإيجار ...
    region:   شمال الرياض | شرق الرياض | غرب الرياض | جنوب الرياض | وسط الرياض
    filter (optional path filter): غرفتين | ثلاث غرف | أربع غرف | عزاب | عوائل | دور أرضي | أقل من 5 سنة
    """
    url = _build_url(category, city, region, district, filter, page)
    items = _parse_listings(_fetch(url))
    if max_price:
        items = [i for i in items if i["price"] and i["price"] <= max_price]
    if min_rooms:
        items = [i for i in items if i["rooms"] and i["rooms"] >= min_rooms]
    return {"source": url, "count": len(items), "listings": items}


@mcp.tool()
def search_all_pages(
    district: str = "حي المونسية",
    region: str = "شرق الرياض",
    city: str = "الرياض",
    category: str = "شقق للإيجار",
    filter: str = "",
    pages: int = 5,
    max_price: int | None = None,
    min_rooms: int | None = None,
) -> dict:
    """Scan pages 1..N (max 10) and return merged, de-duplicated listings sorted by price."""
    merged, seen = [], set()
    for p in range(1, min(pages, 10) + 1):
        try:
            res = search_listings(district, region, city, category, filter, p, max_price, min_rooms)
        except httpx.HTTPError:
            break
        new = [i for i in res["listings"] if i["id"] not in seen]
        if not new:
            break
        seen.update(i["id"] for i in new)
        merged.extend(new)
        time.sleep(1.0)  # be polite
    merged.sort(key=lambda x: x["price"] or 10**12)
    return {"count": len(merged), "listings": merged}


@mcp.tool()
def get_listing(url: str) -> dict:
    """Get full page text of a single Aqar listing URL."""
    soup = BeautifulSoup(_fetch(url), "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""
    main = soup.find("main") or soup.body
    text = main.get_text("\n", strip=True) if main else ""
    return {"url": url, "title": title, "text": text[:8000]}


if __name__ == "__main__":
    mcp.run(transport="stdio" if "--stdio" in sys.argv else "streamable-http")
