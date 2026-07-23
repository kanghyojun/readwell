const DEFAULT_ENDPOINT = "http://100.99.117.44:2100";
const input = document.getElementById("endpoint");
const saved = document.getElementById("saved");

chrome.storage.sync.get("endpoint").then(({ endpoint }) => {
  input.value = endpoint || DEFAULT_ENDPOINT;
});

document.getElementById("save").addEventListener("click", async () => {
  const value = input.value.trim().replace(/\/+$/, "") || DEFAULT_ENDPOINT;
  await chrome.storage.sync.set({ endpoint: value });
  saved.textContent = "저장됨";
  setTimeout(() => (saved.textContent = ""), 1500);
});
