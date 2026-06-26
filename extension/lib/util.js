// Pure helpers ported from maps_email_scraper.py so the extension produces the
// same fields, cleaning, and dedupe behavior as the Python pipeline.

export const EMAIL_RE = /[\w.+-]+@[\w-]+\.[\w.-]+/g;
const PHONE_RE = /\+?\d[\d\s\-()]{7,}\d/;

const JUNK_EXTENSIONS = [".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
  ".css", ".js", ".ico", ".woff", ".woff2", ".ttf", ".mp4", ".pdf", ".webm"];
const JUNK_DOMAINS = ["example.com", "example.org", "email.com", "domain.com",
  "yourdomain.com", "sentry.io", "wixpress.com", "sentry-next.wixpress.com",
  "mysite.com", "company.com", "godaddy.com", "placeholder.com"];

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Sponsored listings link through google.com/aclk trackers; recover the real
 *  destination from adurl/q, otherwise drop it so we don't harvest Google's
 *  own emails. */
export function cleanWebsite(url) {
  if (!url) return "";
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return url;
  }
  const host = parsed.hostname.toLowerCase();
  if (host === "google.com" || host.endsWith(".google.com")) {
    for (const param of ["adurl", "q"]) {
      const candidate = parsed.searchParams.get(param);
      if (candidate && candidate.startsWith("http")) return candidate;
    }
    return "";
  }
  return url;
}

/** Heuristically pull phone + address out of a Maps result card's detail
 *  lines (e.g. "Dentist · 12 Mall Road", "Open ⋅ Closes 9pm · 098765 43210"). */
export function parseCardLines(lines) {
  let phone = "";
  let address = "";
  const segments = [];
  for (const line of lines || []) {
    for (const s of line.split(/[·⋅|]/)) {
      const t = s.trim();
      if (t) segments.push(t);
    }
  }
  const hoursWords = ["open", "close", "24 hours", "opens", "temporarily", "permanently"];
  for (const seg of segments) {
    if (!phone) {
      const m = seg.match(PHONE_RE);
      // avoid mistaking "4.5 (123)" ratings or years for phone numbers
      if (m && (m[0].match(/\d/g) || []).length >= 8) {
        phone = m[0].trim();
        continue;
      }
    }
    const low = seg.toLowerCase();
    if (hoursWords.some((w) => low.includes(w))) continue;
    if (/^[\d.,()\s★]+$/.test(seg)) continue; // pure numbers = rating/reviews
    if ((/\d/.test(seg) || seg.includes(",")) && seg.length > address.length) {
      address = seg;
    }
  }
  return { phone, address };
}

/** Same business can appear under multiple categories; collapse on normalized
 *  website, falling back to name+phone. Mirrors _dedupe_key in Python. */
export function dedupeKey(listing) {
  const site = listing.website || "";
  if (site) {
    let parsed;
    try {
      parsed = new URL(site.startsWith("http") ? site : "https://" + site);
    } catch {
      parsed = null;
    }
    if (parsed) {
      let host = parsed.hostname.toLowerCase();
      if (host.startsWith("www.")) host = host.slice(4);
      return "site:" + host + parsed.pathname.replace(/\/+$/, "");
    }
  }
  const phoneDigits = (listing.phone || "").replace(/\D/g, "");
  return `namephone:${(listing.name || "").toLowerCase()}|${phoneDigits}`;
}

/** Lowercase, strip junk extensions/placeholder domains, dedupe in order.
 *  Mirrors _clean_emails in Python. */
export function cleanEmails(emails) {
  const seen = [];
  for (const raw of emails) {
    const e = raw.toLowerCase().trim().replace(/\.+$/, "");
    if (!e.includes("@")) continue;
    const at = e.lastIndexOf("@");
    const local = e.slice(0, at);
    const domain = e.slice(at + 1);
    if (!local || !domain.includes(".")) continue;
    if (JUNK_EXTENSIONS.some((ext) => e.endsWith(ext))) continue;
    if (JUNK_DOMAINS.some((d) => domain === d || domain.endsWith("." + d))) continue;
    if (e.length > 60 || local.length > 40) continue; // minified-JS garbage
    if (!seen.includes(e)) seen.push(e);
  }
  return seen;
}
