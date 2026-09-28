// Report topology is emitted as a ```mermaid fenced block wrapped in
// .mermaid-card. Without a renderer it shows up as a raw code block, so the
// relationship map is effectively unreadable. This script renders those blocks
// into SVG client-side.
//
// Two hard rules from the roadmap (Q1):
//   1. Rendering is display only — it never asserts that a drawn relation is
//      verified; it just draws what the report text already said.
//   2. If mermaid is unavailable or a diagram fails to parse, keep the original
//      code block as the plain-text fallback. Never blank the card.
// mermaid runs with securityLevel "strict" so labels are sanitized.
(function () {
  var MERMAID_URL = "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs";
  var loaded = null;

  function loadMermaid() {
    if (loaded) return loaded;
    loaded = import(MERMAID_URL)
      .then(function (mod) {
        var mermaid = mod.default || mod;
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          theme: "dark",
          flowchart: { htmlLabels: false },
        });
        return mermaid;
      })
      .catch(function () {
        // Offline / CDN blocked: fall back to plain text for the whole page.
        return null;
      });
    return loaded;
  }

  function pendingBlocks() {
    return Array.prototype.slice.call(
      document.querySelectorAll(".mermaid-card code.language-mermaid")
    ).filter(function (code) {
      return code.dataset.mermaidState === undefined;
    });
  }

  function renderBlock(mermaid, code, index) {
    var source = (code.textContent || "").trim();
    var host = code.closest("pre") || code;
    if (!source) {
      code.dataset.mermaidState = "empty";
      return;
    }
    code.dataset.mermaidState = "rendering";
    var id = "diting-mermaid-" + index + "-" + Date.now();
    mermaid
      .render(id, source)
      .then(function (out) {
        var holder = document.createElement("div");
        holder.className = "mermaid-rendered";
        holder.innerHTML = out && out.svg ? out.svg : "";
        host.replaceWith(holder);
      })
      .catch(function () {
        // Unparsable diagram: leave the code block untouched.
        code.dataset.mermaidState = "failed";
      });
  }

  function run() {
    var blocks = pendingBlocks();
    if (!blocks.length) return;
    loadMermaid().then(function (mermaid) {
      if (!mermaid) {
        blocks.forEach(function (code) {
          code.dataset.mermaidState = "unavailable";
        });
        return;
      }
      blocks.forEach(function (code, index) {
        renderBlock(mermaid, code, index);
      });
    });
  }

  var scheduled = false;
  function schedule() {
    if (scheduled) return;
    scheduled = true;
    window.requestAnimationFrame(function () {
      scheduled = false;
      run();
    });
  }

  function start() {
    run();
    new MutationObserver(schedule).observe(document.body, {
      childList: true,
      subtree: true,
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
})();
