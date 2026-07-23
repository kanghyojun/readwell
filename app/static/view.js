// 앵커 점프(+하이라이트), 탭 전환, 추가 질문.
(function () {
  "use strict";

  // 분석 항목의 locator 앵커를 누르면 좌측 원문 해당 문단으로 스크롤 + 강조.
  function jumpTo(locator) {
    var el = document.getElementById(locator);
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
