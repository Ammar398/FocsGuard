/**
 * FocusGuard browser extension - service worker.
 *
 * WHAT THIS SENDS
 *   A domain and at most one coarse path segment, for the active tab of the
 *   focused window, while monitoring is on.
 *
 *       https://www.youtube.com/watch?v=ABC&list=PRIVATE#t=42
 *         ->  { domain: "youtube.com", path_prefix: "/watch" }
 *
 * WHAT IT NEVER SENDS
 *   Full URLs, query strings, fragments, page content, form data, cookies,
 *   history, or anything from a tab that is not the active one.
 *
 * WHY MINIMISE HERE RATHER THAN IN THE AGENT
 *   Because "the agent strips it" still means the full URL crossed a process
 *   boundary and existed in another program's memory and possibly its logs.
 *   Minimising in the extension means the query string never leaves the browser
 *   at all. Query strings routinely carry search terms, document IDs, email
 *   addresses and session tokens - a manager does not need any of that to see
 *   that 45 minutes went to YouTube (spec sections 20, 35).
 *
 * The agent independently rejects anything that still looks like a full URL, so
 * a regression here fails loudly instead of quietly leaking.
 */

const AGENT_URL = "http://127.0.0.1:48213";
const POLL_ALARM = "focusguard-poll";
const POLL_MINUTES = 0.25; // ~15s; MV3 service workers sleep, so an alarm is required

/** Browser-internal pages. Never tracked - a user's local files are not our business. */
const IGNORED_SCHEMES = [
  "chrome:", "chrome-extension:", "edge:", "extension:",
  "about:", "file:", "devtools:", "view-source:", "moz-extension:",
];

const STRIPPED_PREFIXES = ["www.", "m.", "mobile."];

/**
 * Reduce a URL to { domain, path_prefix }, or null if it must not be tracked.
 * Mirrors normalise_domain() / coarse_path_prefix() in
 * shared/focusguard_shared/utilities/urls.py - tests/test_urls.py pins the
 * behaviour both sides must share.
 */
function minimiseUrl(rawUrl) {
  if (!rawUrl) return null;

  let parsed;
  try {
    parsed = new URL(rawUrl);
  } catch {
    return null;
  }

  if (IGNORED_SCHEMES.includes(parsed.protocol)) return null;

  let host = (parsed.hostname || "").toLowerCase().replace(/\.+$/, "");
  if (!host || !host.includes(".")) return null; // rejects localhost and malformed hosts

  for (const prefix of STRIPPED_PREFIXES) {
    if (host.startsWith(prefix) && host.length > prefix.length) {
      host = host.slice(prefix.length);
      break;
    }
  }

  // Only the FIRST path segment. Enough to tell youtube.com/watch from
  // youtube.com/results, while revealing nothing about which video, document or
  // profile is open.
  const segments = parsed.pathname.split("/").filter(Boolean);
  const pathPrefix = segments.length ? "/" + segments[0].slice(0, 64) : null;

  return { domain: host, path_prefix: pathPrefix };
}

async function getSettings() {
  const stored = await chrome.storage.local.get(["token", "enabled"]);
  return {
    token: stored.token || "",
    enabled: stored.enabled !== false, // default on, but always visible in the popup
  };
}

/** Report the active tab of the focused window. */
async function reportActiveTab() {
  const { token, enabled } = await getSettings();
  if (!token || !enabled) return;

  // lastFocusedWindow keeps this to the window the user is actually looking at:
  // a video playing in a background window is not what they are doing.
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!tab || !tab.url) return;

  const minimised = minimiseUrl(tab.url);
  if (!minimised) return;

  try {
    await fetch(`${AGENT_URL}/activity`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-FocusGuard-Token": token },
      body: JSON.stringify({ ...minimised, active: true }),
    });
    await chrome.storage.local.set({ lastReport: Date.now(), lastError: null });
  } catch (error) {
    // The agent simply may not be running. That is normal and not an error the
    // user needs to see repeatedly, so it is recorded for the popup only.
    await chrome.storage.local.set({ lastError: String(error) });
  }
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create(POLL_ALARM, { periodInMinutes: POLL_MINUTES });
});
chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create(POLL_ALARM, { periodInMinutes: POLL_MINUTES });
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === POLL_ALARM) reportActiveTab();
});

// Report promptly on a switch as well as on the timer, so short visits are not
// missed between polls.
chrome.tabs.onActivated.addListener(reportActiveTab);
chrome.tabs.onUpdated.addListener((_id, changeInfo, tab) => {
  if (changeInfo.status === "complete" && tab.active) reportActiveTab();
});
chrome.windows.onFocusChanged.addListener((windowId) => {
  if (windowId !== chrome.windows.WINDOW_ID_NONE) reportActiveTab();
});
