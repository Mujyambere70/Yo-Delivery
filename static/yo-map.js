/* Yo live map (couriers and Yo Tickets).
   Version 5: a real street map (OpenStreetMap tiles with Leaflet) with the road the courier drives.
   Without internet (or when the map cannot load) the drawn SVG map of version 3 is used - no API key either way.
   Usage: <div class="map-box" data-live="/courier/live/YC....json"></div>
   The page asks the server for the courier's position every 4 seconds and moves the marker smoothly. */
(function () {
  "use strict";
  var NS = "http://www.w3.org/2000/svg", W = 800, H = 420;

  function el(name, attrs, parent) {
    var e = document.createElementNS(NS, name);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }

  function Map(box) {
    this.box = box;
    this.url = box.getAttribute("data-live");
    function word(k, dflt) {            // words come from hidden spans, so the page translator translates them
      var s = box.querySelector('[data-k="' + k + '"]');
      return s ? s.textContent.trim() : dflt;
    }
    this.labels = { pickup: word("pickup", "Pickup"), drop: word("drop", "Delivery") };
    this.svg = el("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": box.getAttribute("aria-label") || "Map" });
    box.insertBefore(this.svg, box.firstChild);
    this.shown = null;
    this.target = null;
    this.status = null;
    this.load();
  }

  Map.prototype.project = function (pts) {
    var lats = pts.map(function (p) { return p[0]; }), lngs = pts.map(function (p) { return p[1]; });
    var midLat = (Math.max.apply(null, lats) + Math.min.apply(null, lats)) / 2;
    var k = Math.cos(midLat * Math.PI / 180);
    var x0 = Math.min.apply(null, lngs) * k, x1 = Math.max.apply(null, lngs) * k;
    var y0 = Math.min.apply(null, lats), y1 = Math.max.apply(null, lats);
    var w = Math.max(x1 - x0, 0.01), h = Math.max(y1 - y0, 0.01);
    var s = Math.min((W * 0.78) / w, (H * 0.72) / h);
    var cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
    return function (lat, lng) { return [W / 2 + (lng * k - cx) * s, H / 2 - (lat - cy) * s]; };
  };

  Map.prototype.draw = function (d) {
    var svg = this.svg;
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    var pts = [[d.pickup.lat, d.pickup.lng]];
    if (d.drop) pts.push([d.drop.lat, d.drop.lng]);
    d.hubs.forEach(function (hb) { pts.push([hb.lat, hb.lng]); });
    if (d.courier) pts.push([d.courier.lat, d.courier.lng]);
    var P = this.project(pts);
    this.P = P;
    var defs = el("defs", {}, svg);
    var pat = el("pattern", { id: "yoGrid", width: 40, height: 40, patternUnits: "userSpaceOnUse" }, defs);
    el("path", { d: "M 40 0 L 0 0 0 40", fill: "none", stroke: "#dbe3ee", "stroke-width": 1 }, pat);
    var big = el("pattern", { id: "yoGrid2", width: 200, height: 200, patternUnits: "userSpaceOnUse" }, defs);
    el("rect", { width: 200, height: 200, fill: "url(#yoGrid)" }, big);
    el("path", { d: "M 200 0 L 0 0 0 200", fill: "none", stroke: "#c8d3e2", "stroke-width": 3 }, big);
    el("rect", { width: W, height: H, fill: "#eef2f7" }, svg);
    el("rect", { width: W, height: H, fill: "url(#yoGrid2)" }, svg);
    // towns
    var g = el("g", { "font-size": 12, fill: "#64748b", "font-family": "Segoe UI, Roboto, Arial, sans-serif" }, svg);
    var taken = [P(d.pickup.lat, d.pickup.lng)].concat(d.drop ? [P(d.drop.lat, d.drop.lng)] : [])
      .concat(d.hubs.map(function (hb) { return P(hb.lat, hb.lng); }));
    d.towns.forEach(function (t) {
      var p = P(t.lat, t.lng);
      if (p[0] < -20 || p[0] > W + 20 || p[1] < -20 || p[1] > H + 20) return;
      if (taken.some(function (q) { return Math.abs(q[0] - p[0]) < 70 && Math.abs(q[1] - p[1]) < 26; })) return;
      el("circle", { cx: p[0], cy: p[1], r: 3, fill: "#94a3b8" }, g);
      var tx = el("text", { x: p[0] + 6, y: p[1] - 5 }, g);
      tx.textContent = t.label;
    });
    // planned route
    var route = [[d.pickup.lat, d.pickup.lng]].concat(d.hubs.map(function (hb) { return [hb.lat, hb.lng]; }));
    if (d.drop) route.push([d.drop.lat, d.drop.lng]);
    var path = route.map(function (q, i) { var p = P(q[0], q[1]); return (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1); }).join(" ");
    el("path", { d: path, fill: "none", stroke: "#fff", "stroke-width": 9, "stroke-linecap": "round", "stroke-linejoin": "round" }, svg);
    el("path", { d: path, fill: "none", stroke: "#94a3b8", "stroke-width": 4, "stroke-dasharray": "2 9", "stroke-linecap": "round" }, svg);
    // where the courier really drove (the owner only)
    if (d.trail && d.trail.length > 1) {
      var tp = d.trail.map(function (q, i) { var p = P(q[0], q[1]); return (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1); }).join(" ");
      el("path", { d: tp, fill: "none", stroke: "#2563eb", "stroke-width": 4, "stroke-linecap": "round", opacity: 0.7 }, svg);
    }
    // hubs
    d.hubs.forEach(function (hb) {
      var p = P(hb.lat, hb.lng);
      el("rect", { x: p[0] - 8, y: p[1] - 8, width: 16, height: 16, rx: 3, fill: "#1d2b53", stroke: "#fff", "stroke-width": 2 }, svg);
      var t = el("text", { x: p[0] + 12, y: p[1] + 16, "font-size": 12, "font-weight": 700, fill: "#1d2b53" }, svg);
      t.textContent = hb.label;
    });
    this.pin(P(d.pickup.lat, d.pickup.lng), "#16a34a", "A", this.labels.pickup + " · " + d.pickup.label);
    if (d.drop) this.pin(P(d.drop.lat, d.drop.lng), "#ff6b35", "B", this.labels.drop + " · " + d.drop.label);
    // the courier
    this.courierG = el("g", { opacity: d.courier ? 1 : 0 }, svg);
    el("circle", { r: 22, fill: "#2563eb", opacity: 0.18 }, this.courierG);
    el("circle", { r: 14, fill: "#2563eb", stroke: "#fff", "stroke-width": 3 }, this.courierG);
    var ic = el("text", { "text-anchor": "middle", y: 5, "font-size": 14 }, this.courierG);
    var icons = { car: "🚗", van: "🚚", bicycle: "🚲", bus: "🚌", train: "🚆", plane: "✈️" };   // couriers and Yo Tickets
    ic.textContent = (d.courier && icons[d.courier.vehicle]) || "🛵";
    this.courierLabel = el("text", { x: 20, y: -18, "font-size": 12, "font-weight": 700, fill: "#1e3a8a",
                                     stroke: "#fff", "stroke-width": 3, "paint-order": "stroke" }, this.courierG);
    if (d.courier) {
      this.courierLabel.textContent = d.courier.name;
      this.shown = P(d.courier.lat, d.courier.lng);
      this.courierG.setAttribute("transform", "translate(" + this.shown[0] + " " + this.shown[1] + ")");
    }
  };

  Map.prototype.pin = function (p, color, letter, label) {
    var g = el("g", { transform: "translate(" + p[0] + " " + p[1] + ")" }, this.svg);
    el("path", { d: "M0 0 C -12 -16 -14 -22 -14 -28 A 14 14 0 1 1 14 -28 C 14 -22 12 -16 0 0 Z", fill: color, stroke: "#fff", "stroke-width": 2 }, g);
    var t = el("text", { y: -23, "text-anchor": "middle", "font-size": 13, "font-weight": 800, fill: "#fff" }, g);
    t.textContent = letter;
    var right = p[0] > W * 0.6;                       // near the right edge: the name goes on the left side
    var lb = el("text", { x: right ? -16 : 16, y: -30, "text-anchor": right ? "end" : "start", "font-size": 12,
                          "font-weight": 700, fill: "#111827", stroke: "#fff", "stroke-width": 3, "paint-order": "stroke" }, g);
    lb.textContent = label;
  };

  Map.prototype.move = function (d) {
    if (!d.courier) { this.courierG.setAttribute("opacity", 0); return; }
    this.courierG.setAttribute("opacity", 1);
    this.courierLabel.textContent = d.courier.name;
    var to = this.P(d.courier.lat, d.courier.lng), from = this.shown || to, self = this, start = null;
    if (to[0] < 0 || to[0] > W || to[1] < 0 || to[1] > H) { this.draw(d); return; }
    function step(ts) {
      if (!start) start = ts;
      var t = Math.min(1, (ts - start) / 3800);
      var x = from[0] + (to[0] - from[0]) * t, y = from[1] + (to[1] - from[1]) * t;
      self.courierG.setAttribute("transform", "translate(" + x + " " + y + ")");
      self.shown = [x, y];
      if (t < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  };

  Map.prototype.steps = function (d) {
    var list = document.querySelector("[data-steps]");
    if (!list) return;
    var items = list.querySelectorAll("li");
    for (var i = 0; i < items.length; i++) {
      items[i].classList.toggle("done", i < d.step || ((d.status === "Delivered" || d.status === "Arrived") && i <= d.step));
      items[i].classList.toggle("now", i === d.step && !d.done);
    }
  };

  Map.prototype.load = function () {
    var self = this;
    if (self.stopped) return;                     // a street map that was replaced by the drawn map
    fetch(this.url, { credentials: "same-origin", headers: { "Accept": "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) return;
        if (self.status === null) self.draw(d); else self.move(d);
        self.steps(d);
        if (self.status !== null && self.status !== d.status) {
          // a new step: reload once to show the new history (keeps the page simple)
          setTimeout(function () { location.reload(); }, 1200);
          return;
        }
        self.status = d.status;
        if (!d.done) setTimeout(function () { self.load(); }, 4000);
      })
      .catch(function () { setTimeout(function () { self.load(); }, 8000); });
  };

  /* ---------------- the street map (Leaflet + OpenStreetMap) ---------------- */
  function StreetMap(box, L) {
    this.box = box; this.L = L;
    this.url = box.getAttribute("data-live");
    var word = function (k, dflt) { var s = box.querySelector('[data-k="' + k + '"]'); return s ? s.textContent.trim() : dflt; };
    this.labels = { pickup: word("pickup", "Pickup"), drop: word("drop", "Delivery") };
    box.classList.add("leaflet");
    this.div = document.createElement("div");
    this.div.style.height = "100%";
    box.insertBefore(this.div, box.firstChild);
    this.map = L.map(this.div, { scrollWheelZoom: false, zoomControl: true });
    var self = this;
    window.YoLeaflet.tiles(L, this.map, function () {      // no street pictures: use the drawn map
      self.stopped = true;
      try { self.map.remove(); } catch (e) {}
      self.div.remove();
      box.classList.remove("leaflet");
      new Map(box);
    });
    this.layer = L.layerGroup().addTo(this.map);
    this.status = null; this.fitted = false; this.marker = null; this.roadKey = "";
    this.load();
  }
  function pinIcon(L, color, letter) {
    return L.divIcon({ className: "", iconSize: [30, 30], iconAnchor: [15, 30], popupAnchor: [0, -28],
                       html: '<div class="yo-pin" style="background:' + color + '"><span>' + letter + "</span></div>" });
  }
  StreetMap.prototype.draw = function (d) {
    var L = this.L, g = this.layer, pts = [];
    g.clearLayers();
    var icons = { car: "🚗", van: "🚚", bicycle: "🚲", bus: "🚌", train: "🚆", plane: "✈️" };
    var planned = [[d.pickup.lat, d.pickup.lng]].concat(d.hubs.map(function (h) { return [h.lat, h.lng]; }));
    if (d.drop) planned.push([d.drop.lat, d.drop.lng]);
    L.polyline(planned, { color: "#64748b", weight: 3, dashArray: "4 8", opacity: 0.8 }).addTo(g);
    this.road = L.polyline(d.road && d.road.length > 1 ? d.road : [], { color: "#2563eb", weight: 5, opacity: 0.75 }).addTo(g);
    this.roadKey = JSON.stringify(d.road || []).length + ":" + (d.road && d.road.length);
    if (d.trail && d.trail.length > 1) L.polyline(d.trail, { color: "#1d4ed8", weight: 4, opacity: 0.5 }).addTo(g);
    d.hubs.forEach(function (h) {
      L.marker([h.lat, h.lng], { icon: L.divIcon({ className: "", iconSize: [16, 16], html: '<div class="yo-hub"></div>' }) })
        .bindTooltip(h.label).addTo(g);
      pts.push([h.lat, h.lng]);
    });
    L.marker([d.pickup.lat, d.pickup.lng], { icon: pinIcon(L, "#16a34a", "A") })
      .bindTooltip(this.labels.pickup + " · " + d.pickup.label).addTo(g);
    pts.push([d.pickup.lat, d.pickup.lng]);
    if (d.drop) {
      L.marker([d.drop.lat, d.drop.lng], { icon: pinIcon(L, "#ff6b35", "B") })
        .bindTooltip(this.labels.drop + " · " + d.drop.label).addTo(g);
      pts.push([d.drop.lat, d.drop.lng]);
    }
    this.marker = L.marker(d.courier ? [d.courier.lat, d.courier.lng] : [d.pickup.lat, d.pickup.lng], {
      icon: L.divIcon({ className: "", iconSize: [34, 34], iconAnchor: [17, 17],
                        html: '<div class="yo-veh">' + ((d.courier && icons[d.courier.vehicle]) || "🛵") + "</div>" }),
      opacity: d.courier ? 1 : 0, zIndexOffset: 1000 }).addTo(g);
    if (d.courier) { this.marker.bindTooltip(d.courier.name, { permanent: true, direction: "top", offset: [0, -16] }); pts.push([d.courier.lat, d.courier.lng]); }
    if (!this.fitted) { this.map.fitBounds(L.latLngBounds(pts).pad(0.25), { maxZoom: 15 }); this.fitted = true; }
  };
  StreetMap.prototype.move = function (d) {
    var key = JSON.stringify(d.road || []).length + ":" + (d.road && d.road.length);
    if (key !== this.roadKey) { this.road.setLatLngs(d.road && d.road.length > 1 ? d.road : []); this.roadKey = key; }
    if (!d.courier) { this.marker.setOpacity(0); return; }
    this.marker.setOpacity(1);
    var from = this.marker.getLatLng(), to = [d.courier.lat, d.courier.lng], m = this.marker, start = null;
    function step(ts) {
      if (!start) start = ts;
      var t = Math.min(1, (ts - start) / 3800);
      m.setLatLng([from.lat + (to[0] - from.lat) * t, from.lng + (to[1] - from.lng) * t]);
      if (t < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  };
  StreetMap.prototype.steps = Map.prototype.steps;
  StreetMap.prototype.load = Map.prototype.load;

  function useDrawn() {
    try { return localStorage.getItem("yoMap") === "drawn"; } catch (e) { return false; }
  }
  document.querySelectorAll(".map-box[data-live]").forEach(function (box) {
    if (box.clientWidth < 600) { W = 440; H = 400; }      // phones: a smaller drawing keeps the labels readable
    var started = false;
    var drawn = function () { if (!started) { started = true; new Map(box); } };
    var sw = box.parentNode.querySelector("[data-map-switch]");
    if (sw) sw.addEventListener("click", function () {
      try { localStorage.setItem("yoMap", useDrawn() ? "street" : "drawn"); } catch (e) {}
      location.reload();
    });
    if (useDrawn() || !window.YoLeaflet) { drawn(); return; }
    window.YoLeaflet.load(function (L) { if (!started) { started = true; new StreetMap(box, L); } }, drawn);
  });
})();
