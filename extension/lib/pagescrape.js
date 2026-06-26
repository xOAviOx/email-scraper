// Functions that run INSIDE the Google Maps tab via chrome.scripting.executeScript.
//
// IMPORTANT: each of these is serialized and injected, so it must be fully
// self-contained — it can only use its arguments and page globals, never module
// imports or outer-scope helpers. That's why selectors are inlined here rather
// than shared with util.js. These mirror the Playwright selectors in
// maps_email_scraper.py (role="feed", a.hfpxzc, etc.).

/** Search results page: dismiss consent, scroll the feed to `limit` cards,
 *  then return the raw cards. Returns {listings:[...]} or {note:"no-feed"}
 *  (the caller then falls back to scraping a single-place page). */
export async function pageScrapeSearch(limit) {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // Best-effort cookie-consent dismissal.
  try {
    if (location.hostname.includes("consent.google")) {
      for (const label of ["Accept all", "I agree", "Reject all"]) {
        const btn = [...document.querySelectorAll("button")].find(
          (b) => b.textContent.trim().toLowerCase() === label.toLowerCase()
        );
        if (btn) {
          btn.click();
          await sleep(2500);
          break;
        }
      }
    }
  } catch {
    /* consent handling is best-effort */
  }

  // Wait up to ~15s for the results feed.
  let feed = null;
  for (let i = 0; i < 30; i++) {
    feed = document.querySelector('div[role="feed"]');
    if (feed) break;
    await sleep(500);
  }
  if (!feed) return { note: "no-feed" };

  // Scroll until we have `limit` cards, hit the end, or stop growing.
  let stale = 0;
  let last = 0;
  while (true) {
    const count = document.querySelectorAll('div[role="feed"] a.hfpxzc').length;
    if (count >= limit) break;
    const ended =
      document.querySelector('div[role="feed"] span.HlvSq') ||
      [...document.querySelectorAll("span")].some((s) =>
        /reached the end of the list/i.test(s.textContent)
      );
    if (ended) break;
    if (count === last) {
      stale++;
      if (stale >= 5) break;
    } else {
      stale = 0;
      last = count;
    }
    feed.scrollBy(0, feed.scrollHeight);
    await sleep(900 + Math.random() * 600);
  }

  const listings = [];
  for (const link of document.querySelectorAll('div[role="feed"] a.hfpxzc')) {
    const card = link.closest("div.Nv2PK") || link.parentElement;
    const site = card.querySelector('a[data-value="Website"]');
    const rating = card.querySelector("span.MW4etd");
    const lines = Array.from(card.querySelectorAll(".W4Efsd"))
      .map((e) => e.innerText.trim())
      .filter(Boolean);
    listings.push({
      name: (link.getAttribute("aria-label") || "").trim(),
      website: site ? site.href : "",
      rating: rating ? rating.textContent.trim() : "",
      lines,
      href: link.href || "",
    });
  }
  return { listings: listings.slice(0, limit) };
}

/** A single place's detail panel — used both for the no-feed fallback and to
 *  enrich listings that are missing a phone or website (those only live here,
 *  not on the search cards). */
export function pageScrapeSinglePlace() {
  const nameEl = document.querySelector("h1");
  const name = nameEl ? nameEl.textContent.trim() : "";
  if (!name) return null;

  let website = "";
  const siteEl = document.querySelector('a[data-item-id="authority"]');
  if (siteEl) website = siteEl.getAttribute("href") || "";

  let phone = "";
  const phoneEl = document.querySelector('button[data-item-id^="phone"]');
  if (phoneEl) {
    const src =
      (phoneEl.getAttribute("aria-label") || "") +
      " " +
      (phoneEl.getAttribute("data-item-id") || "");
    const m = src.match(/\+?\d[\d\s\-()]{7,}\d/);
    if (m) phone = m[0].trim();
  }

  let address = "";
  const addrEl = document.querySelector('button[data-item-id="address"]');
  if (addrEl) {
    address = (addrEl.getAttribute("aria-label") || "")
      .replace("Address: ", "")
      .trim();
  }

  let rating = "";
  const ratingEl = document.querySelector('div.F7nice span[aria-hidden="true"]');
  if (ratingEl) rating = ratingEl.textContent.trim();

  return { name, website, phone, address, rating };
}
