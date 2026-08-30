const DEFAULT_BACKEND = "http://127.0.0.1:8765";
const POLL_ALARM = "giveaway-agent-poll";
let polling = false;

chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create(POLL_ALARM, { periodInMinutes: 0.5 });
  void pollForTask();
});
chrome.runtime.onStartup.addListener(() => void pollForTask());
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === POLL_ALARM) void pollForTask();
});
chrome.action.onClicked.addListener(() => chrome.runtime.openOptionsPage());
chrome.runtime.onMessage.addListener((message) => {
  if (message?.type === "poll_now") void pollForTask();
});

async function configuration() {
  const stored = await chrome.storage.local.get(["backendUrl", "apiToken"]);
  return {
    backendUrl: (stored.backendUrl || DEFAULT_BACKEND).replace(/\/$/, ""),
    apiToken: stored.apiToken || ""
  };
}

async function apiFetch(path, options = {}) {
  const config = await configuration();
  if (!config.apiToken) throw new Error("Configure the local API token first.");
  return fetch(`${config.backendUrl}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Giveaway-Agent-Token": config.apiToken,
      ...(options.headers || {})
    }
  });
}

async function pollForTask() {
  if (polling) return;
  polling = true;
  try {
    const response = await apiFetch("/api/v1/tasks/next");
    if (!response.ok) throw new Error(`Task poll failed: HTTP ${response.status}`);
    const task = await response.json();
    if (task) await openAndCapture(task);
  } catch (error) {
    console.debug("Giveaway Agent poll:", error.message);
  } finally {
    polling = false;
  }
}

async function openAndCapture(task) {
  const tab = await chrome.tabs.create({ url: task.url, active: true });
  await waitForTab(tab.id, 60000);
  await new Promise((resolve) => setTimeout(resolve, 1200));
  const frameResults = await chrome.scripting.executeScript({
    target: { tabId: tab.id, allFrames: true },
    files: ["content.js"]
  });
  const snapshot = mergeFrames(task, frameResults);
  const response = await apiFetch(`/api/v1/tasks/${task.id}/snapshot`, {
    method: "POST",
    body: JSON.stringify(snapshot)
  });
  if (!response.ok) throw new Error(`Snapshot upload failed: HTTP ${response.status}`);
}

function waitForTab(tabId, timeoutMs) {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener);
      reject(new Error("Page load timed out."));
    }, timeoutMs);
    const listener = (updatedId, changeInfo) => {
      if (updatedId === tabId && changeInfo.status === "complete") {
        clearTimeout(timeout);
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }
    };
    chrome.tabs.onUpdated.addListener(listener);
  });
}

function mergeFrames(task, executionResults) {
  const frames = executionResults
    .filter((item) => item.result && item.result.url.startsWith("http"))
    .map((item) => ({ frameId: item.frameId, ...item.result }));
  const main = frames.find((frame) => frame.frameId === 0) || frames[0];
  if (!main) throw new Error("No readable HTTP frame was found.");
  const prefixItems = (items, frameId) => items.map((item) => ({
    ...item,
    element_ref: `f${frameId}_${item.element_ref}`
  }));
  const manual = frames.some((frame) => frame.manual_verification_required);
  return {
    schema_version: 1,
    task_id: task.id,
    requested_url: task.url,
    final_url: main.url,
    title: main.title,
    captured_at: new Date().toISOString(),
    status: manual ? "manual_verification_required" : "captured",
    manual_verification_required: manual,
    visible_text: frames.map((frame) => frame.visible_text).join("\n").slice(0, 100000),
    fields: frames.flatMap((frame) => prefixItems(frame.fields, frame.frameId)).slice(0, 1000),
    links: frames.flatMap((frame) => prefixItems(frame.links, frame.frameId)).slice(0, 2000),
    buttons: frames.flatMap((frame) => prefixItems(frame.buttons, frame.frameId)).slice(0, 1000),
    iframe_urls: [...new Set(frames.flatMap((frame) => frame.iframe_urls))].slice(0, 500)
  };
}
