"""One-off: dump what Google Maps result cards actually contain right now."""
from urllib.parse import quote
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page()
    pg.goto("https://www.google.com/maps/search/" + quote("dentists in mumbai") + "?hl=en",
            wait_until="domcontentloaded", timeout=30000)
    pg.wait_for_selector('div[role="feed"]', timeout=15000)
    pg.wait_for_timeout(3000)
    data = pg.evaluate(r"""() => {
        const out = [];
        const links = document.querySelectorAll('div[role="feed"] a.hfpxzc');
        for (const link of Array.from(links).slice(0, 4)) {
            const card = link.closest('div.Nv2PK') || link.parentElement;
            out.push({
                name: link.getAttribute('aria-label'),
                hasWebsiteBtn: !!card.querySelector('a[data-value="Website"]'),
                lines: Array.from(card.querySelectorAll('.W4Efsd')).map(e => e.innerText.trim()).filter(Boolean),
            });
        }
        return {count: links.length, cards: out};
    }""")
    print("total cards:", data["count"])
    for c in data["cards"]:
        print("\nNAME:", c["name"])
        print("  website button on card:", c["hasWebsiteBtn"])
        print("  lines:", c["lines"])
    b.close()
