(function () {
  var TITLES = {
    "export-md-btn": "Markdown",
    "export-pdf-btn": "PDF",
    "export-docx-btn": "Word"
  };
  var lastAutoDownload = "";
  function apply() {
    Object.keys(TITLES).forEach(function (id) {
      var el = document.getElementById(id);
      if (el && !el.title) el.title = TITLES[id];
    });
  }
  function autoDownload() {
    var box = document.getElementById("export-file");
    if (!box) return;
    var link = box.querySelector("a[href*='/file=']");
    if (!link) return;
    var href = link.getAttribute("href");
    if (!href || href === lastAutoDownload) return;
    lastAutoDownload = href;
    var a = document.createElement("a");
    a.href = href;
    a.download = link.getAttribute("download") || "";
    document.body.appendChild(a);
    a.click();
    a.remove();
  }
  var observer = new MutationObserver(function () { apply(); autoDownload(); });
  function start() {
    observer.observe(document.body, { childList: true, subtree: true });
    apply();
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
