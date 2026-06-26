// Stage 2 (email harvesting) for the extension. Runs from the Collector page,
// which has <all_urls> host permission — so these cross-origin fetches read the
// response body without CORS restrictions, from the user's own IP.
//
// Service workers / extension pages have no DOMParser-in-worker guarantee, so
// (unlike the BeautifulSoup version) we regex emails straight out of the raw
// HTML — cleanEmails() filters the noise the same way Python does.

import { EMAIL_RE, cleanEmails, sleep } from "./util.js";

const CONTACT_PATHS = ["", "/contact", "/contact-us", "/about"];
const REQUEST_TIMEOUT_MS = 10000;
const SITE_DELAY = [800, 2000]; // randomized politeness delay between fetches

async function fetchHtml(url) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS);
  try {
    const resp = await fetch(url, { signal: ctrl.signal, redirect: "follow" });
    const ctype = resp.headers.get("content-type") || "";
    if (!resp.ok || !ctype.includes("text/html")) return "";
    return await resp.text();
  } catch {
    return ""; // dead site, timeout, TLS error — skip it
  } finally {
    clearTimeout(timer);
  }
}

/** Fetch a site's homepage + common contact pages and return deduped,
 *  junk-filtered emails from both mailto: links and visible text. */
export async function harvestSite(url) {
  if (!/^https?:\/\//i.test(url)) url = "https://" + url;
  let base;
  try {
    base = new URL(url).origin;
  } catch {
    return [];
  }

  const found = [];
  for (const path of CONTACT_PATHS) {
    const pageUrl = path === "" ? url : base + path;
    await sleep(SITE_DELAY[0] + Math.random() * (SITE_DELAY[1] - SITE_DELAY[0]));
    const html = await fetchHtml(pageUrl);
    if (!html) continue;
    for (const m of html.matchAll(/mailto:([^"'?>\s]+)/gi)) {
      try {
        found.push(decodeURIComponent(m[1]));
      } catch {
        found.push(m[1]);
      }
    }
    for (const m of html.matchAll(EMAIL_RE)) found.push(m[0]);
  }
  return cleanEmails(found);
}
