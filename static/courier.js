/* Yo Courier pages: live price, India/abroad fields, "use my location", copy link, and the courier app's GPS. */
(function () {
  "use strict";

  // ----- live price on the quote and booking forms -----
  var form = document.getElementById("quoteForm"), out = document.getElementById("quoteOut");
  if (form && out) {
    var timer = null;
    var ask = function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        var data = new FormData(form);
        if (form.getAttribute("data-token")) data.set("csrf_token", form.getAttribute("data-token"));
        fetch(form.getAttribute("data-url"), { method: "POST", body: data, credentials: "same-origin" })
          .then(function (r) { return r.json(); })
          .then(function (q) {
            out.innerHTML = "";
            if (!q.ok) { var e = document.createElement("span"); e.className = "err small"; e.textContent = q.error; out.appendChild(e); return; }
            var p = document.createElement("div"); p.className = "quote-price"; p.textContent = q.price; out.appendChild(p);
            var meta = document.createElement("div"); meta.className = "small muted";
            meta.textContent = q.zone + " · " + q.weight + (q.distance ? " · " + q.distance + " km" : "") + " · " + q.transit;
            out.appendChild(meta);
            var ul = document.createElement("ul"); ul.className = "quote-lines";
            q.lines.forEach(function (l) { var li = document.createElement("li"); var a = document.createElement("span"); a.textContent = l[0];
              var b = document.createElement("span"); b.textContent = l[1]; li.appendChild(a); li.appendChild(b); ul.appendChild(li); });
            if (q.tax && q.tax.replace(/[^1-9]/g, "")) { var li = document.createElement("li"); li.innerHTML = "<span translate=\"no\"></span><span></span>"; li.firstChild.textContent = q.tax_label || "GST 18%"; li.lastChild.textContent = q.tax; ul.appendChild(li); }
            out.appendChild(ul);
          })
          .catch(function () {});
      }, 250);
    };
    form.addEventListener("input", ask);
    form.addEventListener("change", ask);
    ask();
  }

  // ----- India: choose a city from the list; abroad: type the city and the post code -----
  document.querySelectorAll("[data-country]").forEach(function (sel) {
    var scope = sel.form || document;
    var apply = function () {
      var abroad = sel.value !== "IN";
      scope.querySelectorAll("[data-india]").forEach(function (b) {
        b.hidden = abroad; b.querySelectorAll("select,input").forEach(function (i) { i.disabled = abroad; }); });
      scope.querySelectorAll("[data-abroad]").forEach(function (b) {
        b.hidden = !abroad; b.querySelectorAll("select,input").forEach(function (i) { i.disabled = !abroad; }); });
    };
    sel.addEventListener("change", apply);
    apply();
  });

  // ----- use my location (pickup point) -----
  var locBtn = document.getElementById("useLocation");
  if (locBtn && form) {
    locBtn.addEventListener("click", function () {
      var note = document.getElementById("locNote");
      if (!navigator.geolocation) { note.textContent = "Your browser cannot share the location."; return; }
      navigator.geolocation.getCurrentPosition(function (p) {
        var lat = p.coords.latitude, lng = p.coords.longitude;
        note.textContent = "✓ " + lat.toFixed(4) + ", " + lng.toFixed(4);
        // version 5: the town or village of this point is chosen for you, and the pin goes on the map
        fetch("/places/near?lat=" + lat + "&lng=" + lng, { credentials: "same-origin" })
          .then(function (r) { return r.json(); })
          .then(function (d) {
            var town = form.elements.from_city, cc = form.elements.from_country;
            if (d.place && town) {
              if (cc) cc.value = d.place.cc;
              town.value = d.place.name;
              if (form.elements.from_place) form.elements.from_place.value = d.place.id;
              var n = document.querySelector('[data-place-note="from_city"]');
              if (n) n.textContent = d.place.label;
            }
            var box = document.querySelector('[data-pin-for="from_city"]');
            if (box) box.dispatchEvent(new CustomEvent("yo:pin", { detail: { lat: lat, lng: lng } }));
          })
          .catch(function () {})
          .then(function () {
            form.elements.from_lat.value = lat.toFixed(5);
            form.elements.from_lng.value = lng.toFixed(5);
            form.dispatchEvent(new Event("change", { bubbles: true }));
          });
      }, function () { note.textContent = "The location was not shared."; }, { enableHighAccuracy: true, timeout: 10000 });
    });
  }

  // ----- copy a link -----
  document.querySelectorAll("[data-copy]").forEach(function (b) {
    b.addEventListener("click", function () {
      var text = b.getAttribute("data-copy"), old = b.textContent;
      (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject()).then(function () {
        b.textContent = "✓"; setTimeout(function () { b.textContent = old; }, 1500);
      }).catch(function () { window.prompt("", text); });
    });
  });

  // ----- courier app: send the position while online -----
  var drv = document.querySelector("[data-ping]");
  if (drv) {
    var url = drv.getAttribute("data-ping"), token = drv.getAttribute("data-token"), last = null, status = document.getElementById("gpsNote");
    var send = function (pos) {
      var body = new URLSearchParams({ csrf_token: token });
      if (pos) { body.set("lat", pos.coords.latitude.toFixed(5)); body.set("lng", pos.coords.longitude.toFixed(5)); }
      if (!navigator.onLine) {                         // version 6: no network - keep the last position for later
        if (pos) { try { localStorage.setItem("yoLastPos", JSON.stringify({ lat: pos.coords.latitude, lng: pos.coords.longitude, t: Date.now() })); } catch (e) {} }
        return;
      }
      fetch(url, { method: "POST", body: body, credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (d) {
        if (d.ok && d.jobs > Number(drv.getAttribute("data-jobs") || 0)) location.reload();     // a new job came
      }).catch(function () {
        if (pos) { try { localStorage.setItem("yoLastPos", JSON.stringify({ lat: pos.coords.latitude, lng: pos.coords.longitude, t: Date.now() })); } catch (e) {} }
      });
    };
    // version 6: offline mode - the courier app pages stay readable without network, and the last position
    // is sent when the network comes back
    var banner = document.getElementById("offlineNote");
    var showNet = function () { if (banner) banner.hidden = navigator.onLine; };
    window.addEventListener("offline", showNet);
    window.addEventListener("online", function () {
      showNet();
      var saved = null;
      try { saved = JSON.parse(localStorage.getItem("yoLastPos") || "null"); } catch (e) {}
      if (saved && Date.now() - saved.t < 30 * 60 * 1000) {
        send({ coords: { latitude: saved.lat, longitude: saved.lng } });
        try { localStorage.removeItem("yoLastPos"); } catch (e) {}
      }
    });
    showNet();
    if ("serviceWorker" in navigator) { navigator.serviceWorker.register("/sw.js").catch(function () {}); }
    if (navigator.geolocation) {
      navigator.geolocation.watchPosition(function (p) {
        if (status) status.textContent = "GPS ✓ " + p.coords.latitude.toFixed(4) + ", " + p.coords.longitude.toFixed(4);
        if (!last || Date.now() - last > 10000) { last = Date.now(); send(p); }
      }, function () { if (status) status.textContent = "GPS is off - the map shows your city only."; },
      { enableHighAccuracy: true, maximumAge: 5000 });
    }
    setInterval(function () { if (!last || Date.now() - last > 25000) { last = Date.now(); send(null); } }, 15000);
  }
})();
