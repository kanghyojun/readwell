// Firefox는 browser, Chrome은 chrome 네임스페이스. 둘 다 Promise를 돌려준다.
const api = globalThis.browser ?? globalThis.chrome;

const DEFAULT_ENDPOINT = "http://localhost:2100";
const input = document.getElementById("endpoint");
const saved = document.getElementById("saved");

api.storage.sync.get("endpoint").then(({ endpoint }) => {
  input.value = endpoint || DEFAULT_ENDPOINT;
});

document.getElementById("save").addEventListener("click", async () => {
  const value = input.value.trim().replace(/\/+$/, "") || DEFAULT_ENDPOINT;
  await api.storage.sync.set({ endpoint: value });
  saved.textContent = "저장됨";
  setTimeout(() => (saved.textContent = ""), 1500);
});
