(function () {
  var VER = 10;
  if (window.__miroStarfieldVer === VER) return;
  window.__miroStarfieldVer = VER;

  var old = document.getElementById("bg-particles");
  if (old) old.remove();

  // Slower drift than v7 — background should feel calm, not busy.
  var LAYER = [
    { speed: 0.08, size: 0.32, alpha: 0.32, countDiv: 3600, maxN: 240, minN: 100 },
    { speed: 0.22, size: 0.72, alpha: 0.58, countDiv: 5600, maxN: 160, minN: 45 },
    { speed: 0.48, size: 1.35, alpha: 0.88, countDiv: 8600, maxN: 95, minN: 40 }
  ];

  var canvas = document.createElement("canvas");
  canvas.id = "bg-particles";
  canvas.setAttribute("aria-hidden", "true");
  canvas.style.cssText =
    "position:fixed;inset:0;width:100%;height:100%;pointer-events:none;z-index:0;";
  var ctx = canvas.getContext("2d");
  var layers = [[], [], []];
  var meteors = [];
  var w = 0, h = 0, rafId = 0, t = 0, nextMeteorAt = 0;
  var camVX = 0.06, camVY = 0.035, windPhase = 0;
  var reduceMq = window.matchMedia("(prefers-reduced-motion: reduce)");

  function isMobile() {
    return window.innerWidth < 768;
  }

  /** 0 = static backdrop only; ~0.42 on phone; 1 on desktop. */
  function particleScale() {
    if (reduceMq.matches) return 0;
    return isMobile() ? 0.42 : 1;
  }

  function newStar(depth) {
    var spec = LAYER[depth];
    var giant = depth === 2 && Math.random() < 0.1;
    var twinkly = Math.random() < (depth === 0 ? 0.16 : depth === 1 ? 0.32 : 0.5);
    var floatR = (0.06 + Math.random() * 0.2) * (0.3 + depth * 0.4);
    return {
      x: Math.random() * w,
      y: Math.random() * h,
      vx: (Math.random() - 0.5) * 0.05 * spec.speed,
      vy: (Math.random() - 0.5) * 0.05 * spec.speed,
      r: (giant ? 1.6 + Math.random() * 1.5 : 0.35 + Math.random() * 1.1) * spec.size,
      a: (0.28 + Math.random() * 0.52) * spec.alpha,
      tw: Math.random() * Math.PI * 2,
      tw2: Math.random() * Math.PI * 2,
      twSpeed: twinkly ? (0.008 + Math.random() * 0.012) : (0.003 + Math.random() * 0.005),
      twSpeed2: 0.018 + Math.random() * 0.025,
      twAmp: twinkly ? (0.28 + Math.random() * 0.22) : (0.08 + Math.random() * 0.12),
      flash: twinkly && Math.random() < 0.12,
      floatPhase: Math.random() * Math.PI * 2,
      floatSpeed: 0.0025 + Math.random() * 0.005,
      floatRx: floatR * (0.55 + Math.random() * 0.7),
      floatRy: floatR * (0.45 + Math.random() * 0.7),
      glow: giant || (depth === 2 && Math.random() < 0.45) || (depth === 1 && Math.random() < 0.18),
      spike: giant || (depth === 2 && Math.random() < 0.1),
      hue: Math.random() < 0.12 ? 210
        : (Math.random() < 0.08 ? 255
          : (Math.random() < 0.1 ? 40 : 200))
    };
  }

  function spawnMeteor() {
    var heavy = Math.random() < 0.28;
    var fromLeft = Math.random() < 0.58;
    var angle = 0.26 + Math.random() * 0.5;
    // Very slow glide + long trail.
    var speed = heavy ? (1.05 + Math.random() * 0.85) : (1.25 + Math.random() * 1.0);
    var ux = fromLeft ? Math.cos(angle) : -Math.cos(angle);
    var uy = Math.sin(angle) * (0.72 + Math.random() * 0.35);
    return {
      x: fromLeft ? (-90 - Math.random() * 120) : (w + 90 + Math.random() * 120),
      y: Math.random() * h * 0.5,
      vx: ux * speed,
      vy: uy * speed,
      speed: speed,
      heavy: heavy,
      len: heavy ? (160 + Math.random() * 100) : (110 + Math.random() * 80),
      life: 0,
      maxLife: heavy ? (150 + Math.random() * 70) : (120 + Math.random() * 60),
      width: heavy ? (2.0 + Math.random() * 1.2) : (0.9 + Math.random() * 0.7),
      hue: heavy ? (24 + Math.random() * 24) : (188 + Math.random() * 35)
    };
  }

  function rebuild() {
    var scale = particleScale();
    layers = [[], [], []];
    meteors = [];
    for (var d = 0; d < 3; d++) {
      var spec = LAYER[d];
      var n;
      if (scale === 0) {
        // Sparse static field — painted once, no RAF.
        n = d === 0 ? 48 : d === 1 ? 28 : 14;
      } else {
        n = Math.round(
          Math.min(
            spec.maxN,
            Math.max(spec.minN, Math.round((w * h) / spec.countDiv))
          ) * scale
        );
        if (n < 1) n = 1;
      }
      for (var i = 0; i < n; i++) layers[d].push(newStar(d));
    }
    nextMeteorAt = t + 180 + Math.random() * 200;
  }

  function resize() {
    var dpr = Math.min(window.devicePixelRatio || 1, isMobile() ? 1.5 : 2);
    w = window.innerWidth;
    h = window.innerHeight;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    rebuild();
    if (particleScale() === 0) {
      stop();
      paintFrame(false);
    } else if (!rafId && !document.hidden) {
      loop();
    }
  }

  function wrap(v, max) {
    if (v < -24) return v + max + 48;
    if (v > max + 24) return v - max - 48;
    return v;
  }

  function hsla(h, s, l, a) {
    return "hsla(" + h + ", " + s + "%, " + l + "%, " + a.toFixed(3) + ")";
  }

  function twinkleFactor(s) {
    var base = 0.5 + 0.5 * Math.sin(s.tw);
    if (s.flash) {
      var pulse = Math.pow(0.5 + 0.5 * Math.sin(s.tw2), 5);
      base = Math.max(base, pulse * 0.85 + base * 0.15);
    }
    return Math.max(0.12, 1 - s.twAmp + s.twAmp * base);
  }

  function drawSoftStar(s, x, y, alpha) {
    var glowR = s.r * (s.spike ? 7.5 : 5.5);
    if (s.glow && alpha > 0.14) {
      var g = ctx.createRadialGradient(x, y, 0, x, y, glowR);
      g.addColorStop(0, hsla(s.hue, 70, 88, alpha * 0.42));
      g.addColorStop(0.32, hsla(s.hue, 72, 70, alpha * 0.14));
      g.addColorStop(1, hsla(s.hue, 80, 55, 0));
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(x, y, glowR, 0, 6.2832);
      ctx.fill();
    }

    if (s.spike && alpha > 0.55) {
      var len = s.r * (3.2 + 3.5 * (alpha - 0.55));
      var spikeA = (alpha - 0.55) * 0.35;
      ctx.save();
      ctx.strokeStyle = hsla(s.hue, 75, 96, spikeA);
      ctx.lineWidth = Math.max(0.5, s.r * 0.28);
      ctx.lineCap = "round";
      ctx.beginPath();
      ctx.moveTo(x - len, y);
      ctx.lineTo(x + len, y);
      ctx.moveTo(x, y - len * 0.8);
      ctx.lineTo(x, y + len * 0.8);
      ctx.stroke();
      ctx.restore();
    }

    ctx.beginPath();
    ctx.fillStyle = hsla(s.hue, 62, 92, Math.min(1, alpha));
    ctx.arc(x, y, Math.max(0.4, s.r), 0, 6.2832);
    ctx.fill();
  }

  function fadeLife(life, maxLife) {
    var p = life / maxLife;
    if (p < 0.1) return p / 0.1;
    if (p > 0.58) return (1 - p) / 0.42;
    return 1;
  }

  function drawMeteor(m) {
    var fade = Math.max(0, Math.min(1, fadeLife(m.life, m.maxLife)));
    var tx = m.x - (m.vx / m.speed) * m.len;
    var ty = m.y - (m.vy / m.speed) * m.len;
    var grad = ctx.createLinearGradient(tx, ty, m.x, m.y);
    if (m.heavy) {
      grad.addColorStop(0, "rgba(0,0,0,0)");
      grad.addColorStop(0.4, hsla(m.hue, 90, 65, 0.18 * fade));
      grad.addColorStop(0.8, hsla(m.hue, 95, 78, 0.52 * fade));
      grad.addColorStop(1, hsla(48, 100, 94, 0.94 * fade));
    } else {
      grad.addColorStop(0, "rgba(0,0,0,0)");
      grad.addColorStop(0.5, hsla(m.hue, 80, 78, 0.22 * fade));
      grad.addColorStop(1, hsla(m.hue, 90, 94, 0.86 * fade));
    }
    ctx.strokeStyle = grad;
    ctx.lineWidth = m.width;
    ctx.lineCap = "round";
    ctx.beginPath();
    ctx.moveTo(tx, ty);
    ctx.lineTo(m.x, m.y);
    ctx.stroke();

    ctx.beginPath();
    ctx.fillStyle = m.heavy
      ? hsla(48, 100, 96, 0.94 * fade)
      : hsla(m.hue, 90, 96, 0.8 * fade);
    ctx.arc(m.x, m.y, m.heavy ? 2.6 : 1.4, 0, 6.2832);
    ctx.fill();

    if (m.heavy) {
      var hg = ctx.createRadialGradient(m.x, m.y, 0, m.x, m.y, 11);
      hg.addColorStop(0, hsla(m.hue, 90, 70, 0.32 * fade));
      hg.addColorStop(1, hsla(m.hue, 90, 60, 0));
      ctx.fillStyle = hg;
      ctx.beginPath();
      ctx.arc(m.x, m.y, 11, 0, 6.2832);
      ctx.fill();
    }
  }

  function paintFrame(animate) {
    var i, d, s, m, spec, fx, fy, alpha;
    var scale = particleScale();
    var mobile = isMobile();

    if (animate) {
      t += 1;
      windPhase += 0.0014;
      camVX += (Math.sin(windPhase * 0.55) * 0.09 + Math.sin(windPhase * 0.9) * 0.025 - camVX) * 0.018;
      camVY += (Math.cos(windPhase * 0.4) * 0.055 + Math.cos(windPhase * 0.8) * 0.018 - camVY) * 0.018;
    }

    ctx.clearRect(0, 0, w, h);

    for (d = 0; d < 3; d++) {
      spec = LAYER[d];
      for (i = 0; i < layers[d].length; i++) {
        s = layers[d][i];
        if (animate) {
          s.floatPhase += s.floatSpeed;
          s.x = wrap(s.x + s.vx + camVX * spec.speed, w);
          s.y = wrap(s.y + s.vy + camVY * spec.speed, h);
          s.tw += s.twSpeed;
          s.tw2 += s.twSpeed2;
        }
        fx = Math.sin(s.floatPhase) * s.floatRx;
        fy = Math.cos(s.floatPhase * 0.83) * s.floatRy;
        alpha = animate ? s.a * twinkleFactor(s) : s.a * 0.85;
        drawSoftStar(s, s.x + fx, s.y + fy, alpha);
      }
    }

    if (!animate || scale === 0) return;

    var maxMeteors = mobile ? 1 : 1;
    var meteorGap = mobile ? (360 + Math.random() * 320) : (280 + Math.random() * 320);
    if (t >= nextMeteorAt && meteors.length < maxMeteors) {
      meteors.push(spawnMeteor());
      nextMeteorAt = t + meteorGap;
    }

    for (i = meteors.length - 1; i >= 0; i--) {
      m = meteors[i];
      m.x += m.vx;
      m.y += m.vy;
      m.life += 1;
      drawMeteor(m);
      if (
        m.life >= m.maxLife ||
        m.x < -160 || m.x > w + 160 ||
        m.y < -120 || m.y > h + 120
      ) {
        meteors.splice(i, 1);
      }
    }
  }

  function draw() {
    paintFrame(true);
  }

  function loop() {
    if (particleScale() === 0 || document.hidden) {
      rafId = 0;
      return;
    }
    draw();
    rafId = window.requestAnimationFrame(loop);
  }

  function stop() {
    if (rafId) {
      window.cancelAnimationFrame(rafId);
      rafId = 0;
    }
  }

  function start() {
    if (!document.body) {
      document.addEventListener("DOMContentLoaded", start, { once: true });
      return;
    }
    document.body.appendChild(canvas);
    resize();
    window.addEventListener("resize", resize);
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) stop();
      else if (particleScale() > 0 && !rafId) loop();
      else if (particleScale() === 0) paintFrame(false);
    });
    if (typeof reduceMq.addEventListener === "function") {
      reduceMq.addEventListener("change", resize);
    } else if (typeof reduceMq.addListener === "function") {
      reduceMq.addListener(resize);
    }
  }

  start();
})();
