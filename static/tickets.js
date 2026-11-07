/* Yo Tickets - small helpers for the ticket pages (everything also works without JavaScript):
   swap "from" and "to", choose seats (up to the number of passengers, live total), the hold countdown,
   card fields, the QR code of the e-ticket and the camera scanner of the conductor app. */
(function () {
  "use strict";

  // ---- swap from / to
  document.querySelectorAll("[data-swap]").forEach(function (b) {
    b.addEventListener("click", function () {
      var f = b.form.elements["from"], t = b.form.elements["to"], x = f.value;
      f.value = t.value; t.value = x;
      var fp = b.form.elements["from_place"], tp = b.form.elements["to_place"];
      if (fp && tp) { x = fp.value; fp.value = tp.value; tp.value = x; }
    });
  });

  // ---- seat map
  var form = document.getElementById("seatForm");
  if (form) {
    var max = parseInt(form.getAttribute("data-max"), 10) || 1;
    var cur = form.getAttribute("data-currency") || "INR";
    var boxes = form.querySelectorAll('input[name="seat"]');
    var list = document.getElementById("seatChosen"), count = document.getElementById("seatCount");
    var total = document.getElementById("seatTotal"), go = document.getElementById("seatGo");
    var fmt;
    try {
      fmt = new Intl.NumberFormat(document.documentElement.lang || "en", { style: "currency", currency: cur, maximumFractionDigits: cur === "INR" ? 0 : 2 });
    } catch (e) { fmt = { format: function (v) { return v.toFixed(2) + " " + cur; } }; }
    function update() {
      var chosen = [];
      boxes.forEach(function (b) { if (b.checked) chosen.push(b); });
      boxes.forEach(function (b) {
        if (!b.hasAttribute("data-taken")) b.disabled = !b.checked && chosen.length >= max;
      });
      list.innerHTML = "";
      var sum = 0;
      chosen.forEach(function (b) {
        var s = document.createElement("span");
        s.textContent = b.getAttribute("data-label");
        list.appendChild(s);
        sum += parseInt(b.getAttribute("data-local"), 10) || 0;
      });
      count.textContent = chosen.length + " / " + max;
      total.textContent = chosen.length ? fmt.format(sum / 100) : "—";
      if (go) go.disabled = chosen.length === 0;
    }
    boxes.forEach(function (b) { b.addEventListener("change", update); });
    update();
  }

  // ---- hold countdown on the payment page
  var timer = document.querySelector("[data-left]");
  if (timer) {
    var left = parseInt(timer.getAttribute("data-left"), 10), out = timer.querySelector("b");
    var tick = function () {
      if (left <= 0) { out.textContent = "0:00"; setTimeout(function () { location.reload(); }, 1500); return; }
      out.textContent = Math.floor(left / 60) + ":" + ("0" + (left % 60)).slice(-2);
      left -= 1;
      setTimeout(tick, 1000);
    };
    tick();
  }

  // ---- card fields only when "card" is chosen
  var pay = document.querySelectorAll('input[name="payment"]'), cardBox = document.getElementById("cardBox");
  if (pay.length && cardBox) {
    var show = function () {
      var card = document.querySelector('input[name="payment"]:checked');
      cardBox.hidden = !(card && card.value === "card");
      cardBox.querySelectorAll("input,select").forEach(function (i) { i.disabled = cardBox.hidden; });
    };
    pay.forEach(function (p) { p.addEventListener("change", show); });
    show();
  }

  // ---- QR code of the e-ticket (qrcode-generator from cdnjs; without it the link is shown as text)
  var qr = document.getElementById("ticketQr");
  if (qr) {
    if (window.qrcode) {
      var q = window.qrcode(0, "M");
      q.addData(qr.getAttribute("data-qr"));
      q.make();
      qr.innerHTML = q.createSvgTag({ cellSize: 4, margin: 0 });
    } else {
      qr.textContent = qr.getAttribute("data-qr");
    }
  }

  // ---- conductor: scan with the camera (BarcodeDetector, or jsQR in the other browsers) or type the PNR
  var scan = document.getElementById("scanForm");
  if (scan) {
    var input = scan.elements["token"], result = document.getElementById("scanResult");
    var camBtn = document.getElementById("camBtn"), video = document.getElementById("camVideo");
    var busy = false;
    var send = function (token) {
      if (busy) return;
      busy = true;
      var data = new FormData(scan);
      data.set("token", token);
      data.set("json", "1");
      fetch(scan.action, { method: "POST", body: data, credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          result.hidden = false;
          result.className = "tk-result " + (d.ok ? (d.already ? "info" : "ok") : "bad");
          result.textContent = d.message;
          if (d.ok && !d.already && scan.getAttribute("data-reload")) setTimeout(function () { location.reload(); }, 1600);
        })
        .catch(function () { scan.submit(); })
        .then(function () { setTimeout(function () { busy = false; }, 2500); });
    };
    scan.addEventListener("submit", function (e) {
      if (!window.fetch) return;
      e.preventDefault();
      if (input.value.trim()) send(input.value.trim());
    });
    // version 5: browsers without BarcodeDetector (Firefox, Safari, desktop) read the QR code with jsQR
    // (a small JavaScript library hosted with the shop, Apache 2.0 licence) from the camera picture.
    var stopCam = null;
    var showNoCam = function () {
      result.hidden = false;
      result.className = "tk-result bad";
      result.textContent = document.getElementById("noCam").textContent;
    };
    var loadJsQR = function (ok, fail) {
      if (window.jsQR) { ok(); return; }
      var s = document.createElement("script");
      s.src = scan.getAttribute("data-jsqr");
      s.onload = function () { window.jsQR ? ok() : fail(); };
      s.onerror = fail;
      document.head.appendChild(s);
    };
    if (camBtn && navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
      camBtn.hidden = false;
      camBtn.addEventListener("click", function () {
        if (stopCam) { stopCam(); return; }
        var native = "BarcodeDetector" in window;
        var start = function () {
          navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } }).then(function (stream) {
            var on = true;
            stopCam = function () { on = false; stream.getTracks().forEach(function (t) { t.stop(); }); video.hidden = true; stopCam = null; };
            video.hidden = false;
            video.setAttribute("playsinline", "");
            video.srcObject = stream;
            video.play();
            var canvas = document.createElement("canvas"), ctx = canvas.getContext("2d", { willReadFrequently: true });
            var detector = native ? new window.BarcodeDetector({ formats: ["qr_code"] }) : null;
            var found = function (text) { input.value = text; send(text); };
            var look = function () {
              if (!on) return;
              if (detector) {
                detector.detect(video).then(function (codes) {
                  if (codes.length) found(codes[0].rawValue);
                  setTimeout(look, 600);
                }).catch(function () { setTimeout(look, 1200); });
                return;
              }
              if (video.readyState >= 2 && video.videoWidth) {
                var w = Math.min(640, video.videoWidth), h = Math.round(video.videoHeight * w / video.videoWidth);
                canvas.width = w; canvas.height = h;
                ctx.drawImage(video, 0, 0, w, h);
                var code = window.jsQR(ctx.getImageData(0, 0, w, h).data, w, h, { inversionAttempts: "dontInvert" });
                if (code && code.data) found(code.data);
              }
              setTimeout(look, 400);
            };
            look();
          }).catch(showNoCam);
        };
        if (native) start(); else loadJsQR(start, showNoCam);
      });
    }
  }
})();
