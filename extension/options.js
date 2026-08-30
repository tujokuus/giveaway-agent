const backend = document.querySelector("#backend");
const token = document.querySelector("#token");
const status = document.querySelector("#status");

chrome.storage.local.get(["backendUrl", "apiToken"]).then((stored) => {
  backend.value = stored.backendUrl || "http://127.0.0.1:8765";
  token.value = stored.apiToken || "";
});

document.querySelector("#save").addEventListener("click", async () => {
  await chrome.storage.local.set({
    backendUrl: backend.value.trim(),
    apiToken: token.value.trim()
  });
  status.textContent = "Saved. Looking for a queued task.";
  chrome.runtime.sendMessage({ type: "poll_now" });
});
