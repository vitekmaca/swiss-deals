#!/usr/bin/env python3
"""Swiss supermarket weekly deals scraper: Migros, COOP, Denner"""
import os, sys, re
from datetime import datetime
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import requests
from bs4 import BeautifulSoup

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept-Language": "de-CH,de;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
}


# ─── Migros ──────────────────────────────────────────────────────────────────

def scrape_migros():
    products = []
    try:
        s = requests.Session()
        s.headers.update(HEADERS)

        # Guest OAuth token (no login needed)
        token_r = s.post(
            "https://www.migros.ch/oauthclients/public/tokens/guest",
            json={"marketCode": "national"},
            timeout=20,
        )
        token_r.raise_for_status()
        token = token_r.json().get("access_token", "")

        # Promotions endpoint
        promo_r = s.get(
            "https://www.migros.ch/product-display/public/web/v2/products/promotion/search",
            headers={"Authorization": f"Bearer {token}"},
            params={"lang": "de", "marketCode": "national", "limit": 200, "offset": 0},
            timeout=30,
        )
        promo_r.raise_for_status()
        data = promo_r.json()

        items = data.get("products") or data.get("items") or data.get("results") or []
        for item in items:
            name = _nested(item, "name", "de") or item.get("name", "")
            price = (
                _nested(item, "price", "effective", "value")
                or _nested(item, "price", "value")
                or ""
            )
            old_price = (
                _nested(item, "price", "recommendedRetailPrice")
                or _nested(item, "price", "original", "value")
                or ""
            )
            discount = (
                _nested(item, "promotion", "reductionLabel")
                or _nested(item, "promotion", "labelTextKey")
                or ""
            )
            if name:
                products.append({
                    "name": str(name),
                    "price": _fmt_price(price),
                    "old_price": _fmt_price(old_price),
                    "discount": str(discount),
                })
    except Exception as e:
        print(f"[Migros] ERROR: {e}", file=sys.stderr)

    print(f"[Migros] {len(products)} products found", file=sys.stderr)
    return products


# ─── COOP ────────────────────────────────────────────────────────────────────

def scrape_coop():
    products = []
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent=UA,
                locale="de-CH",
                extra_http_headers={"Accept-Language": "de-CH,de;q=0.9"},
            )
            page.goto(
                "https://www.coop.ch/de/einkaufen/supermarkt/aktionen.html",
                wait_until="domcontentloaded",
                timeout=60_000,
            )

            # Click "load all" / "load more" buttons until gone
            for _ in range(15):
                try:
                    btn = page.locator(
                        "button:has-text('Alle laden'), "
                        "button:has-text('Mehr laden'), "
                        "button:has-text('Alle Aktionen')"
                    )
                    if btn.count() == 0:
                        break
                    btn.first.scroll_into_view_if_needed()
                    btn.first.click(timeout=4000)
                    page.wait_for_timeout(2500)
                except Exception:
                    break

            # Product tiles — COOP uses several possible class patterns
            tiles = page.locator(
                "[class*='product-tile']:not([class*='wrapper']), "
                "[class*='ProductTile']:not([class*='Wrapper']), "
                "li[class*='product-list']"
            )
            n = tiles.count()
            for i in range(n):
                tile = tiles.nth(i)
                name = _pw_text(tile,
                    "[class*='product-name'], [class*='ProductName'], "
                    "[class*='product-title'], h3, h2")
                price = _pw_text(tile,
                    "[class*='price--reduced'], [class*='ActionPrice'], "
                    "[class*='sale-price'], [class*='actual-price'], "
                    "[class*='price-action']")
                old_price = _pw_text(tile,
                    "[class*='price--original'], [class*='OldPrice'], "
                    "[class*='regular-price'], s, del, [class*='CrossedPrice']")
                discount = _pw_text(tile,
                    "[class*='badge'], [class*='discount'], [class*='saving']")
                if name:
                    products.append({
                        "name": name,
                        "price": price,
                        "old_price": old_price,
                        "discount": discount,
                    })
            browser.close()
    except Exception as e:
        print(f"[COOP] ERROR: {e}", file=sys.stderr)

    print(f"[COOP] {len(products)} products found", file=sys.stderr)
    return products


# ─── Denner ──────────────────────────────────────────────────────────────────

def scrape_denner():
    products = []
    seen_hrefs = set()
    try:
        s = requests.Session()
        s.headers.update(HEADERS)

        page_num = 1
        while page_num <= 10:
            r = s.get(
                "https://www.denner.ch/de/aktionen",
                params={"page": page_num} if page_num > 1 else {},
                timeout=30,
            )
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")

            # Links matching /de/aktionen/product-name~pID pattern
            cards = soup.select("a[href*='/de/aktionen/']")
            new_cards = 0
            for card in cards:
                href = card.get("href", "")
                if "~p" not in href or href in seen_hrefs:
                    continue
                seen_hrefs.add(href)
                new_cards += 1

                parent = (
                    card.find_parent("article")
                    or card.find_parent("li")
                    or card.find_parent(class_=re.compile(r"product|card|item"))
                    or card
                )

                # Name
                name_el = parent.select_one("h2, h3, h4, [class*='name'], [class*='title']")
                name = name_el.get_text(strip=True) if name_el else card.get_text(strip=True)[:80]

                # Prices
                price = _bs4_text(parent, "[class*='action-price'], [class*='new-price'], [class*='price--sale']")
                old_price = _bs4_text(parent, "s, del, [class*='old-price'], [class*='price--original']")
                discount = _bs4_text(parent, "[class*='discount'], [class*='badge'], [class*='saving']")

                if name:
                    products.append({
                        "name": name,
                        "price": price,
                        "old_price": old_price,
                        "discount": discount,
                    })

            # Stop if no new cards or no "next page" link
            if new_cards == 0:
                break
            next_el = soup.select_one("a[rel='next'], [class*='pagination'] a[aria-label*='next']")
            if not next_el:
                break
            page_num += 1

    except Exception as e:
        print(f"[Denner] ERROR: {e}", file=sys.stderr)

    print(f"[Denner] {len(products)} products found", file=sys.stderr)
    return products


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _nested(d, *keys):
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def _fmt_price(val):
    if val is None or val == "":
        return ""
    if isinstance(val, (int, float)):
        return f"CHF {val:.2f}"
    return str(val)


def _pw_text(tile, selector):
    try:
        el = tile.locator(selector)
        return el.first.inner_text().strip() if el.count() > 0 else ""
    except Exception:
        return ""


def _bs4_text(parent, selector):
    if parent is None:
        return ""
    el = parent.select_one(selector)
    return el.get_text(strip=True) if el else ""


# ─── HTML email ──────────────────────────────────────────────────────────────

def build_html(migros, coop, denner):
    date_str = datetime.now().strftime("%d.%m.%Y")
    stores = [
        ("Migros", "#e87722", migros),
        ("COOP", "#c41230", coop),
        ("Denner", "#8b0000", denner),
    ]

    rows_html = ""
    for store_name, color, products in stores:
        count = len(products)
        rows_html += f"""
        <tr>
          <td colspan="4" style="background:{color};color:white;font-weight:bold;
              padding:12px 16px;font-size:18px;letter-spacing:.5px;">
            {store_name} &nbsp;·&nbsp; {count} akčních produktů
          </td>
        </tr>"""
        if count == 0:
            rows_html += """
        <tr>
          <td colspan="4" style="padding:16px;color:#999;font-style:italic;text-align:center;">
            Nepodařilo se načíst — zkontroluj GitHub Actions logy.
          </td>
        </tr>"""
        else:
            rows_html += """
        <tr style="background:#f5f5f5;">
          <th style="padding:8px 12px;text-align:left;font-size:12px;color:#555;">Produkt</th>
          <th style="padding:8px 12px;text-align:right;font-size:12px;color:#555;">Akční cena</th>
          <th style="padding:8px 12px;text-align:right;font-size:12px;color:#555;">Původní cena</th>
          <th style="padding:8px 12px;text-align:center;font-size:12px;color:#555;">Sleva</th>
        </tr>"""
            for p in products[:60]:
                disc_html = (
                    f'<span style="background:#c00;color:white;font-size:11px;'
                    f'padding:2px 7px;border-radius:10px;">{p["discount"]}</span>'
                    if p.get("discount") else ""
                )
                old_html = (
                    f'<span style="color:#aaa;text-decoration:line-through;font-size:12px;">'
                    f'{p["old_price"]}</span>'
                    if p.get("old_price") else ""
                )
                rows_html += f"""
        <tr style="border-bottom:1px solid #f0f0f0;">
          <td style="padding:9px 12px;font-size:14px;">{p['name']}</td>
          <td style="padding:9px 12px;text-align:right;font-weight:bold;color:#c00;font-size:15px;">{p['price']}</td>
          <td style="padding:9px 12px;text-align:right;">{old_html}</td>
          <td style="padding:9px 12px;text-align:center;">{disc_html}</td>
        </tr>"""
            if count > 60:
                rows_html += f"""
        <tr>
          <td colspan="4" style="padding:10px 12px;color:#999;font-size:12px;font-style:italic;">
            … a dalších {count - 60} produktů. Navštiv web obchodu pro úplný seznam.
          </td>
        </tr>"""
        rows_html += '<tr><td colspan="4" style="padding:12px;"></td></tr>'

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
</head>
<body style="margin:0;padding:0;background:#f0f0f0;font-family:Arial,sans-serif;">
<div style="max-width:700px;margin:20px auto;background:white;border-radius:10px;
            overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.15);">
  <div style="background:#1a1a1a;color:white;padding:20px 24px;">
    <h1 style="margin:0;font-size:22px;">🛒 Švýcarské akce &nbsp;·&nbsp; {date_str}</h1>
    <p style="margin:6px 0 0;color:#aaa;font-size:13px;">
      Migros · COOP · Denner — automatický přehled každý čtvrtek
    </p>
  </div>
  <table style="width:100%;border-collapse:collapse;">
    {rows_html}
  </table>
  <div style="padding:16px 24px;color:#999;font-size:11px;border-top:1px solid #eee;">
    Generováno automaticky přes GitHub Actions.
  </div>
</div>
</body></html>"""


# ─── Email ───────────────────────────────────────────────────────────────────

def send_email(html: str):
    sender = os.environ["EMAIL_FROM"]
    password = os.environ["EMAIL_PASSWORD"]
    recipient = os.environ["EMAIL_TO"]
    date_str = datetime.now().strftime("%d.%m.%Y")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🛒 Švýcarské akce – {date_str}"
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(sender, password)
        smtp.sendmail(sender, recipient, msg.as_string())

    print(f"✓ Email odeslán na {recipient}")


# ─── Main ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== Migros ===", file=sys.stderr)
    migros = scrape_migros()

    print("=== COOP ===", file=sys.stderr)
    coop = scrape_coop()

    print("=== Denner ===", file=sys.stderr)
    denner = scrape_denner()

    html = build_html(migros, coop, denner)
    send_email(html)
