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
      var utm = window.__mgUtm || {};
      ["utm_source", "utm_medium", "utm_campaign", "utm_content"].forEach(function (name) {
        var input = form.querySelector('input[name="' + name + '"]');
        var value = input && input.value ? input.value : (utm[name] || "");
        if (/^[A-Za-z0-9._~-]{1,64}$/.test(value)) body[name] = value;
      });
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
