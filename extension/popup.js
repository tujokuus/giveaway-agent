const statusElement = document.querySelector("#status");

document.querySelector("#capture").addEventListener("click", async () => {
  statusElement.textContent = "Capturing…";
  const response = await chrome.runtime.sendMessage({ type: "capture_current_tab" });
  statusElement.textContent = response?.ok
    ? `Snapshot stored for task ${response.taskId}.`
    : `Capture failed: ${response?.error || "unknown error"}`;
});

document.querySelector("#settings").addEventListener("click", () => {
  chrome.runtime.openOptionsPage();
});
