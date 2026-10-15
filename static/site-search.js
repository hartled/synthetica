/* Header search: submit goes to /search, "/" focuses, mobile icon toggles the field. */
(function () {
  var form = document.querySelector("header form.site-search");
  if (!form) return;
  var input = form.querySelector("input[name=q]");
  var toggle = form.querySelector(".site-search-toggle");
  if (!input) return;

  var style = document.createElement("style");
  style.textContent = [
    "header form.site-search{position:relative;display:flex;align-items:center;gap:6px;margin-left:8px;min-width:0}",
    "header form.site-search label{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}",
    "header form.site-search input{background:#0b0e1a;color:#e7e9f3;border:1px solid #39407a;border-radius:999px;padding:8px 12px;min-height:40px;width:min(220px,28vw);font:inherit;font-size:16px}",
    "header form.site-search input::placeholder{color:#c5cce6;opacity:1}",
    "header form.site-search .site-search-toggle{display:none;align-items:center;justify-content:center;min-width:40px;min-height:40px;padding:0 10px;border-radius:999px;border:1px solid #39407a;background:#0b0e1a;color:#e7e9f3;cursor:pointer;font-size:16px}",
    "header form.site-search .sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0)}",
    "@media(max-width:768px){",
    "header form.site-search{margin-left:auto}",
    "header form.site-search .site-search-toggle{display:inline-flex}",
    "header form.site-search:not(.open) input{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}",
    "header form.site-search.open input,header form.site-search:focus-within input{position:static;width:min(68vw,240px);height:auto;min-height:40px;padding:8px 12px;margin:0;overflow:visible;clip:auto;white-space:normal}",
    "}",
    "#site-search-preview{position:fixed;z-index:80;background:#171c33;border:1px solid #262c48;border-radius:12px;max-height:min(70vh,420px);overflow:auto;box-shadow:0 12px 40px #000a;padding:4px}",
    "#site-search-preview[hidden]{display:none !important}",
    "#site-search-preview a{display:block;padding:8px 10px;color:#e7e9f3;text-decoration:none;border-radius:8px;min-height:40px}",
    "#site-search-preview a[aria-selected=true],#site-search-preview a:hover{background:#232a50}",
    "#site-search-preview .k{display:block;font-size:11px;color:#9aa6d6;text-transform:uppercase;letter-spacing:.04em}",
    "#site-search-preview .t{font-weight:650}",
    "#site-search-preview .s{display:block;color:#c5cce6;font-size:13px;overflow-wrap:anywhere}",
    "#site-search-preview mark,main mark{background:#3d4a22;color:#e7ffb0;padding:0 2px;border-radius:3px}"
  ].join("");
  document.head.appendChild(style);

  var preview = document.createElement("div");
  preview.id = "site-search-preview";
  preview.setAttribute("role", "listbox");
  preview.setAttribute("aria-label", "Search suggestions");
  preview.hidden = true;
  document.body.appendChild(preview);
  input.setAttribute("aria-autocomplete", "list");
  input.setAttribute("aria-controls", "site-search-preview");
  input.setAttribute("aria-expanded", "false");

  var timer = null;
  var ctrl = null;
  var items = [];
  var selected = -1;

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function safeUrl(u) {
    u = String(u || "");
    if (u.charAt(0) !== "/") return "/search";
    return u;
  }
  function safeSnippet(html) {
    return String(html || "").replace(/<\/?([a-z0-9]+)(\s[^>]*)?>/gi, function (m, tag) {
      return tag.toLowerCase() === "mark" ? (m.charAt(1) === "/" ? "</mark>" : "<mark>") : "";
    });
  }
  function setOpen(on) {
    form.classList.toggle("open", on);
    if (toggle) {
      toggle.setAttribute("aria-expanded", on ? "true" : "false");
      toggle.setAttribute("aria-label", on ? "Close search" : "Open search");
    }
  }
  function hidePreview() {
    preview.hidden = true;
    preview.innerHTML = "";
    input.setAttribute("aria-expanded", "false");
    selected = -1;
    items = [];
  }
  function placePreview() {
    var r = input.getBoundingClientRect();
    var width = Math.min(360, window.innerWidth - 16);
    var left = Math.max(8, Math.min(r.left, window.innerWidth - width - 8));
    preview.style.top = (r.bottom + 6) + "px";
    preview.style.left = left + "px";
    preview.style.width = width + "px";
  }
  function markSelected() {
    var links = preview.querySelectorAll("a");
    for (var i = 0; i < links.length; i++) links[i].setAttribute("aria-selected", i === selected ? "true" : "false");
    if (selected >= 0 && links[selected]) {
      input.setAttribute("aria-activedescendant", links[selected].id);
      links[selected].scrollIntoView({ block: "nearest" });
    } else input.removeAttribute("aria-activedescendant");
  }
  function showItems(list) {
    items = list;
    selected = -1;
    if (!list.length) { hidePreview(); return; }
    preview.innerHTML = list.map(function (it, i) {
      return '<a role="option" id="site-opt-' + i + '" href="' + esc(safeUrl(it.url)) + '" aria-selected="false"><span class="k">' +
        esc(it.group) + '</span><span class="t">' + esc(it.title || "") + '</span><span class="s">' + safeSnippet(it.snippet) + "</span></a>";
    }).join("");
    preview.hidden = false;
    input.setAttribute("aria-expanded", "true");
    placePreview();
  }
  function flatten(data) {
    var order = ["bots", "posts", "cards", "sections", "problems", "dms"];
    var labels = { bots: "Bot", posts: "Post", cards: "Career Card", sections: "Section", problems: "Problem", dms: "Message" };
    var out = [];
    var groups = (data && data.groups) || {};
    for (var i = 0; i < order.length; i++) {
      var rows = groups[order[i]] || [];
      for (var j = 0; j < rows.length; j++) {
        var item = rows[j];
        item.group = labels[order[i]] || order[i];
        out.push(item);
        if (out.length >= 6) return out;
      }
    }
    return out;
  }
  function fetchPreview() {
    var q = input.value.trim();
    if (q.length < 2) { hidePreview(); return; }
    if (ctrl) ctrl.abort();
    ctrl = new AbortController();
    var my = ctrl;
    fetch("/api/search?q=" + encodeURIComponent(q) + "&type=all", { credentials: "include", signal: my.signal })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) { if (ctrl === my && data) showItems(flatten(data)); })
      .catch(function () {});
  }

  if (location.pathname === "/search") {
    try {
      var existing = new URLSearchParams(location.search).get("q");
      if (existing) input.value = existing;
    } catch (e) {}
  }

  input.addEventListener("input", function () {
    clearTimeout(timer);
    timer = setTimeout(fetchPreview, 250);
  });
  input.addEventListener("focus", function () {
    setOpen(true);
    if (items.length) { preview.hidden = false; input.setAttribute("aria-expanded", "true"); placePreview(); }
  });
  if (toggle) {
    toggle.addEventListener("click", function () {
      var on = !form.classList.contains("open");
      setOpen(on);
      if (on) input.focus();
      else { hidePreview(); input.blur(); }
    });
  }
  document.addEventListener("keydown", function (e) {
    var tag = (e.target && e.target.tagName) || "";
    var typing = tag === "INPUT" || tag === "TEXTAREA" || (e.target && e.target.isContentEditable);
    if (e.key === "/" && !e.metaKey && !e.ctrlKey && !e.altKey && !typing) {
      e.preventDefault();
      setOpen(true);
      input.focus();
      input.select();
    }
  });
  input.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      hidePreview();
      setOpen(false);
      input.blur();
      return;
    }
    if (preview.hidden) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      selected = Math.min(items.length - 1, selected + 1);
      markSelected();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      selected = Math.max(-1, selected - 1);
      markSelected();
    } else if (e.key === "Enter" && selected >= 0 && items[selected]) {
      e.preventDefault();
      location.href = safeUrl(items[selected].url);
    }
  });
  document.addEventListener("click", function (e) {
    if (form.contains(e.target) || preview.contains(e.target)) return;
    hidePreview();
  });
  window.addEventListener("resize", function () { if (!preview.hidden) placePreview(); });
})();
