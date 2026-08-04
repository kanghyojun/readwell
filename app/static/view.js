// 앵커 점프(+하이라이트), 탭 전환, 추가 질문, 진행 상태 폴링.
(function () {
  "use strict";

  // 추출·분석은 서버 백그라운드에서 돈다. 상태가 바뀌면 다시 그린다.
  // 뷰가 서버 렌더링이라 부분 갱신보다 리로드가 단순하고 어긋날 일이 없다.
  var status = document.body.getAttribute("data-status");
  if (status === "extracting" || status === "analyzing") {
    var timer = setInterval(function () {
      fetch("/view/" + window.SESSION_ID + "/status")
        .then(function (r) { return r.json(); })
        .then(function (s) {
          if (s.status !== status) {
            clearInterval(timer);
            location.reload();
          }
        })
        .catch(function () { /* 일시적 실패는 다음 주기에 다시 시도 */ });
    }, 2000);
  }

  // --- 번역: 문단마다 따로 도착한다 ---------------------------------------
  // 분석과 달리 리로드하지 않는다. 조각이 들어올 때마다 새로고침하면 읽던 자리를
  // 잃는다. 새로 온 문단만 원문 바로 아래에 꽂는다.

  function insertTranslations(paragraphs) {
    var added = 0;
    Object.keys(paragraphs).forEach(function (locator) {
      if (document.querySelector('[data-tr-for="' + locator + '"]')) return;
      var source = document.getElementById(locator);
      if (!source) return;
      var el = document.createElement("p");
      el.className = "para-tr";
      el.setAttribute("data-tr-for", locator);
      el.textContent = paragraphs[locator];
      source.insertAdjacentElement("afterend", el);
      added += 1;
    });
    return added;
  }

  function renderProgress(body) {
    var box = document.getElementById("tr-progress");
    if (!box) return;
    if (body.note) { box.textContent = body.note; return; }
    var total = document.querySelectorAll(".para").length;
    var count = Object.keys(body.paragraphs || {}).length;
    box.textContent = body.done ? "" : "번역 " + count + "/" + total;
  }

  function pollTranslations() {
    fetch("/view/" + window.SESSION_ID + "/translations")
      .then(function (r) { return r.json(); })
      .then(function (body) {
        insertTranslations(body.paragraphs || {});
        renderProgress(body);
        if (body.done && !isPending()) {
          clearInterval(trTimer);
          if (Object.keys(body.paragraphs || {}).length) showToggle();
        } else {
          showToggle();
        }
      })
      .catch(function () { /* 다음 주기에 다시 시도 */ });
  }

  function showToggle() {
    var toggle = document.getElementById("tr-toggle");
    if (toggle) toggle.hidden = false;
  }

  function isPending() {
    var s = document.body.getAttribute("data-status");
    return s === "extracting" || s === "analyzing";
  }

  var trTimer = null;
  if (window.HAS_TRANSLATION || isPending()) {
    if (window.HAS_TRANSLATION) showToggle();
    trTimer = setInterval(pollTranslations, 2000);
    pollTranslations();
  }

  // 원문/번역 보기 전환. 실제 숨김은 body 클래스로 CSS가 처리한다.
  var toggleBox = document.getElementById("tr-toggle");
  if (toggleBox) {
    toggleBox.addEventListener("click", function (e) {
      var btn = e.target.closest("button[data-mode]");
      if (!btn) return;
      var mode = btn.getAttribute("data-mode");
      document.body.classList.remove("tr-mode-source", "tr-mode-target");
      if (mode !== "both") document.body.classList.add("tr-mode-" + mode);
      toggleBox.querySelectorAll("button").forEach(function (b) {
        b.classList.toggle("active", b === btn);
      });
    });
  }

  // 분석 항목의 locator 앵커를 누르면 좌측 원문 해당 문단으로 스크롤 + 강조.
  function jumpTo(locator) {
    var el = document.getElementById(locator);
    // 번역만 보는 중이면 원문 문단은 숨어 있다. 짝이 되는 번역문으로 간다.
    if (el && !el.offsetParent) {
      el = document.querySelector('[data-tr-for="' + locator + '"]') || el;
    }
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.classList.remove("flash");
    void el.offsetWidth; // 리플로우로 애니메이션 재시작
    el.classList.add("flash");
  }

  document.addEventListener("click", function (e) {
    var a = e.target.closest("a.anchor");
    if (!a) return;
    var href = a.getAttribute("href") || "";
    if (href.charAt(0) === "#") {
      e.preventDefault();
      jumpTo(href.slice(1));
    }
  });

  // 탭 전환
  var tabs = document.querySelectorAll(".tab");
  tabs.forEach(function (tab) {
    tab.addEventListener("click", function () {
      var name = tab.getAttribute("data-tab");
      tabs.forEach(function (t) { t.classList.remove("active"); });
      tab.classList.add("active");
      document.querySelectorAll(".panel").forEach(function (p) {
        p.classList.toggle("active", p.getAttribute("data-panel") === name);
      });
    });
  });

  // 추가 질문
  var form = document.getElementById("ask-form");
  if (form) {
    var input = document.getElementById("ask-input");
    var kind = document.getElementById("ask-kind");
    var log = document.getElementById("ask-log");
    var btn = form.querySelector("button");

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var question = (input.value || "").trim();
      if (!question) return;
      btn.disabled = true;
      btn.textContent = "생각 중…";

      fetch("/view/" + window.SESSION_ID + "/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: question, kind: kind.value }),
      })
        .then(function (r) { return r.json().then(function (b) { return { ok: r.ok, b: b }; }); })
        .then(function (res) {
          var li = document.createElement("li");
          if (!res.ok || res.b.error) {
            li.innerHTML = '<div class="q">[' + kind.value + "] " + escapeHtml(question) +
              '</div><div class="a">오류: ' + escapeHtml(res.b.error || "실패") + "</div>";
          } else {
            var locs = (res.b.locators || []).map(function (l) {
              return '<a class="anchor" href="#' + l + '">' + l + "</a>";
            }).join(" ");
            li.innerHTML = '<div class="q">[' + escapeHtml(res.b.kind) + "] " + escapeHtml(question) +
              '</div><div class="a">' + escapeHtml(res.b.answer) + " " + locs + "</div>";
          }
          log.appendChild(li);
          input.value = "";
        })
        .catch(function () {
          var li = document.createElement("li");
          li.textContent = "요청 실패";
          log.appendChild(li);
        })
        .finally(function () {
          btn.disabled = false;
          btn.textContent = "보내기";
        });
    });
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
})();
