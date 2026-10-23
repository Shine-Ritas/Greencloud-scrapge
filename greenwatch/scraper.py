import re
from decimal import Decimal
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from bs4 import BeautifulSoup
from .config import SOURCE_URL


class ScrapeError(Exception):
    pass


def fetch_page():
    req = Request(SOURCE_URL, headers={
        "User-Agent": "GreenWatch/1.0 (personal stock monitor; four scheduled checks/day)",
        "Accept": "text/html", "Cache-Control": "no-cache",
    })
    try:
        with urlopen(req, timeout=30) as response:
            if urlparse(response.url).hostname != "greencloudvps.com":
                raise ScrapeError("Store redirected to an unexpected host")
            body = response.read(4 * 1024 * 1024 + 1)
            if len(body) > 4 * 1024 * 1024:
                raise ScrapeError("Store response exceeded 4 MB")
            return body.decode("utf-8", errors="replace")
    except ScrapeError:
        raise
    except Exception:
        raise ScrapeError("Could not fetch store (network, HTTP, or TLS error)") from None


def parse_products(html, keywords):
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select('.product[id^="product"]')
    if not cards:
        raise ScrapeError("No product cards found; store layout may have changed or access was blocked")
    products = []
    seen = set()
    for card in cards:
        product_id = card.get("id", "")
        name_node = card.find(id=f"{product_id}-name")
        qty_node = card.select_one(".qty")
        price_node = card.select_one(".product-pricing")
        order = card.find(id=f"{product_id}-order-button")
        name = name_node.get_text(" ", strip=True) if name_node else ""
        qty = re.search(r"\b([\d,]+)\s+Available\b", qty_node.get_text(" ", strip=True), re.I) if qty_node else None
        price_text = price_node.get_text(" ", strip=True) if price_node else ""
        price = re.search(r"\$\s*([\d,]+\.\d{2})\s+USD\b", price_text, re.I)
        billing = next((label for label in ("Annually", "Monthly", "Quarterly", "Semi-Annually", "Biennially", "Triennially")
                        if re.search(r"(?<![\w-])" + re.escape(label) + r"\b", price_text, re.I)), None)
        specs = [" ".join(li.stripped_strings) for li in card.select(".product-desc li")]
        location = next((re.sub(r"\bLocation\s*:?", "", item, flags=re.I).strip(" :")
                         for item in specs if re.search(r"\bLocation\b", item, re.I)), "")
        if not all((name, qty, price, billing, location)) or product_id in seen:
            raise ScrapeError(f"Incomplete or duplicate product card: {product_id}; previous stock was preserved")
        href = order.get("href", "") if order else ""
        url = urljoin(SOURCE_URL, href)
        safe_url = urlparse(url).scheme == "https" and urlparse(url).hostname == "greencloudvps.com"
        orderable = bool(href and safe_url and "disabled" not in order.get("class", [])
                         and not order.has_attr("disabled") and order.get("aria-disabled") != "true") if order else False
        is_apac = any(re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", location.casefold()) for word in keywords)
        seen.add(product_id)
        products.append({
            "id": product_id, "name": name, "location": location, "apac": int(is_apac),
            "price_cents": int(Decimal(price.group(1).replace(",", "")) * 100),
            "currency": "USD", "billing": billing, "stock": int(qty.group(1).replace(",", "")),
            "orderable": int(orderable), "url": url if safe_url else SOURCE_URL, "specs": specs,
        })
    if not any(p["apac"] for p in products):
        raise ScrapeError("No APAC locations recognized; check store layout or APAC_EXTRA_KEYWORDS")
    return products


def qualifies(product):
    return bool(product["apac"] and "kvm" in product["name"].lower()
                and product["price_cents"] == 2500 and product["currency"] == "USD"
                and product["billing"] == "Annually" and product["stock"] >= 1 and product["orderable"])
