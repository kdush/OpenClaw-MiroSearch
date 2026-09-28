(function () {
  // Gradio re-creates the overlay subtree whenever `visible` flips, so node
  // identity is unstable: presence in the DOM *is* the open state, and every
  // handler has to be delegated from document.
  var MODALS = [
    { id: "settings-modal", open: "settings-open-btn", close: "settings-close-btn" }
  ];
  var FOCUSABLE = "a[href],button:not([disabled]),input:not([disabled])," +
    "select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex='-1'])";
  var openCards = {};
  var restoreFocusTo = null;

  function topOpenId() {
    var last = null;
    for (var i = 0; i < MODALS.length; i++) {
      if (openCards[MODALS[i].id]) last = MODALS[i].id;
    }
    return last;
  }

  function markOpen(modal, card) {
    openCards[modal.id] = card;
    var overlay = card.parentElement;
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    var title = card.querySelector(".modal-title");
    if (title) {
      title.id = modal.id + "-title";
      overlay.setAttribute("aria-labelledby", title.id);
    }
    document.documentElement.classList.add("miro-modal-open");
    // Focus the card, not a field: Gradio hydrates the modal's children in
    // passes, so "first focusable" is a race and lands on the footer buttons.
    card.setAttribute("tabindex", "-1");
    card.focus();
  }

  function markClosed(modal) {
    delete openCards[modal.id];
    if (topOpenId()) return;
    document.documentElement.classList.remove("miro-modal-open");
    if (restoreFocusTo && document.contains(restoreFocusTo)) restoreFocusTo.focus();
    restoreFocusTo = null;
  }

  function sync() {
    for (var i = 0; i < MODALS.length; i++) {
      var modal = MODALS[i];
      var overlay = document.getElementById(modal.id);
      var card = overlay && overlay.querySelector(".modal-card");
      if (card && !openCards[modal.id]) markOpen(modal, card);
      else if (!card && openCards[modal.id]) markClosed(modal);
    }
  }

  document.addEventListener("click", function (ev) {
    var trigger = ev.target && ev.target.closest ? ev.target.closest(".modal-open-trigger") : null;
    if (trigger) restoreFocusTo = trigger;
    var topId = topOpenId();
    if (topId) {
      var card = openCards[topId];
      if (!card.contains(ev.target)) {
        var btn = card.querySelector("[id$='-close-btn']");
        if (btn) btn.click();
      }
    }
    // Gradio swaps the DOM after its own handler runs; observe() usually beats
    // this, but a follow-up pass keeps the two paths consistent.
    setTimeout(sync, 0);
  }, true);

  document.addEventListener("keydown", function (ev) {
    var topId = topOpenId();
    if (!topId) return;
    var card = openCards[topId];
    if (ev.key === "Escape") {
      // An open dropdown list owns this Escape; closing the dialog here would
      // throw away the edits the user was about to confirm.
      if (card.querySelector("ul.options")) return;
      var btn = card.querySelector("[id$='-close-btn']");
      if (btn) btn.click();
      return;
    }
    if (ev.key !== "Tab") return;
    var items = [].slice.call(card.querySelectorAll(FOCUSABLE)).filter(function (el) {
      return el.getClientRects().length > 0;
    });
    if (!items.length) return;
    var first = items[0];
    var last = items[items.length - 1];
    var active = document.activeElement;
    if (!card.contains(active)) { ev.preventDefault(); first.focus(); return; }
    if (active === card) {
      ev.preventDefault();
      (ev.shiftKey ? last : first).focus();
      return;
    }
    if (ev.shiftKey && active === first) { ev.preventDefault(); last.focus(); }
    else if (!ev.shiftKey && active === last) { ev.preventDefault(); first.focus(); }
  }, true);

  var observer = new MutationObserver(function (records) {
    for (var i = 0; i < records.length; i++) {
      if (records[i].addedNodes.length || records[i].removedNodes.length) {
        sync();
        return;
      }
    }
  });

  function start() {
    observer.observe(document.body, { childList: true, subtree: true });
    sync();
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
