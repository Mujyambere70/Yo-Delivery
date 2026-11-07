/* Yo places (version 5): type a town or village anywhere in the world and choose it from the list.
   Usage: <input name="to_city" data-place="to_place"> + <input type="hidden" name="to_place">
   Optional: data-cc="#countrySelect" (search only in that country), data-kinds="city,town" and
   data-place-note="to_city" on an element that shows the full name of the chosen place.
   After a choice the input sends a "place" event with the place (id, name, label, cc, region, pin, lat, lng). */
(function () {
  "use strict";
  var URL = "/places/search";

  function setup(input) {
    if (input.getAttribute("data-place-ready")) return;
    input.setAttribute("data-place-ready", "1");
    var form = input.form, hidden = form ? form.elements[input.getAttribute("data-place")] : null;
    var wrap = document.createElement("div");
    wrap.className = "pl-wrap";
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    var list = document.createElement("ul");
    list.className = "pl-list";
    list.id = (input.id || input.name) + "_list";
    list.setAttribute("role", "listbox");
    list.hidden = true;
    wrap.appendChild(list);
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-controls", list.id);
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("autocomplete", "off");
    var items = [], active = -1, timer = null;

    function note(p) {
      var n = document.querySelector('[data-place-note="' + input.name + '"]');
      if (n) n.textContent = p ? p.label + (p.approx ? " ≈" : "") : "";
    }
    function close() {
      list.hidden = true; input.setAttribute("aria-expanded", "false"); active = -1;
      input.removeAttribute("aria-activedescendant");
    }
    function choose(p) {
      input.value = p.name;
      if (hidden) hidden.value = p.id;
      note(p);
      close();
      input.dispatchEvent(new CustomEvent("place", { bubbles: true, detail: p }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    }
    function render() {
      list.innerHTML = "";
      items.forEach(function (p, i) {
        var li = document.createElement("li");
        li.setAttribute("role", "option");
        li.id = list.id + "_" + i;
        if (i === active) { li.className = "on"; li.setAttribute("aria-selected", "true"); }
        var b = document.createElement("b"); b.textContent = p.name;
        var s = document.createElement("small");
        s.textContent = p.label.slice(p.name.length + 2) + (p.kind_name ? " · " + p.kind_name : "") + (p.pin ? " · " + p.pin : "");
        li.appendChild(b); li.appendChild(s);
        li.addEventListener("mousedown", function (e) { e.preventDefault(); choose(p); });
        list.appendChild(li);
      });
      list.hidden = !items.length;
      input.setAttribute("aria-expanded", items.length ? "true" : "false");
      if (active >= 0) input.setAttribute("aria-activedescendant", list.id + "_" + active);
    }
    input.addEventListener("input", function () {
      if (hidden) hidden.value = "";
      note(null);
      var q = input.value.trim();
      clearTimeout(timer);
      if (q.length < 2) { items = []; render(); return; }
      timer = setTimeout(function () {
        var ccSel = input.getAttribute("data-cc"), cc = "";
        if (ccSel && document.querySelector(ccSel)) cc = document.querySelector(ccSel).value;
        var url = URL + "?q=" + encodeURIComponent(q) + (cc ? "&cc=" + encodeURIComponent(cc) : "") +
                  (input.getAttribute("data-kinds") ? "&kinds=" + input.getAttribute("data-kinds") : "");
        fetch(url, { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (d) {
          if (input.value.trim() !== q) return;
          items = d.places || []; active = -1; render();
        }).catch(function () {});
      }, 180);
    });
    input.addEventListener("keydown", function (e) {
      if (list.hidden) return;
      if (e.key === "ArrowDown") { active = Math.min(items.length - 1, active + 1); render(); e.preventDefault(); }
      else if (e.key === "ArrowUp") { active = Math.max(0, active - 1); render(); e.preventDefault(); }
      else if (e.key === "Enter" && active >= 0) { choose(items[active]); e.preventDefault(); }
      else if (e.key === "Escape") { close(); }
    });
    input.addEventListener("blur", function () { setTimeout(close, 150); });
  }

  function all(root) { (root || document).querySelectorAll("input[data-place]").forEach(setup); }
  all();
  window.YoPlaces = { setup: setup, all: all };

  /* ---------- Leaflet (OpenStreetMap) loaded only when a page needs a real map ---------- */
  // Leaflet 1.9.4 (BSD licence) is hosted with the shop (static/vendor/leaflet); only the map tiles come from OSM
  var LEAFLET = (document.querySelector('script[src*="places.js"]') || { src: "/static/places.js" }).src
                  .replace(/places\.js.*$/, "vendor/leaflet/"), waiting = null;
  function loadLeaflet(ok, fail) {
    if (window.L && window.L.map) { ok(window.L); return; }
    if (waiting) { waiting.push([ok, fail]); return; }
    waiting = [[ok, fail]];
    var done = function (good) {
      var list = waiting; waiting = null;
      list.forEach(function (cb) { try { (good ? cb[0] : (cb[1] || function () {}))(window.L); } catch (e) {} });
    };
    var css = document.createElement("link");
    css.rel = "stylesheet"; css.href = LEAFLET + "leaflet.css";
    document.head.appendChild(css);
    var js = document.createElement("script");
    js.src = LEAFLET + "leaflet.js";
    var timer = setTimeout(function () { done(false); }, 6000);       // no internet: the drawn map is used
    js.onload = function () { clearTimeout(timer); done(!!(window.L && window.L.map)); };
    js.onerror = function () { clearTimeout(timer); done(false); };
    document.head.appendChild(js);
  }
  function tiles(L, map, onFail) {
    // the street pictures (tiles) come from OpenStreetMap; when none arrives in 6 seconds (no internet, blocked),
    // onFail() lets the page use the drawn map instead
    var layer = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19, subdomains: "abc",
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>'
    }).addTo(map);
    var loaded = 0;
    layer.on("tileload", function () { loaded++; });
    if (onFail) setTimeout(function () { if (!loaded) onFail(); }, 6000);
    return layer;
  }
  window.YoLeaflet = { load: loadLeaflet, tiles: tiles };

  /* ---------- "Put the pin on your door": a small map under a place field ----------
     <div class="pin-map" data-pin-for="from_city" data-lat="from_lat" data-lng="from_lng" hidden></div> */
  document.querySelectorAll("[data-pin-for]").forEach(function (box) {
    var input = document.querySelector('input[name="' + box.getAttribute("data-pin-for") + '"]');
    var form = input && input.form;
    if (!form) return;
    var latF = form.elements[box.getAttribute("data-lat")], lngF = form.elements[box.getAttribute("data-lng")];
    var help = box.parentNode.querySelector("[data-pin-help]");
    var map = null, marker = null;
    function put(lat, lng, zoom, after) {
      loadLeaflet(function (L) {
        box.hidden = false;
        if (help) help.hidden = false;
        if (!map) {
          map = L.map(box, { scrollWheelZoom: false });
          tiles(L, map, function () {                // no street pictures: the pin is not useful, hide the map
            box.hidden = true; if (help) help.hidden = true;
          });
          map.on("click", function (e) { setPin(e.latlng.lat, e.latlng.lng); });
        }
        map.setView([lat, lng], zoom || 14);
        setTimeout(function () { map.invalidateSize(); }, 50);
        if (after) after();
      });
    }
    function setPin(lat, lng) {
      var L = window.L;
      if (!marker) {
        marker = L.marker([lat, lng], { draggable: true }).addTo(map);
        marker.on("dragend", function () { var p = marker.getLatLng(); save(p.lat, p.lng); });
      } else marker.setLatLng([lat, lng]);
      save(lat, lng);
    }
    function save(lat, lng) {
      if (latF) latF.value = lat.toFixed(5);
      if (lngF) lngF.value = lng.toFixed(5);
      form.dispatchEvent(new Event("change", { bubbles: true }));
    }
    input.addEventListener("place", function (e) {
      if (latF) latF.value = ""; if (lngF) lngF.value = "";
      if (marker) { marker.remove(); marker = null; }
      put(e.detail.lat, e.detail.lng, e.detail.kind === "city" || e.detail.kind === "hub" ? 13 : 14);
    });
    box.addEventListener("yo:pin", function (e) {          // "use my location": the pin goes on that point
      put(e.detail.lat, e.detail.lng, 16, function () { setPin(e.detail.lat, e.detail.lng); });
    });
  });
})();
