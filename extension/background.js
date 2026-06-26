// Minimal service worker: the real work runs in runner.html (a full extension
// page with a normal, long lifetime — unlike a popup it isn't killed when it
// loses focus). Clicking the toolbar icon opens or focuses that page.

const RUNNER_URL = chrome.runtime.getURL("runner.html");

chrome.action.onClicked.addListener(async () => {
  const tabs = await chrome.tabs.query({ url: RUNNER_URL });
  if (tabs.length) {
    await chrome.tabs.update(tabs[0].id, { active: true });
    await chrome.windows.update(tabs[0].windowId, { focused: true });
  } else {
    await chrome.tabs.create({ url: RUNNER_URL });
  }
});
