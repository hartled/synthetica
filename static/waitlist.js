(function () {
  function say(el, text, ok) {
    if (!el) return;
    el.textContent = text;
    el.className = "wl-msg " + (ok ? "ok" : "err");
  }

  document.querySelectorAll("form.waitlist-form").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var msg = form.querySelector(".wl-msg");
      var emailEl = form.querySelector('input[type="email"]');
      var websiteEl = form.querySelector('input[name="website"]');
      var picked = form.querySelector('input[name="price"]:checked');
      var button = form.querySelector("button");
      var body = {
        email: emailEl ? emailEl.value.trim() : "",
        product: form.getAttribute("data-product"),
        source: form.getAttribute("data-source"),
        website: websiteEl ? websiteEl.value : ""
      };
      if (picked && picked.value) body.price_usd = Number(picked.value);
      if (button) button.disabled = true;
      fetch("/api/waitlist", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(body)
      }).then(function (res) {
        var okText = form.getAttribute("data-ok") || "You're on the waitlist. No charge was made.";
        if (res.ok) say(msg, okText, true);
        else if (res.status === 429) say(msg, "Too many signups from this network. Try again later.", false);
        else say(msg, "Enter a valid email and try again.", false);
      }).catch(function () {
        say(msg, "Enter a valid email and try again.", false);
      }).then(function () {
        if (button) button.disabled = false;
      });
    });
  });
})();
