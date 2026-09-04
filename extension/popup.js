// 현재 탭 URL을 readwell 웹서비스 /read로 보내고, 응답 뷰를 새 탭으로 연다.
// Firefox는 browser, Chrome은 chrome 네임스페이스. 둘 다 Promise를 돌려준다.
const api = globalThis.browser ?? globalThis.chrome;

const DEFAULT_ENDPOINT = "http://localhost:2100";

async function getEndpoint() {
  const { endpoint } = await api.storage.sync.get("endpoint");
  return (endpoint || DEFAULT_ENDPOINT).replace(/\/+$/, "");
}

const readBtn = document.getElementById("read");
const skimBtn = document.getElementById("skim");
const statusEl = document.getElementById("status");
const questionsEl = document.getElementById("questions");

// 한 줄에 질문 하나. 빈 줄은 버린다. 하나도 없으면 서버가 기본 질문 5개로 읽는다.
function readQuestions() {
  return (questionsEl.value || "")
    .split("\n")
    .map((q) => q.trim())
    .filter((q) => q.length > 0);
}

// 서버가 돌려준 절대 URL이면 그대로, 아니면 endpoint로 조립한다.
function resolveUrl(endpoint, url, path, id) {
  return url && /^https?:\/\//.test(url) ? url : endpoint + path + id;
}

// 두 흐름이 하는 일은 같다. 어디로 POST하고 어느 URL을 여느냐만 다르다.
async function openIn(button, path, body, urlField, viewPath) {
  button.disabled = true;
  statusEl.textContent = "여는 중…";
  try {
    const [tab] = await api.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.url) throw new Error("탭 URL을 못 읽음");
    const endpoint = await getEndpoint();

    const res = await fetch(endpoint + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.assign({ url: tab.url }, body)),
    });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();

    // 작업은 서버가 이어서 한다. 진행 상황은 새 탭에서 보인다.
    await api.tabs.create({
      url: resolveUrl(endpoint, data[urlField], viewPath, data.id),
    });
    statusEl.textContent = "새 탭에서 열었어요.";
  } catch (e) {
    statusEl.textContent = "실패: " + (e && e.message ? e.message : e);
  } finally {
    button.disabled = false;
  }
}

// 훑어보기는 질문을 보내지 않는다. 판단하기 전에 질문을 짜는 건 번거로움이다.
skimBtn.addEventListener("click", () =>
  openIn(skimBtn, "/preview", {}, "previewUrl", "/preview/")
);

readBtn.addEventListener("click", () =>
  openIn(readBtn, "/read", { questions: readQuestions() }, "viewUrl", "/view/")
);

document.getElementById("list").addEventListener("click", async (e) => {
  e.preventDefault();
  await api.tabs.create({ url: (await getEndpoint()) + "/" });
});

document.getElementById("opt").addEventListener("click", (e) => {
  e.preventDefault();
  api.runtime.openOptionsPage();
});
