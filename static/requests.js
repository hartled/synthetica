/* Request board. User text is assigned with textContent only. */
(function () {
  var sort = "votes";
  var timer = null;
  var STATUS = {open: "Open", planned: "Planned", shipped: "Shipped", declined: "Declined"};

  function voteLabel(n) {
    var count = Number(n) || 0;
    return count === 1 ? "1 vote" : count + " votes";
  }

  function statusLabel(status) {
    return STATUS[status] || "Open";
  }

  function detail(data) {
    if (!data) return "Something went wrong. Try again.";
    if (typeof data.detail === "string") return data.detail;
    if (Array.isArray(data.detail) && data.detail[0] && data.detail[0].msg) return String(data.detail[0].msg);
    return "Something went wrong. Try again.";
  }

  function applyVote(btn, item) {
    var voted = !!item.voted;
    btn.disabled = voted;
    btn.setAttribute("aria-pressed", voted ? "true" : "false");
    btn.textContent = voted ? "Voted" : "Upvote";
    btn.setAttribute("aria-label", (voted ? "Voted on " : "Upvote ") + (item.title || "request"));
  }

  function fill(art, item) {
    art.dataset.id = String(item.id);
    var title = art.querySelector("h2");
    var desc = art.querySelector(".desc");
    var pill = art.querySelector(".pill.status");
    var count = art.querySelector(".count");
    var btn = art.querySelector("button.vote");
    if (title) title.textContent = item.title || "";
    if (desc) desc.textContent = item.description || "";
    if (pill) pill.textContent = statusLabel(item.status);
    if (count) count.textContent = voteLabel(item.vote_count);
    if (btn) applyVote(btn, item);
  }

  function card(item) {
    var art = document.createElement("article");
    art.className = "card idea";
    var h = document.createElement("h2");
    var badges = document.createElement("p");
    badges.className = "badges";
    var pill = document.createElement("span");
    pill.className = "pill status";
    badges.appendChild(pill);
    if (item.seeded) {
      var seed = document.createElement("span");
      seed.className = "pill seed";
      seed.textContent = "Seeded by the MindGuild team";
      badges.appendChild(seed);
    }
    var desc = document.createElement("p");
    desc.className = "desc";
    var row = document.createElement("div");
    row.className = "vote-row";
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "vote";
    btn.addEventListener("click", function () { cast(item.id, btn); });
    var count = document.createElement("span");
    count.className = "count";
    row.appendChild(btn);
    row.appendChild(count);
    art.appendChild(h);
    art.appendChild(badges);
    art.appendChild(desc);
    art.appendChild(row);
    fill(art, item);
    return art;
  }

  function paint(list) {
    var root = document.getElementById("list");
    var cards = root.querySelectorAll("article.idea");
    var same = cards.length === list.length;
    if (same) {
      for (var i = 0; i < list.length; i++) {
        if (cards[i].dataset.id !== String(list[i].id)) { same = false; break; }
      }
    }
    if (same && list.length) {
      list.forEach(function (item, i) { fill(cards[i], item); });
      return;
    }
    root.replaceChildren();
    if (!list.length) {
      var empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = "No requests yet. Be the first.";
      root.appendChild(empty);
      return;
    }
    list.forEach(function (item) { root.appendChild(card(item)); });
  }

  function load() {
    return fetch("/api/requests?sort=" + encodeURIComponent(sort), {credentials: "same-origin"})
      .then(function (res) {
        if (!res.ok) throw new Error("load");
        return res.json();
      })
      .then(function (data) { paint(data.requests || []); })
      .catch(function () {
        var root = document.getElementById("list");
        if (!root.querySelector("article.idea")) {
          root.replaceChildren();
          var p = document.createElement("p");
          p.className = "empty";
          p.textContent = "Could not load requests. Refresh and try again.";
          root.appendChild(p);
        }
      });
  }

  function setSort(next) {
    sort = next;
    document.getElementById("sort-votes").setAttribute("aria-pressed", next === "votes" ? "true" : "false");
    document.getElementById("sort-new").setAttribute("aria-pressed", next === "new" ? "true" : "false");
    load();
  }

  function cast(id, btn) {
    var status = document.getElementById("vote-status");
    btn.disabled = true;
    fetch("/api/requests/" + encodeURIComponent(id) + "/vote", {
      method: "POST",
      credentials: "same-origin",
      headers: {"Content-Type": "application/json"},
      body: "{}"
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) { return {res: res, data: data}; });
    }).then(function (out) {
      if (out.res.ok || out.res.status === 409) {
        status.textContent = out.res.ok
          ? "Voted. " + voteLabel(out.data.vote_count) + "."
          : detail(out.data);
        return load();
      }
      btn.disabled = false;
      status.textContent = out.res.status === 429
        ? "Too many votes from this network. Try again later."
        : detail(out.data);
    }).catch(function () {
      btn.disabled = false;
      status.textContent = "Could not record that vote. Try again.";
    });
  }

  function say(text, ok) {
    var msg = document.getElementById("form-msg");
    msg.textContent = text;
    msg.className = "msg " + (ok ? "ok" : "err");
    msg.setAttribute("role", ok ? "status" : "alert");
  }

  document.getElementById("sort-votes").addEventListener("click", function () { setSort("votes"); });
  document.getElementById("sort-new").addEventListener("click", function () { setSort("new"); });
  document.getElementById("request-form").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var button = ev.target.querySelector("button.submit");
    var email = document.getElementById("email").value.trim();
    var body = {
      title: document.getElementById("title").value,
      description: document.getElementById("description").value,
      website: document.getElementById("website").value
    };
    if (email) body.email = email;
    button.disabled = true;
    fetch("/api/requests", {
      method: "POST",
      credentials: "same-origin",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body)
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) { return {res: res, data: data}; });
    }).then(function (out) {
      button.disabled = false;
      if (!out.res.ok) {
        say(out.res.status === 429 ? "Too many requests from this network. Try again later." : detail(out.data), false);
        return;
      }
      document.getElementById("title").value = "";
      document.getElementById("description").value = "";
      document.getElementById("email").value = "";
      say("Posted. It is on the board with 0 votes.", true);
      setSort("new");
    }).catch(function () {
      button.disabled = false;
      say("Could not post that request. Try again.", false);
    });
  });

  load();
  timer = setInterval(function () {
    if (!document.hidden) load();
  }, 15000);
  window.addEventListener("pagehide", function () { clearInterval(timer); });
})();
