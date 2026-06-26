# LeadHarvest Collector (browser extension)

A Chrome/Edge (Manifest V3) extension that runs your LeadHarvest scrapes from
**your own browser and IP** instead of the central server. Each user who installs
it contributes their IP, so Google Maps is far less likely to rate-limit or block
the work.

## How it works

```
LeadHarvest server          Collector extension (your browser/IP)
─────────────────           ─────────────────────────────────────
queue a job  ──────claim──▶  open Google Maps tab, scroll + scrape listings
                            └▶ visit each business site, harvest emails
   central DB  ◀──leads────  POST results back, mark job done (quota spent)
```

The actual Maps page loads and website fetches happen in *your* browser, using
your session and IP. The server only hands out jobs and stores the leads.

## Install (unpacked)

1. Open `chrome://extensions` (or `edge://extensions`).
2. Turn on **Developer mode** (top-right).
3. Click **Load unpacked** and select this `extension/` folder.
4. Click the LeadHarvest icon in the toolbar — the **Collector** tab opens.

## Use

1. On your LeadHarvest dashboard, copy the **Server URL** and **API token**
   (shown in the "Browser extension" card).
2. Paste both into the Collector's **Connection** card and click **Connect**.
   You should see your email and remaining daily quota.
3. Create one or more scrape jobs on the dashboard (categories + locations).
   They start in the `queued` state.
4. Back in the Collector, click **Start collecting**. It claims your queued jobs
   one at a time, scrapes them, and posts the leads back. Watch the Activity log.
5. Download results (CSV / XLSX / JSON) from the dashboard as usual.

**Keep the Collector tab open while it works.** Closing it stops the run. Signing
in to Google Maps once (and clearing any consent prompt) before starting makes
scraping more reliable.

## Permissions

- `<all_urls>` host access — required to fetch arbitrary business websites for
  email extraction (Stage 2) and to inject the scraper into Google Maps tabs.
- `scripting`, `tabs` — to open/drive the background Maps tab.
- `storage` — to remember your server URL and token locally.

## Notes / limitations

- This is the scraping engine; don't also run the server-side `webapp.worker`
  against the same jobs, or both will try to claim them. Pick one.
- Targets Chromium (Chrome/Edge). A Firefox build needs minor manifest tweaks.
- Google occasionally changes Maps' DOM; if scraping returns nothing, the
  selectors in `lib/pagescrape.js` (shared with `maps_email_scraper.py`) may
  need updating.
- Use responsibly — you're accountable for how you contact the people you
  collect (DPDP / GDPR / CAN-SPAM and the source sites' terms).
