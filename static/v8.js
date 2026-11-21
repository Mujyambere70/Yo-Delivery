/* Yo Delivery 8: the three-lines menu, the category scroll row, the admin menu filter and live help-desk messages.
   Every feature works without this file too: the menu is a normal page (/menu), the categories scroll by touch,
   the admin menu is a plain list and a question page can be reloaded. */
(function () {
  "use strict";

  // ---------------------------------------------------------------- the menu drawer
  var drawer = document.getElementById("menuDrawer"), back = document.getElementById("menuBack");
  var opener = null, loaded = false;

  function closeMenu() {
    if (!drawer || drawer.hidden) return;
    drawer.hidden = true; back.hidden = true;
    document.body.classList.remove("drawer-open");
    if (opener) opener.focus();
  }

  function filterMenu(q) {
    q = q.trim().toLowerCase();
    var words = q.split(/\s+/).filter(Boolean), any = false;
    drawer.querySelectorAll(".menu-sec:not(.found)").forEach(function (sec) {
      var shown = 0;
      sec.querySelectorAll("a").forEach(function (a) {
        var hay = (a.textContent + " " + (a.getAttribute("data-words") || "")).toLowerCase();
        var ok = words.every(function (w) { return hay.indexOf(w) >= 0; });
        a.hidden = !ok; if (ok) shown++;
      });
      sec.hidden = !shown; if (shown) any = true;
    });
    var none = drawer.querySelector(".menu-none");
    if (none) none.hidden = any || !q;
    return any;
  }

  var findTimer = null;
  function serverFind(q) {
    var box = drawer.querySelector(".menu-sec.found");
    if (!box) return;
    if (q.trim().length < 3) { box.hidden = true; return; }
    clearTimeout(findTimer);
    findTimer = setTimeout(function () {
      fetch(drawer.getAttribute("data-find") + "?q=" + encodeURIComponent(q), { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          var rows = (data.results || []).filter(function (r) { return r.kind !== "Page"; });
          var list = box.querySelector("div");
          list.textContent = "";
          rows.forEach(function (r) {
            var a = document.createElement("a"), text = document.createElement("span"), small = document.createElement("small");
            var ico = document.createElement("span"), em = document.createElement("em");
            a.href = r.url; ico.textContent = "🔎"; text.textContent = r.label; small.textContent = r.sub || "";
            em.textContent = r.kind; text.appendChild(small); a.appendChild(ico); a.appendChild(text); a.appendChild(em);
            list.appendChild(a);
          });
          box.hidden = !rows.length;
          var none = drawer.querySelector(".menu-none");
          if (none && rows.length) none.hidden = true;
        }).catch(function () { box.hidden = true; });
    }, 250);
  }

  function wireMenu() {
    var close = drawer.querySelector(".menu-close"), input = drawer.querySelector(".menu-find input");
    if (close) close.addEventListener("click", closeMenu);
    if (input) {
      input.addEventListener("input", function () { filterMenu(input.value); serverFind(input.value); });
      setTimeout(function () { input.focus(); }, 30);
    }
  }

  function openMenu(from) {
    opener = from || document.getElementById("menuBtn");
    drawer.hidden = false; back.hidden = false;
    document.body.classList.add("drawer-open");
    if (loaded) { wireFocus(); return; }
    drawer.textContent = "…";
    fetch(drawer.getAttribute("data-src"), { credentials: "same-origin" })
      .then(function (r) { if (!r.ok) throw new Error("menu"); return r.text(); })
      .then(function (html) {
        drawer.innerHTML = html;             // our own page fragment (same origin, escaped by the server)
        loaded = true; wireMenu();
      })
      .catch(function () { window.location.href = (from && from.href) || "/menu"; });
  }

  function wireFocus() {
    var input = drawer.querySelector(".menu-find input");
    if (input) input.focus();
  }

  if (drawer && back) {
    document.querySelectorAll("#menuBtn, [data-menu]").forEach(function (btn) {
      btn.addEventListener("click", function (e) { e.preventDefault(); openMenu(btn); });
    });
    back.addEventListener("click", closeMenu);
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closeMenu();
      if (e.key === "Tab" && !drawer.hidden) {          // keep the keyboard inside the open menu
        var items = drawer.querySelectorAll("a[href]:not([hidden]), button, input");
        var list = Array.prototype.filter.call(items, function (el) { return el.offsetParent !== null; });
        if (!list.length) return;
        var first = list[0], last = list[list.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    });
  }

  // ---------------------------------------------------------------- categories: one row with arrows
  var row = document.getElementById("catRow");
  if (row) {
    var prev = document.querySelector(".cat-arrow.prev"), next = document.querySelector(".cat-arrow.next");
    var rtl = document.documentElement.dir === "rtl";
    var update = function () {
      var max = row.scrollWidth - row.clientWidth, x = Math.abs(row.scrollLeft);
      if (prev) prev.hidden = max < 8 || x < 8;
      if (next) next.hidden = max < 8 || x > max - 8;
    };
    var step = function (dir) { row.scrollBy({ left: dir * (rtl ? -1 : 1) * Math.max(220, row.clientWidth * 0.7), behavior: "smooth" }); };
    if (prev) prev.addEventListener("click", function () { step(-1); });
    if (next) next.addEventListener("click", function () { step(1); });
    row.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    var active = row.querySelector("a.active");       // no scrollIntoView: it would move the start of the Tab order
    if (active) row.scrollLeft = active.offsetLeft - row.offsetLeft - (row.clientWidth - active.clientWidth) / 2;
    update();
  }

  // ---------------------------------------------------------------- admin: filter of the side menu
  var navFilter = document.getElementById("admFilter");
  if (navFilter) {
    var nav = navFilter.closest("aside").querySelector("nav");
    navFilter.addEventListener("input", function () {
      var words = navFilter.value.trim().toLowerCase().split(/\s+/).filter(Boolean), group = null, shown = 0;
      Array.prototype.forEach.call(nav.children, function (el) {
        if (el.tagName === "H3") { if (group) group.hidden = !shown; group = el; shown = 0; return; }
        var hay = (el.textContent + " " + (el.getAttribute("data-words") || "")).toLowerCase();
        var ok = words.every(function (w) { return hay.indexOf(w) >= 0; });
        el.hidden = !ok; if (ok) shown++;
      });
      if (group) group.hidden = !shown;
    });
    navFilter.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { var first = nav.querySelector("a:not([hidden])"); if (first && navFilter.value.trim()) { e.preventDefault(); first.click(); } }
    });
    var on = nav.querySelector("a.on"), side = navFilter.closest("aside");
    if (on && side) side.scrollTop = Math.max(0, on.offsetTop - side.clientHeight / 2);   // keeps the Tab order at the top
  }

  // ---------------------------------------------------------------- help desk: new messages without reloading
  var thread = document.querySelector("[data-live-thread]");
  if (thread && window.fetch) {
    var url = thread.getAttribute("data-live-thread"), last = parseInt(thread.getAttribute("data-last") || "0", 10);
    var state = document.getElementById("liveState"), wait = 5000, quiet = 0;
    var poll = function () {
      if (document.hidden) { setTimeout(poll, wait); return; }
      fetch(url + "?after=" + last, { credentials: "same-origin" })
        .then(function (r) { if (!r.ok) throw new Error("live"); return r.json(); })
        .then(function (data) {
          (data.messages || []).forEach(function (m) {
            var box = document.createElement("div"), head = document.createElement("small"), who = document.createElement("b");
            var body = document.createElement("div"), side = thread.getAttribute("data-side");
            box.className = "msg" + (m.staff ? " staff" : "");
            who.textContent = (m.staff && side === "customer") ? "Yo support" : m.name;
            head.appendChild(who); head.appendChild(document.createTextNode(" \u00b7 " + m.at.slice(0, 16)));
            body.textContent = m.body;
            box.appendChild(head); box.appendChild(body); thread.appendChild(box);
            last = Math.max(last, m.id);
            box.scrollIntoView({ block: "nearest" });
          });
          var tn = document.getElementById("typingNow");            // version 9: "is typing"
          if (tn) tn.hidden = !data.typing;
          if (data.typing) quiet = 0;
          quiet = (data.messages || []).length ? 0 : quiet + 1;
          wait = data.typing ? 2500 : quiet > 24 ? 20000 : 5000;               // after two quiet minutes ask less often
          if (state) state.hidden = false;
          var st = document.getElementById("liveStatus");
          if (st && data.status) st.textContent = data.status;
          setTimeout(poll, wait);
        })
        .catch(function () { if (state) state.hidden = true; setTimeout(poll, 30000); });
    };
    setTimeout(poll, wait);
  }

  // ---------------------------------------------------------------- version 9: tell the other side "is typing" (at most every 3 s)
  var typingForm = document.querySelector("form[data-typing]");
  if (typingForm && window.fetch) {
    var sentAt = 0, area = typingForm.querySelector("textarea"), tok = typingForm.querySelector("[name=csrf_token]");
    if (area && tok) area.addEventListener("input", function () {
      if (Date.now() - sentAt < 3000 || !area.value.trim()) return;
      sentAt = Date.now();
      fetch(typingForm.getAttribute("data-typing"), { method: "POST", credentials: "same-origin",
            body: new URLSearchParams({ csrf_token: tok.value }) }).catch(function () {});
    });
  }

  // ---------------------------------------------------------------- support form: articles that may answer
  var subject = document.querySelector("[data-kb-suggest]");
  if (subject && window.fetch) {
    var box = document.getElementById("kbSuggest"), t = null;
    subject.addEventListener("input", function () {
      clearTimeout(t);
      t = setTimeout(function () {
        if (subject.value.trim().length < 6) { box.hidden = true; return; }
        fetch(subject.getAttribute("data-kb-suggest") + "?q=" + encodeURIComponent(subject.value))
          .then(function (r) { return r.json(); })
          .then(function (data) {
            var ul = box.querySelector("ul"); ul.textContent = "";
            (data.articles || []).forEach(function (a) {
              var li = document.createElement("li"), link = document.createElement("a");
              link.href = a.url; link.textContent = a.question; link.target = "_blank"; link.rel = "noopener";
              li.appendChild(link); ul.appendChild(li);
            });
            box.hidden = !(data.articles || []).length;
          }).catch(function () { box.hidden = true; });
      }, 350);
    });
  }
})();
