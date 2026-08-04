// 현재 탭 URL을 readwell 웹서비스 /read로 보내고, 응답 뷰를 새 탭으로 연다.
// Firefox는 browser, Chrome은 chrome 네임스페이스. 둘 다 Promise를 돌려준다.
const api = globalThis.browser ?? globalThis.chrome;

const DEFAULT_ENDPOINT = "http://localhost:2100";

async function getEndpoint() {
  const { endpoint } = await api.storage.sync.get("endpoint");
  return (endpoint || DEFAULT_ENDPOINT).replace(/\/+$/, "");
}

const readBtn = document.getElementById("read");
const statusEl = document.getElementById("status");
const questionsEl = document.getElementById("questions");

// 한 줄에 질문 하나. 빈 줄은 버린다. 하나도 없으면 서버가 기본 질문 5개로 읽는다.
function readQuestions() {
  return (questionsEl.value || "")
    .split("\n")
    .map((q) => q.trim())
    .filter((q) => q.length > 0);
}

readBtn.addEventListener("click", async () => {
  readBtn.disabled = true;
  statusEl.textContent = "여는 중…";
  try {
    const [tab] = await api.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.url) throw new Error("탭 URL을 못 읽음");
    const endpoint = await getEndpoint();

    const res = await fetch(endpoint + "/read", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: tab.url, questions: readQuestions() }),
    });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();

    // viewUrl이 절대경로면 그대로, 아니면 endpoint로 조립.
    const viewUrl =
      data.viewUrl && /^https?:\/\//.test(data.viewUrl)
        ? data.viewUrl
        : endpoint + "/view/" + data.id;

    // 추출·분석은 서버가 이어서 한다. 진행 상황은 새 탭에서 보인다.
    await api.tabs.create({ url: viewUrl });
    statusEl.textContent = "새 탭에서 열었어요.";
  } catch (e) {
    statusEl.textContent = "실패: " + (e && e.message ? e.message : e);
  } finally {
    readBtn.disabled = false;
  }
});

document.getElementById("list").addEventListener("click", async (e) => {
  e.preventDefault();
  await api.tabs.create({ url: (await getEndpoint()) + "/" });
});

document.getElementById("opt").addEventListener("click", (e) => {
  e.preventDefault();
  api.runtime.openOptionsPage();
});
