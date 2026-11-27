/**
 * FocusGuard popup - the visible monitoring indicator (spec sections 34, 62).
 *
 * Monitoring must never be hidden. This panel shows, at a glance, whether the
 * agent is running, whether reporting is on, and when the last report was sent -
 * and it lets the user pause it at any time, from here, without hunting through
 * settings.
 */

const AGENT_URL = "http://127.0.0.1:48213";
const HELPER_URL = "http://127.0.0.1:48214";

const dot = document.getElementById("dot");
const heading = document.getElementById("heading");
const agentEl = document.getElementById("agent");
const sessionEl = document.getElementById("session");
const stateEl = document.getElementById("state");
const lastEl = document.getElementById("last");
const toggleBtn = document.getElementById("toggle");
const tokenInput = document.getElementById("token");
const saveBtn = document.getElementById("save");
const tokenHint = document.getElementById("tokenHint");

function relative(timestamp) {
  if (!timestamp) return "never";
  const seconds = Math.round((Date.now() - timestamp) / 1000);
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  return `${Math.round(seconds / 3600)}h ago`;
}

async function refresh() {
  const { token, enabled, lastReport } = await chrome.storage.local.get([
    "token", "enabled", "lastReport",
  ]);
  const on = enabled !== false;

  stateEl.textContent = on ? "active" : "paused";
  lastEl.textContent = relative(lastReport);
  toggleBtn.textContent = on ? "Pause monitoring" : "Resume monitoring";
  tokenInput.value = token || "";

  if (!token) {
    tokenHint.style.color = "#d97706";
    tokenHint.textContent =
      "A pairing token is required before anything is sent. Run focusguard-token.";
  }

  const ok = async (url) => {
    try {
      return (await fetch(url, { cache: "no-store" })).ok;
    } catch {
      return false;
    }
  };
  // The Helper is always running once FocusGuard is installed; the agent only
  // while a focus session is on.
  const reachable = await ok(`${HELPER_URL}/status`);
  const inSession = await ok(`${AGENT_URL}/status`);

  agentEl.textContent = reachable ? "running" : "not running";
  sessionEl.textContent = inSession ? "on - sites are counted" : "none right now";

  // The indicator is green ONLY when data is genuinely flowing. Anything else -
  // paused, no token, agent down - must not look like normal operation.
  if (reachable && on && token) {
    dot.className = "dot on";
    heading.textContent = "FocusGuard - monitoring";
  } else if (reachable && (!on || !token)) {
    dot.className = "dot warn";
    heading.textContent = token ? "FocusGuard - paused" : "FocusGuard - setup needed";
  } else {
    dot.className = "dot off";
    heading.textContent = "FocusGuard - not running on this computer";
  }
}

toggleBtn.addEventListener("click", async () => {
  const { enabled } = await chrome.storage.local.get("enabled");
  await chrome.storage.local.set({ enabled: enabled === false });
  refresh();
});

saveBtn.addEventListener("click", async () => {
  await chrome.storage.local.set({ token: tokenInput.value.trim() });
  saveBtn.textContent = "Saved";
  setTimeout(() => (saveBtn.textContent = "Save token"), 1200);
  refresh();
});

refresh();
