"""
Google Maps business email scraper — free, local, no paid APIs.

Pipeline:
  1. Scrape Google Maps listings per category (Playwright, real browser).
  2. Visit each business website and harvest emails (requests + BeautifulSoup).

Install:
    pip install playwright beautifulsoup4 requests
    playwright install chromium

Run:
    python maps_email_scraper.py --location "Kanpur"
    python maps_email_scraper.py --location "Kanpur,London,New York" --limit 30
    python maps_email_scraper.py --worldwide --categories "dentists" --limit 20 --output leads.csv
"""

import argparse
import csv
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import parse_qs, quote, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

# Windows consoles often default to cp1252; business names can be anything.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

# ---------------------------------------------------------------------------
# Config — tweak here
# ---------------------------------------------------------------------------
HEADLESS = True  # set False to watch the browser while debugging selectors

DEFAULT_CATEGORIES = ["interior designers", "dentists", "law firms", "restaurants"]

# Used by --worldwide: major business hubs covering every continent.
DEFAULT_WORLD_CITIES = [
    # North America
    "New York", "Los Angeles", "Chicago", "Houston", "Toronto", "Vancouver", "Mexico City",
    # South America
    "Sao Paulo", "Buenos Aires", "Bogota", "Lima", "Santiago",
    # Europe
    "London", "Paris", "Berlin", "Madrid", "Rome", "Amsterdam", "Vienna",
    "Warsaw", "Stockholm", "Istanbul",
    # Africa
    "Cairo", "Lagos", "Nairobi", "Johannesburg", "Casablanca",
    # Asia
    "Mumbai", "Delhi", "Bangalore", "Dubai", "Riyadh", "Singapore", "Bangkok",
    "Jakarta", "Manila", "Kuala Lumpur", "Hong Kong", "Tokyo", "Seoul",
    # Oceania
    "Sydney", "Melbourne", "Auckland",
]

# Used by --india: Indian Tier 1 + Tier 2 cities (per the widely used
# RBI/HRA city classification). Tier 1 = the 8 metros; Tier 2 = the major
# secondary cities. De-duped, ordered tier 1 first.
INDIA_TIER1_CITIES = [
    "Mumbai", "Delhi", "Bangalore", "Hyderabad",
    "Ahmedabad", "Chennai", "Kolkata", "Pune",
]
INDIA_TIER2_CITIES = [
    "Agra", "Ajmer", "Aligarh", "Amritsar", "Aurangabad", "Bareilly",
    "Bhopal", "Bhubaneswar", "Chandigarh", "Coimbatore", "Dehradun",
    "Faridabad", "Ghaziabad", "Guwahati", "Gwalior", "Indore", "Jaipur",
    "Jalandhar", "Jammu", "Jamshedpur", "Jodhpur", "Kanpur", "Kochi",
    "Lucknow", "Ludhiana", "Madurai", "Mangalore", "Meerut", "Moradabad",
    "Mysore", "Nagpur", "Nashik", "Noida", "Patna", "Raipur", "Rajkot",
    "Ranchi", "Salem", "Solapur", "Srinagar", "Surat",
    "Thiruvananthapuram", "Tiruchirappalli", "Vadodara", "Varanasi",
    "Vijayawada", "Visakhapatnam", "Warangal",
]
INDIA_TIER1_TIER2_CITIES = INDIA_TIER1_CITIES + INDIA_TIER2_CITIES

MAPS_SEARCH_DELAY = (2.0, 4.0)  # randomized pause between Maps searches

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 10          # seconds per website request
SITE_DELAY_RANGE = (1.0, 2.0)  # randomized politeness delay between page fetches
MAX_WORKERS = 5               # thread pool size for website fetching
CONTACT_PATHS = ["", "/contact", "/contact-us", "/about"]  # "" = homepage

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"\+?\d[\d\s\-()]{7,}\d")

# Emails matching these are junk (asset filenames, placeholder domains, trackers)
JUNK_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js",
                   ".ico", ".woff", ".woff2", ".ttf", ".mp4", ".pdf", ".webm")
JUNK_DOMAINS = ("example.com", "example.org", "email.com", "domain.com",
                "yourdomain.com", "sentry.io", "wixpress.com", "sentry-next.wixpress.com",
                "mysite.com", "company.com", "godaddy.com", "placeholder.com")


# ---------------------------------------------------------------------------
# Stage 1: Google Maps scraping (single-threaded, Playwright)
# ---------------------------------------------------------------------------
def scrape_maps(query: str, limit: int = 50, browser=None) -> list[dict]:
    """Search Google Maps for `query`, scroll the results panel, and return
    a list of dicts: name, website, phone, address, rating.

    Pass an existing Playwright `browser` to reuse it across many searches
    (much faster for multi-location runs); otherwise one is launched and
    torn down just for this call."""
    if browser is None:
        with sync_playwright() as p:
            return scrape_maps(query, limit, p.chromium.launch(headless=HEADLESS))

    listings = []
    page = browser.new_page(user_agent=USER_AGENT, viewport={"width": 1280, "height": 900})
    try:
        url = f"https://www.google.com/maps/search/{quote(query)}?hl=en"
        page.goto(url, timeout=30000, wait_until="domcontentloaded")

        _dismiss_consent(page)

        # The scrollable results panel. Google shifts class names often,
        # but role="feed" has been stable for years.
        feed = page.locator('div[role="feed"]')
        try:
            feed.wait_for(state="visible", timeout=15000)
        except PlaywrightTimeoutError:
            # No feed: either zero results, or Maps jumped straight to a
            # single place page. Try the single-place fallback.
            single = _scrape_single_place(page)
            if single:
                print("  (Maps opened a single place page — captured 1 result)")
                return [single]
            print(f"  WARNING: results panel not found for '{query}' — "
                  "no results there, or Maps changed its DOM / showed a "
                  "captcha. Set HEADLESS = False to inspect.", file=sys.stderr)
            return []

        _scroll_results(page, feed, limit)
        listings = _extract_listings(page, limit)
    except PlaywrightTimeoutError:
        print(f"  WARNING: timed out loading Maps for '{query}'", file=sys.stderr)
    finally:
        page.close()
    return listings


def _dismiss_consent(page) -> None:
    """Click through the Google cookie-consent interstitial if it appears."""
    try:
        if "consent.google" in page.url:
            for label in ("Accept all", "I agree", "Reject all"):
                btn = page.locator(f'button:has-text("{label}")').first
                if btn.count():
                    btn.click(timeout=5000)
                    page.wait_for_load_state("domcontentloaded", timeout=15000)
                    return
    except Exception:
        pass  # consent handling is best-effort; the scrape may still work


def _scroll_results(page, feed, limit: int) -> None:
    """Scroll the results feed until we have `limit` listings, hit the end of
    the list, or stop seeing growth."""
    stale_rounds = 0
    last_count = 0
    while True:
        count = page.locator('a.hfpxzc').count()
        if count >= limit:
            break
        # "You've reached the end of the list." marker
        if page.locator('div[role="feed"] span.HlvSq').count() or \
           page.get_by_text("reached the end of the list").count():
            break
        if count == last_count:
            stale_rounds += 1
            if stale_rounds >= 5:  # nothing new after 5 scrolls — give up
                break
        else:
            stale_rounds = 0
            last_count = count
        try:
            feed.evaluate("el => el.scrollBy(0, el.scrollHeight)")
        except Exception:
            break
        page.wait_for_timeout(random.uniform(900, 1500))


def _extract_listings(page, limit: int) -> list[dict]:
    """Pull structured fields out of the loaded result cards in one JS pass."""
    raw = page.evaluate(
        """() => {
            const out = [];
            for (const link of document.querySelectorAll('div[role="feed"] a.hfpxzc')) {
                const card = link.closest('div.Nv2PK') || link.parentElement;
                const site = card.querySelector('a[data-value="Website"]');
                const rating = card.querySelector('span.MW4etd');
                const lines = Array.from(card.querySelectorAll('.W4Efsd'))
                    .map(e => e.innerText.trim()).filter(Boolean);
                out.push({
                    name: link.getAttribute('aria-label') || '',
                    website: site ? site.href : '',
                    rating: rating ? rating.textContent.trim() : '',
                    lines: lines,
                    href: link.href || '',
                });
            }
            return out;
        }"""
    )
    listings = []
    for item in raw[:limit]:
        phone, address = _parse_card_lines(item["lines"])
        listings.append({
            "name": item["name"].strip(),
            "website": _clean_website(item["website"].strip()),
            "phone": phone,
            "address": address,
            "rating": item["rating"],
            "href": item.get("href", ""),
        })
    return listings


def _enrich_listings(page, listings: list[dict]) -> None:
    """Maps search cards no longer carry phone or website — those only live on
    each place's detail panel. For any listing still missing them, open its
    place page and fill in phone, website, and (if needed) address.

    This is the slow part of Stage 1 (one navigation per place), but it's the
    only reliable way to get phones and the website URLs the email stage needs."""
    for item in listings:
        url = item.get("href")
        if not url or (item["phone"] and item["website"]):
            continue
        try:
            page.goto(url, timeout=20000, wait_until="domcontentloaded")
            try:
                page.wait_for_selector("h1", timeout=8000)
            except PlaywrightTimeoutError:
                pass
            page.wait_for_timeout(600)  # let the side panel's buttons render
            detail = _scrape_single_place(page)
        except Exception:
            continue  # one bad place page shouldn't sink the whole query
        if not detail:
            continue
        if not item["website"] and detail.get("website"):
            item["website"] = _clean_website(detail["website"])
        if not item["phone"] and detail.get("phone"):
            item["phone"] = detail["phone"]
        if not item["address"] and detail.get("address"):
            item["address"] = detail["address"]


def _clean_website(url: str) -> str:
    """Sponsored listings link through google.com/aclk click trackers instead
    of the real site. Recover the destination from the adurl/q param when
    present; otherwise drop the URL so we don't 'find' Google's own emails."""
    if not url:
        return ""
    host = urlsplit(url).netloc.lower()
    if host == "google.com" or host.endswith(".google.com"):
        qs = parse_qs(urlsplit(url).query)
        for param in ("adurl", "q"):
            for candidate in qs.get(param, []):
                if candidate.startswith("http"):
                    return candidate
        return ""
    return url


def _parse_card_lines(lines: list[str]) -> tuple[str, str]:
    """Heuristically pull phone + address from a result card's detail lines.
    Cards look like: 'Dentist · 12/345 Mall Road' / 'Open ⋅ Closes 9 pm · 098765 43210'."""
    phone = ""
    address = ""
    segments = []
    for line in lines:
        segments.extend(s.strip() for s in re.split(r"[·⋅|]", line) if s.strip())
    hours_words = ("open", "close", "24 hours", "opens", "temporarily", "permanently")
    for seg in segments:
        if not phone:
            m = PHONE_RE.search(seg)
            # avoid mistaking "4.5 (123)"-style ratings or years for phones
            if m and sum(c.isdigit() for c in m.group()) >= 8:
                phone = m.group().strip()
                continue
        low = seg.lower()
        if any(w in low for w in hours_words):
            continue
        if re.fullmatch(r"[\d.,()\s★]+", seg):  # pure numbers = rating/review count
            continue
        # address: prefer the longest remaining segment that has a digit or comma
        if (any(c.isdigit() for c in seg) or "," in seg) and len(seg) > len(address):
            address = seg
    return phone, address


def _scrape_single_place(page) -> dict | None:
    """Fallback when Maps redirects a search straight to one place's page."""
    try:
        name = page.locator("h1").first.inner_text(timeout=5000).strip()
        if not name:
            return None
        website = ""
        site_el = page.locator('a[data-item-id="authority"]').first
        if site_el.count():
            website = site_el.get_attribute("href") or ""
        phone = ""
        phone_el = page.locator('button[data-item-id^="phone"]').first
        if phone_el.count():
            m = PHONE_RE.search(phone_el.get_attribute("data-item-id") or "")
            phone = m.group() if m else ""
        address = ""
        addr_el = page.locator('button[data-item-id="address"]').first
        if addr_el.count():
            address = (addr_el.get_attribute("aria-label") or "").replace("Address: ", "").strip()
        rating = ""
        rating_el = page.locator('div.F7nice span[aria-hidden="true"]').first
        if rating_el.count():
            rating = rating_el.inner_text().strip()
        return {"name": name, "website": website, "phone": phone,
                "address": address, "rating": rating}
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Stage 2: email extraction from business websites (thread pool)
# ---------------------------------------------------------------------------
def extract_emails_from_site(url: str) -> list[str]:
    """Fetch a site's homepage + common contact pages and return deduped,
    junk-filtered emails found in mailto: links and visible text."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    base = f"{urlsplit(url).scheme}://{urlsplit(url).netloc}"
    emails: list[str] = []
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    for path in CONTACT_PATHS:
        page_url = url if path == "" else urljoin(base, path)
        time.sleep(random.uniform(*SITE_DELAY_RANGE))
        try:
            resp = session.get(page_url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            if resp.status_code != 200 or "text/html" not in resp.headers.get("Content-Type", "html"):
                continue
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.select('a[href^="mailto:"]'):
                addr = a["href"][7:].split("?")[0].strip()
                emails.extend(EMAIL_RE.findall(addr))
            emails.extend(EMAIL_RE.findall(soup.get_text(" ")))
        except requests.RequestException:
            continue  # dead page/site — move on
        except Exception:
            continue  # malformed HTML etc.

    return _clean_emails(emails)


def _clean_emails(emails: list[str]) -> list[str]:
    """Lowercase, strip trailing dots, drop asset filenames and placeholder
    domains, dedupe preserving order."""
    seen = []
    for email in emails:
        e = email.lower().strip().strip(".")
        if "@" not in e:
            continue
        local, _, domain = e.rpartition("@")
        if not local or "." not in domain:
            continue
        if e.endswith(JUNK_EXTENSIONS):
            continue
        if any(domain == d or domain.endswith("." + d) for d in JUNK_DOMAINS):
            continue
        if len(e) > 60 or len(local) > 40:  # minified-JS garbage
            continue
        if e not in seen:
            seen.append(e)
    return seen


# ---------------------------------------------------------------------------
# Merge / dedupe / output
# ---------------------------------------------------------------------------
def _dedupe_key(listing: dict) -> str:
    """Same business may appear under multiple categories. Collapse on
    normalized website, falling back to name+phone."""
    site = listing["website"]
    if site:
        parts = urlsplit(site if site.startswith("http") else "https://" + site)
        host = parts.netloc.lower().removeprefix("www.")
        return "site:" + host + parts.path.rstrip("/")
    phone_digits = re.sub(r"\D", "", listing["phone"])
    return f"namephone:{listing['name'].lower()}|{phone_digits}"


CSV_FIELDS = ["category", "location", "name", "website", "phone", "address",
              "rating", "emails"]


def _write_csv(rows: list[dict], path: str) -> None:
    """Rewrite the output CSV with everything collected so far. Called
    throughout the run so a crash or Ctrl+C never loses the data."""
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            out = {k: row.get(k, "") for k in CSV_FIELDS}
            out["emails"] = ";".join(row.get("emails") or [])
            writer.writerow(out)


def run_scrape(categories, locations, limit=50, max_leads=None,
               fetch_emails=True, output_path=None, progress=None) -> list[dict]:
    """Programmatic entry point shared by the CLI and the web worker.

    Runs Stage 1 (Maps) then Stage 2 (website emails) and returns the list of
    lead dicts. `max_leads` caps the total unique listings collected (used to
    enforce per-user daily quotas); `progress(phase, done, total, msg)` is an
    optional callback for job tracking; `output_path` enables incremental CSV
    checkpointing so a crash never loses data."""
    def _emit(phase, done, total, msg):
        if progress:
            try:
                progress(phase, done, total, msg)
            except Exception:
                pass  # progress reporting must never break the scrape

    categories = [c for c in categories if c]
    locations = [l for l in locations if l]
    total_queries = len(categories) * len(locations)

    # --- Stage 1: Maps, one query at a time (single shared browser) --------
    merged: dict[str, dict] = {}  # dedupe key -> listing (first cat/loc wins)
    query_num = 0
    capped = False
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        for location in locations:
            if capped:
                break
            for category in categories:
                query_num += 1
                _emit("maps", query_num, total_queries, f"{category} in {location}")
                listings = scrape_maps(f"{category} in {location}", limit, browser)
                for listing in listings:
                    if not listing["name"]:
                        continue
                    listing["category"] = category
                    listing["location"] = location
                    key = _dedupe_key(listing)
                    if key not in merged:
                        merged[key] = listing
                    if max_leads and len(merged) >= max_leads:
                        capped = True  # hit the quota cap — stop collecting
                        break
                if output_path:
                    _write_csv(list(merged.values()), output_path)  # checkpoint
                if capped:
                    break
                if query_num < total_queries:
                    time.sleep(random.uniform(*MAPS_SEARCH_DELAY))
        browser.close()

    rows = list(merged.values())
    if max_leads:
        rows = rows[:max_leads]
    for row in rows:
        row.setdefault("emails", [])

    # --- Stage 2: websites, concurrent ------------------------------------
    if fetch_emails:
        with_site = [r for r in rows if r["website"]]
        if with_site:
            done = 0
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
                futures = {pool.submit(extract_emails_from_site, r["website"]): r
                           for r in with_site}
                for future in as_completed(futures):
                    row = futures[future]
                    done += 1
                    try:
                        row["emails"] = future.result()
                    except Exception:
                        row["emails"] = []
                    _emit("emails", done, len(with_site), row["name"])
                    if output_path and done % 50 == 0:  # checkpoint
                        _write_csv(rows, output_path)

    if output_path:
        _write_csv(rows, output_path)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape Google Maps listings by category+location, then "
                    "harvest emails from each business website.")
    parser.add_argument("--categories", default=",".join(DEFAULT_CATEGORIES),
                        help='comma-separated, e.g. "interior designers,dentists"')
    parser.add_argument("--location", "--locations", dest="location", default="",
                        help='comma-separated, e.g. "Kanpur" or "Kanpur,London,New York"')
    parser.add_argument("--worldwide", action="store_true",
                        help=f"search a built-in list of {len(DEFAULT_WORLD_CITIES)} "
                             "major cities across every continent")
    parser.add_argument("--india", action="store_true",
                        help=f"search a built-in list of {len(INDIA_TIER1_TIER2_CITIES)} "
                             "Indian Tier 1 + Tier 2 cities")
    parser.add_argument("--limit", type=int, default=50,
                        help="max results per category per location")
    parser.add_argument("--output", default="leads.csv", help="output CSV path")
    args = parser.parse_args()

    categories = [c.strip() for c in args.categories.split(",") if c.strip()]
    locations = [l.strip() for l in args.location.split(",") if l.strip()]
    if args.worldwide:
        locations = DEFAULT_WORLD_CITIES + [l for l in locations
                                            if l not in DEFAULT_WORLD_CITIES]
    if args.india:
        locations = INDIA_TIER1_TIER2_CITIES + [l for l in locations
                                                if l not in INDIA_TIER1_TIER2_CITIES]
    if not locations:
        parser.error("pass --location (one or more, comma-separated), "
                     "--india, or --worldwide")

    total_queries = len(categories) * len(locations)
    print(f"{len(categories)} categories x {len(locations)} locations = "
          f"{total_queries} Maps searches (limit {args.limit} each)")
    if total_queries > 40:
        print("Heads up: that's a big run — expect roughly "
              f"{total_queries * 40 // 60} or more minutes for the Maps stage alone.")

    # Stage 1 (Maps) + Stage 2 (emails) live in run_scrape() so the web
    # worker can reuse them; the CLI just feeds it the parsed args and prints
    # progress to the console as it goes.
    def _console(phase, done, total, msg):
        print(f"  [{phase} {done}/{total}] {msg}")

    rows = run_scrape(categories, locations, limit=args.limit,
                      output_path=args.output, progress=_console)

    # --- Summary ------------------------------------------------------------
    with_emails = sum(1 for r in rows if r["emails"])
    print(f"\n{'=' * 50}")
    print(f"Done. Wrote {len(rows)} listings to {args.output}")
    print(f"Listings with emails: {with_emails}")
    print("Per category:")
    for category in categories:
        cat_rows = [r for r in rows if r["category"] == category]
        cat_emails = sum(1 for r in cat_rows if r["emails"])
        print(f"  {category}: {len(cat_rows)} listings, {cat_emails} with emails")
    if len(locations) > 1:
        print("Per location:")
        for location in locations:
            loc_rows = [r for r in rows if r["location"] == location]
            loc_emails = sum(1 for r in loc_rows if r["emails"])
            print(f"  {location}: {len(loc_rows)} listings, {loc_emails} with emails")


if __name__ == "__main__":
    main()
