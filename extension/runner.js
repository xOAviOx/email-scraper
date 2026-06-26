// Orchestrator for the LeadHarvest Collector. Runs in a full extension page
// (long lifetime), so it can drive a background Maps tab via chrome.scripting,
// harvest emails over fetch, and post leads back — all from the user's own IP.

import { Api } from "./lib/api.js";
import { cleanWebsite, parseCardLines, dedupeKey, sleep } from "./lib/util.js";
import { harvestSite } from "./lib/emails.js";
import { pageScrapeSearch, pageScrapeSinglePlace } from "./lib/pagescrape.js";

const MAPS_SEARCH_DELAY = [2000, 4000]; // between Maps queries
const EMAIL_CONCURRENCY = 5;

// --- DOM handles -----------------------------------------------------------
const $ = (id) => document.getElementById(id);
const els = {
  server: $("server"), token: $("token"), connect: $("connect"), signout: $("signout"),
  status: $("status"), start: $("start"), stop: $("stop"), phase: $("phase"),
  progress: $("progress"), log: $("log"),
};

let api = null;
let running = false;   // a collecting session is active
let stopReq = false;   // user asked to stop after the current step

// --- small helpers ---------------------------------------------------------
function log(msg) {
  const time = new Date().toLocaleTimeString();
  els.log.textContent += `[${time}] ${msg}\n`;
  els.log.scrollTop = els.log.scrollHeight;
}

function setStatus(text, kind = "") {
  els.status.textContent = text;
  els.status.className = "pill" + (kind ? " " + kind : "");
}

function setProgress(done, total) {
  els.progress.style.width = total ? `${Math.round((done / total) * 100)}%` : "0%";
}

/** Run `worker` over `items` with bounded concurrency. */
async function pool(items, size, worker) {
  let i = 0;
  const runners = Array.from({ length: Math.min(size, items.length) }, async () => {
    while (i < items.length && !stopReq) {
      const idx = i++;
      try {
        await worker(items[idx], idx);
      } catch {
        /* one bad site shouldn't sink the batch */
      }
    }
  });
  await Promise.all(runners);
}

function waitForTabLoad(tabId, timeout = 30000) {
  return new Promise((resolve) => {
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      chrome.tabs.onUpdated.removeListener(listener);
      clearTimeout(timer);
      resolve();
    };
    const listener = (id, info) => {
      if (id === tabId && info.status === "complete") finish();
    };
    chrome.tabs.onUpdated.addListener(listener);
    const timer = setTimeout(finish, timeout);
  });
}

async function inject(tabId, func, args = []) {
  const [res] = await chrome.scripting.executeScript({ target: { tabId }, func, args });
  return res ? res.result : null;
}

async function navigate(tabId, url) {
  await chrome.tabs.update(tabId, { url });
  await waitForTabLoad(tabId);
}

// --- settings persistence --------------------------------------------------
async function loadSettings() {
  const { server, token } = await chrome.storage.local.get(["server", "token"]);
  if (server) els.server.value = server;
  if (token) els.token.value = token;
}

async function saveSettings() {
  await chrome.storage.local.set({
    server: els.server.value.trim(),
    token: els.token.value.trim(),
  });
}

// --- connect ---------------------------------------------------------------
async function connect() {
  const server = els.server.value.trim();
  const token = els.token.value.trim();
  if (!server || !token) {
    setStatus("enter server + token", "err");
    return;
  }
  await saveSettings();
  api = new Api(server, token);
  setStatus("connecting…", "run");
  try {
    const me = await api.me();
    setStatus(`${me.email} · ${me.remaining_quota}/${me.daily_quota} left today`, "ok");
    log(`Connected as ${me.email}. Quota remaining today: ${me.remaining_quota}.`);
    els.start.disabled = false;
    els.signout.disabled = false;
  } catch (e) {
    api = null;
    els.start.disabled = true;
    els.signout.disabled = true;
    setStatus("connection failed", "err");
    log(`Connect failed: ${e.message}`);
  }
}

// --- sign out --------------------------------------------------------------
async function signOut() {
  if (running) {
    log("Stop collecting before signing out.");
    return;
  }
  await chrome.storage.local.remove("token");
  els.token.value = "";
  api = null;
  els.start.disabled = true;
  els.signout.disabled = true;
  setProgress(0, 0);
  els.phase.textContent = "";
  setStatus("not connected");
  log("Signed out. Token cleared from this browser.");
}

// --- scraping one Maps query ----------------------------------------------
async function scrapeQuery(tabId, query, limit) {
  const url = `https://www.google.com/maps/search/${encodeURIComponent(query)}?hl=en`;
  await navigate(tabId, url);

  let res = await inject(tabId, pageScrapeSearch, [limit]);
  let raw = (res && res.listings) || [];

  if (res && res.note === "no-feed") {
    // Maps jumped straight to a single place page.
    const single = await inject(tabId, pageScrapeSinglePlace);
    if (single && single.name) {
      raw = [{ name: single.name, website: single.website, rating: single.rating,
               lines: [], href: "", _detail: single }];
    }
  }

  const listings = raw.map((x) => {
    const { phone, address } = parseCardLines(x.lines);
    return {
      name: (x.name || "").trim(),
      website: cleanWebsite((x.website || "").trim()),
      phone: x._detail ? x._detail.phone || phone : phone,
      address: x._detail ? x._detail.address || address : address,
      rating: x.rating || "",
      href: x.href || "",
    };
  });

  // Enrich: phone/website live on each place's detail panel, not the cards.
  for (const it of listings) {
    if (stopReq) break;
    if (!it.href || (it.phone && it.website)) continue;
    try {
      await navigate(tabId, it.href);
      const d = await inject(tabId, pageScrapeSinglePlace);
      if (!d) continue;
      if (!it.website && d.website) it.website = cleanWebsite(d.website);
      if (!it.phone && d.phone) it.phone = d.phone;
      if (!it.address && d.address) it.address = d.address;
    } catch {
      /* one bad place page shouldn't sink the query */
    }
  }
  return listings;
}

// --- running one job end to end -------------------------------------------
async function runJob(job) {
  const categories = job.categories.split(",").map((s) => s.trim()).filter(Boolean);
  const locations = job.locations.split(",").map((s) => s.trim()).filter(Boolean);
  const limit = job.limit_per_query;
  const maxLeads = job.max_leads;
  const totalQueries = categories.length * locations.length;

  log(`Job #${job.id}: ${categories.length} categories × ${locations.length} locations ` +
      `(cap ${maxLeads} leads).`);

  const tab = await chrome.tabs.create({ url: "about:blank", active: false });
  const tabId = tab.id;
  const merged = new Map(); // dedupe key -> lead

  try {
    // --- Stage 1: Google Maps ---------------------------------------------
    let queryNum = 0;
    let capped = false;
    els.phase.textContent = "Stage 1 — Google Maps";
    for (const location of locations) {
      if (capped || stopReq) break;
      for (const category of categories) {
        if (capped || stopReq) break;
        queryNum++;
        const query = `${category} in ${location}`;
        log(`  Maps ${queryNum}/${totalQueries}: ${query}`);
        await api.progress(job.id, { phase: "maps", done: queryNum, total: totalQueries });
        setProgress(queryNum, totalQueries);

        let listings = [];
        try {
          listings = await scrapeQuery(tabId, query, limit);
        } catch (e) {
          log(`    (query failed: ${e.message})`);
        }
        for (const listing of listings) {
          if (!listing.name) continue;
          listing.category = category;
          listing.location = location;
          const key = dedupeKey(listing);
          if (!merged.has(key)) merged.set(key, listing);
          if (maxLeads && merged.size >= maxLeads) {
            capped = true;
            break;
          }
        }
        if (queryNum < totalQueries && !capped && !stopReq) {
          await sleep(MAPS_SEARCH_DELAY[0] +
            Math.random() * (MAPS_SEARCH_DELAY[1] - MAPS_SEARCH_DELAY[0]));
        }
      }
    }

    let rows = [...merged.values()];
    if (maxLeads) rows = rows.slice(0, maxLeads);
    rows.forEach((r) => (r.emails = []));
    log(`  Collected ${rows.length} unique listings.`);

    // --- Stage 2: emails ---------------------------------------------------
    const withSite = rows.filter((r) => r.website);
    if (withSite.length && !stopReq) {
      els.phase.textContent = "Stage 2 — emails";
      let done = 0;
      await pool(withSite, EMAIL_CONCURRENCY, async (row) => {
        row.emails = await harvestSite(row.website);
        done++;
        if (done % 3 === 0 || done === withSite.length) {
          await api.progress(job.id, { phase: "emails", done, total: withSite.length });
          setProgress(done, withSite.length);
        }
      });
      log(`  Email pass done (${rows.filter((r) => r.emails.length).length} with emails).`);
    }

    // --- Post results + complete ------------------------------------------
    const payload = rows.map((r) => ({
      category: r.category, location: r.location, name: r.name,
      website: r.website, phone: r.phone, address: r.address,
      rating: r.rating, emails: r.emails || [],
    }));
    if (payload.length) {
      const { written } = await api.postLeads(job.id, payload);
      log(`  Posted ${written} leads to the server.`);
    }
    await api.complete(job.id, {
      result_count: rows.length,
      email_count: rows.filter((r) => r.emails && r.emails.length).length,
    });
    log(`Job #${job.id} done.`);
  } catch (e) {
    log(`Job #${job.id} failed: ${e.message}`);
    try {
      await api.fail(job.id, e.message);
    } catch {
      /* best effort */
    }
  } finally {
    try {
      await chrome.tabs.remove(tabId);
    } catch {
      /* tab may already be gone */
    }
  }
}

// --- main loop -------------------------------------------------------------
async function start() {
  if (!api || running) return;
  running = true;
  stopReq = false;
  els.start.disabled = true;
  els.stop.disabled = false;
  log("Started. Looking for queued jobs…");

  try {
    while (!stopReq) {
      let claim;
      try {
        claim = await api.claim();
      } catch (e) {
        log(`Claim failed: ${e.message}`);
        break;
      }
      if (!claim.job) {
        log("No queued jobs. Create one on the dashboard, then press Start again.");
        break;
      }
      await runJob(claim.job);
      // refresh quota display between jobs
      try {
        const me = await api.me();
        setStatus(`${me.email} · ${me.remaining_quota}/${me.daily_quota} left today`, "ok");
      } catch {
        /* ignore */
      }
    }
  } finally {
    running = false;
    stopReq = false;
    els.start.disabled = false;
    els.stop.disabled = true;
    els.phase.textContent = "";
    setProgress(0, 0);
    log("Stopped.");
  }
}

function stop() {
  if (!running) return;
  stopReq = true;
  els.stop.disabled = true;
  log("Stopping after the current step…");
}

// --- wire up ---------------------------------------------------------------
els.connect.addEventListener("click", connect);
els.signout.addEventListener("click", signOut);
els.start.addEventListener("click", start);
els.stop.addEventListener("click", stop);
window.addEventListener("beforeunload", (e) => {
  if (running) e.preventDefault();
});
loadSettings();
