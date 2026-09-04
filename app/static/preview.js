// 진행 상태 폴링, 그리고 본 읽기로 승격.
(function () {
  "use strict";

  // 추출·프리뷰는 서버 백그라운드에서 돈다. 상태가 바뀌면 다시 그린다.
  var status = document.body.getAttribute("data-status");
  if (status === "extracting" || status === "previewing") {
    var timer = setInterval(function () {
      fetch("/preview/" + window.PREVIEW_ID + "/status")
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

  var promote = document.getElementById("promote");
  if (!promote) return;

  promote.addEventListener("click", function () {
    promote.disabled = true;
    promote.textContent = "읽는 중…";
    fetch("/preview/" + window.PREVIEW_ID + "/read", { method: "POST" })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        // 판단이 끝난 탭을 남겨둘 이유가 없다. 같은 탭에서 넘어간다.
        location.href = "/view/" + data.id;
      })
      .catch(function (e) {
        promote.disabled = false;
        promote.textContent = "제대로 읽기";
        var hint = document.querySelector(".decide .hint");
        if (hint) hint.textContent = "실패: " + (e && e.message ? e.message : e);
      });
  });
})();
