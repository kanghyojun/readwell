// 목록에서 번역 걸기. 번역은 문단마다 호출이라 오래 걸리므로, 시작만 알리고
// 진행 상황은 폴링해서 버튼 자리에 보여준다.
(function () {
  "use strict";

  function poll(sid, button) {
    var timer = setInterval(function () {
      fetch("/view/" + sid + "/translations")
        .then(function (r) { return r.json(); })
        .then(function (body) {
          var count = Object.keys(body.paragraphs || {}).length;
          if (body.done) {
            clearInterval(timer);
            button.textContent = body.note ? "일부 실패" : "번역 완료 " + count;
            button.title = body.note || "";
            return;
          }
          button.textContent = "번역 중 " + count;
        })
        .catch(function () { /* 일시적 실패는 다음 주기에 다시 */ });
    }, 2000);
  }

  document.addEventListener("click", function (e) {
    var button = e.target.closest("button[data-translate]");
    if (!button) return;
    var sid = button.getAttribute("data-translate");

    button.disabled = true;
    button.textContent = "시작하는 중…";

    fetch("/view/" + sid + "/translate", { method: "POST" })
      .then(function (r) { return r.json(); })
      .then(function (body) {
        if (body.status === "skipped") {
          button.textContent = "번역 대상 아님";
          button.title = body.note || "";
          return;
        }
        button.textContent = "번역 중…";
        poll(sid, button);
      })
      .catch(function () {
        button.textContent = "실패";
        button.disabled = false;
      });
  });
})();
