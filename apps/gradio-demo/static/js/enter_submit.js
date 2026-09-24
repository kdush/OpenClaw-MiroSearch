// Gradio renders the question input as a <textarea> (max_lines=3), where Enter
// inserts a newline instead of firing the component's submit event. Intercept
// Enter here so it behaves like clicking "开始研究"; Shift+Enter keeps newline.
(function () {
  function onKeydown(ev) {
    if (ev.key !== "Enter" || ev.shiftKey || ev.ctrlKey || ev.altKey || ev.metaKey) return;
    if (ev.isComposing) return;
    var target = ev.target;
    if (!target || !target.closest) return;
    if (!target.closest("#question-input")) return;
    ev.preventDefault();
    var runBtn = document.getElementById("run-btn");
    if (runBtn && !runBtn.disabled) runBtn.click();
  }
  document.addEventListener("keydown", onKeydown, true);
})();
