(function () {
  if (window.__mgUtmInit) return;
  window.__mgUtmInit = true;
  var fields = ["utm_source", "utm_medium", "utm_campaign", "utm_content"];
  var ok = /^[A-Za-z0-9._~-]{1,64}$/;

  function clean(value) {
    if (typeof value !== "string") return "";
    var text = value.trim();
    return ok.test(text) ? text : "";
  }

  function blank() {
    var out = {};
    fields.forEach(function (name) { out[name] = ""; });
    return out;
  }

  function any(data) {
    return fields.some(function (name) { return !!data[name]; });
  }

  function fromQuery() {
    var params = new URLSearchParams(window.location.search);
    var data = blank();
    fields.forEach(function (name) { data[name] = clean(params.get(name) || ""); });
    return any(data) ? data : null;
  }

  function fromStore() {
    try {
      var raw = sessionStorage.getItem("mg_utm");
      if (!raw) return null;
      var parsed = JSON.parse(raw);
      var data = blank();
      fields.forEach(function (name) { data[name] = clean(parsed && parsed[name]); });
      return any(data) ? data : null;
    } catch (err) {
      return null;
    }
  }

  var stored = fromStore();
  var query = fromQuery();
  var utm = stored || query || blank();
  if (!stored && query) {
    try { sessionStorage.setItem("mg_utm", JSON.stringify(query)); } catch (err) {}
  }
  window.__mgUtm = utm;

  fields.forEach(function (name) {
    var nodes = document.querySelectorAll('input[name="' + name + '"]');
    for (var i = 0; i < nodes.length; i++) {
      if (!nodes[i].value) nodes[i].value = utm[name] || "";
    }
  });
})();
