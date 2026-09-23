(function () {
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function tick() {
    var now = Math.floor(Date.now() / 1000);
    var nodes = document.querySelectorAll(".runtime-elapsed-value");
    for (var i = 0; i < nodes.length; i++) {
      var match = /--start-ts:\s*(\d+)/.exec(nodes[i].getAttribute("style") || "");
      if (!match) continue;
      var seconds = Math.max(0, now - parseInt(match[1], 10));
      nodes[i].textContent = Math.floor(seconds / 60) + ":" + pad(seconds % 60);
    }
  }
  tick();
  setInterval(tick, 1000);
})();
