#!/usr/bin/env python3
"""Swiss supermarket weekly deals scraper: Migros, Denner → site/index.html"""
import sys, re, os
from datetime import datetime
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

os.makedirs("site", exist_ok=True)


# ─── Migros ──────────────────────────────────────────────────────────────────
# Playwright intercepts the internal JSON API call the page makes when loading
# promotions — no CSS selectors needed.

def scrape_migros():
    products = []
    captured = []

    try:
        from playwright.sync_api import sync_playwright

        def on_response(response):
            url = response.url
            if ("promotion" in url or "aktionen" in url) and "migros" in url:
                ct = response.headers.get("content-type", "")
                if "json" in ct:
                    try:
                        captured.append({"url": url, "data": response.json()})
                        print(f"[Migros] Captured JSON: {url}", file=sys.stderr)
                    except Exception:
                        pass

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent=UA,
                locale="de-CH",
                extra_http_headers={"Accept-Language": "de-CH,de;q=0.9"},
            )
            page.on("response", on_response)
            page.goto(
                "https://www.migros.ch/de/aktionen",
                wait_until="networkidle",
                timeout=90_000,
            )
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(3000)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(2000)

            with open("site/debug_migros.html", "w", encoding="utf-8") as f:
                f.write(page.content())

            browser.close()

        for entry in captured:
            data = entry["data"]
            items = (
                data.get("products")
                or data.get("items")
                or data.get("results")
                or data.get("articles")
                or []
            )
            if isinstance(items, list):
                for item in items:
                    name = (
                        _nested(item, "name", "de")
                        or _nested(item, "name", "text")
                        or item.get("name", "")
                        or item.get("title", "")
                    )
                    price = (
                        _nested(item, "price", "effective", "value")
                        or _nested(item, "price", "value")
                        or _nested(item, "price", "amount")
                        or ""
                    )
                    old_price = (
                        _nested(item, "price", "recommendedRetailPrice")
                        or _nested(item, "price", "original", "value")
                        or ""
                    )
                    discount = (
                        _nested(item, "promotion", "reductionLabel")
                        or _nested(item, "badges", 0, "text")
                        or ""
                    )
                    if name:
                        products.append({
                            "name": str(name),
                            "price": _fmt_price(price),
                            "old_price": _fmt_price(old_price),
                            "discount": str(discount),
                        })

        if not captured:
            print("[Migros] No JSON captured — check site/debug_migros.html", file=sys.stderr)

    except Exception as e:
        print(f"[Migros] ERROR: {e}", file=sys.stderr)

    print(f"[Migros] {len(products)} products", file=sys.stderr)
    return products


# ─── Denner ──────────────────────────────────────────────────────────────────

def scrape_denner():
    products = []
    seen = set()
    try:
        s = requests.Session()
        s.headers.update(HEADERS)

        for page_num in range(1, 11):
            r = s.get(
                "https://www.denner.ch/de/aktionen",
                params={"page": page_num} if page_num > 1 else {},
                timeout=30,
            )
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")

            cards = soup.select("a[href*='/de/aktionen/']")
            new_this_page = 0
            for card in cards:
                href = card.get("href", "")
                if "~p" not in href or href in seen:
                    continue
                seen.add(href)
                new_this_page += 1

                parent = (
                    card.find_parent("article")
                    or card.find_parent("li")
                    or card.find_parent(class_=re.compile(r"product|card|item"))
                    or card
                )
                name_el = parent.select_one("h2, h3, h4, [class*='name'], [class*='title']")
                name = name_el.get_text(strip=True) if name_el else card.get_text(strip=True)[:80]
                price = _bs4_text(parent, "[class*='action-price'], [class*='new-price'], [class*='price--sale']")
                old_price = _bs4_text(parent, "s, del, [class*='old-price'], [class*='price--original']")
                discount = _bs4_text(parent, "[class*='discount'], [class*='badge'], [class*='saving']")
                if name:
                    products.append({"name": name, "price": price, "old_price": old_price, "discount": discount})

            if new_this_page == 0:
                break
            if not soup.select_one("a[rel='next'], [class*='pagination'] a[aria-label*='next']"):
                break

    except Exception as e:
        print(f"[Denner] ERROR: {e}", file=sys.stderr)

    print(f"[Denner] {len(products)} products", file=sys.stderr)
    return products


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _nested(d, *keys):
    for k in keys:
        if isinstance(d, list):
            try:
                d = d[k]
            except (IndexError, TypeError):
                return None
        elif isinstance(d, dict):
            d = d.get(k)
        else:
            return None
    return d

def _fmt_price(val):
    if val is None or val == "":
        return ""
    if isinstance(val, (int, float)):
        return f"CHF {val:.2f}"
    return str(val)

def _bs4_text(parent, selector):
    if parent is None:
        return ""
    el = parent.select_one(selector)
    return el.get_text(strip=True) if el else ""


# ─── HTML page ───────────────────────────────────────────────────────────────

STORE_META = {
    "Migros": {"color": "#e87722", "url": "https://www.migros.ch/de/aktionen",  "emoji": "🟠"},
    "Denner": {"color": "#8b0000", "url": "https://www.denner.ch/de/aktionen",  "emoji": "🔴"},
}

def build_html(migros, denner):
    now = datetime.now()
    date_str = now.strftime("%d.%m.%Y %H:%M")
    stores = [("Migros", migros), ("Denner", denner)]
    total = sum(len(p) for _, p in stores)

    sections_html = ""
    for store_name, products in stores:
        meta = STORE_META[store_name]
        color, url, emoji = meta["color"], meta["url"], meta["emoji"]
        count = len(products)

        if count == 0:
            cards_html = '<p class="empty">⚠️ Data se nepodařilo načíst — zkus to znovu příští čtvrtek.</p>'
        else:
            cards_html = ""
            for p in products:
                disc_html = f'<span class="badge">{p["discount"]}</span>' if p.get("discount") else ""
                old_html = f'<span class="old">{p["old_price"]}</span>' if p.get("old_price") else ""
                cards_html += f"""
          <div class="card">
            <div class="card-name">{p['name']}</div>
            <div class="card-price">
              <span class="price">{p['price']}</span>
              {old_html}
              {disc_html}
            </div>
          </div>"""

        sections_html += f"""
      <section class="store" data-store="{store_name}">
        <div class="store-header" style="background:{color}">
          <div>
            <span class="store-emoji">{emoji}</span>
            <span class="store-name">{store_name}</span>
          </div>
          <div class="store-meta">
            <span class="store-count">{count} produktů</span>
            <a class="store-link" href="{url}" target="_blank">Otevřít web →</a>
          </div>
        </div>
        <div class="cards">{cards_html}</div>
      </section>"""

    return f"""<!DOCTYPE html>
<html lang="cs">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Švýcarské akce</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            background: #f2f2f7; color: #1c1c1e; min-height: 100vh; }}
    header {{ background: #1c1c1e; color: white; padding: 24px 20px 20px;
              position: sticky; top: 0; z-index: 10; box-shadow: 0 2px 12px rgba(0,0,0,.3); }}
    header h1 {{ font-size: 22px; font-weight: 700; }}
    header p  {{ font-size: 13px; color: #8e8e93; margin-top: 4px; }}
    .tabs {{ display: flex; gap: 8px; padding: 14px 16px; background: #f2f2f7;
             border-bottom: 1px solid #d1d1d6; overflow-x: auto; }}
    .tab {{ padding: 7px 18px; border-radius: 20px; border: none; font-size: 14px;
            font-weight: 600; cursor: pointer; white-space: nowrap;
            background: #e5e5ea; color: #1c1c1e; transition: all .15s; }}
    .tab.active {{ color: white; }}
    .tab[data-store="all"].active    {{ background: #1c1c1e; }}
    .tab[data-store="Migros"].active {{ background: #e87722; }}
    .tab[data-store="Denner"].active {{ background: #8b0000; }}
    main {{ padding: 16px; max-width: 1200px; margin: 0 auto; }}
    .store {{ margin-bottom: 24px; border-radius: 14px; overflow: hidden;
              box-shadow: 0 1px 6px rgba(0,0,0,.1); }}
    .store-header {{ display: flex; align-items: center; justify-content: space-between;
                     padding: 14px 18px; color: white; flex-wrap: wrap; gap: 8px; }}
    .store-emoji {{ font-size: 22px; margin-right: 8px; }}
    .store-name  {{ font-size: 20px; font-weight: 700; }}
    .store-meta  {{ display: flex; align-items: center; gap: 12px; }}
    .store-count {{ font-size: 13px; opacity: .85; }}
    .store-link  {{ font-size: 13px; font-weight: 600; color: white; text-decoration: none; opacity: .9; }}
    .store-link:hover {{ opacity: 1; text-decoration: underline; }}
    .cards {{ background: #e5e5ea; display: grid;
              grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 1px; }}
    .card {{ background: white; padding: 14px 16px; display: flex; flex-direction: column; gap: 8px; }}
    .card-name  {{ font-size: 14px; line-height: 1.35; }}
    .card-price {{ display: flex; align-items: baseline; flex-wrap: wrap; gap: 6px; }}
    .price {{ font-size: 18px; font-weight: 700; color: #c00; }}
    .old   {{ font-size: 12px; color: #8e8e93; text-decoration: line-through; }}
    .badge {{ font-size: 11px; font-weight: 700; padding: 2px 8px;
              border-radius: 10px; background: #c00; color: white; }}
    .empty {{ padding: 24px; color: #8e8e93; font-style: italic; background: white; }}
    .hidden {{ display: none !important; }}
    @media (max-width: 480px) {{ .cards {{ grid-template-columns: 1fr 1fr; }} }}
  </style>
</head>
<body>
<header>
  <h1>🛒 Švýcarské akce</h1>
  <p>Migros · Denner &nbsp;·&nbsp; {total} produktů &nbsp;·&nbsp; aktualizováno {date_str}</p>
</header>
<div class="tabs">
  <button class="tab active" data-store="all">Vše ({total})</button>
  <button class="tab" data-store="Migros">Migros ({len(migros)})</button>
  <button class="tab" data-store="Denner">Denner ({len(denner)})</button>
</div>
<main>{sections_html}</main>
<footer style="text-align:center;padding:24px;font-size:12px;color:#8e8e93;">
  Aktualizováno každý čtvrtek automaticky přes
  <a href="https://github.com/vitekmaca/swiss-deals/actions" target="_blank">GitHub Actions</a>.
</footer>
<script>
  document.querySelectorAll('.tab').forEach(tab => {{
    tab.addEventListener('click', () => {{
      document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      const store = tab.dataset.store;
      document.querySelectorAll('.store').forEach(s => {{
        s.classList.toggle('hidden', store !== 'all' && s.dataset.store !== store);
      }});
    }});
  }});
</script>
</body>
</html>"""


# ─── Main ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== Migros ===", file=sys.stderr)
    migros = scrape_migros()

    print("=== Denner ===", file=sys.stderr)
    denner = scrape_denner()

    html = build_html(migros, denner)
    with open("site/index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("✓ site/index.html vygenerován")
