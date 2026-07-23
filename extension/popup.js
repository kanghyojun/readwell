// 현재 탭 URL을 readwell 웹서비스 /read로 보내고, 응답 뷰를 새 탭으로 연다.
const DEFAULT_ENDPOINT = "http://100.99.117.44:2100";

async function getEndpoint() {
  const { endpoint } = await chrome.storage.sync.get("endpoint");
  return (endpoint || DEFAULT_ENDPOINT).replace(/\/+$/, "");
}

const readBtn = document.getElementById("read");
const statusEl = document.getElementById("status");

readBtn.addEventListener("click", async () => {
  readBtn.disabled = true;
  statusEl.textContent = "추출·분석 중… (수십 초 걸릴 수 있어요)";
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.url) throw new Error("탭 URL을 못 읽음");
    const endpoint = await getEndpoint();

    const res = await fetch(endpoint + "/read", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: tab.url }),
    });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();

    // viewUrl이 절대경로면 그대로, 아니면 endpoint로 조립.
    const viewUrl =
      data.viewUrl && /^https?:\/\//.test(data.viewUrl)
        ? data.viewUrl
        : endpoint + "/view/" + data.id;

    await chrome.tabs.create({ url: viewUrl });
    statusEl.textContent = data.error ? "분석 일부 실패: " + data.error : "새 탭에서 열었어요.";
  } catch (e) {
    statusEl.textContent = "실패: " + (e && e.message ? e.message : e);
  } finally {
    readBtn.disabled = false;
  }
});

document.getElementById("opt").addEventListener("click", (e) => {
  e.preventDefault();
  chrome.runtime.openOptionsPage();
});
