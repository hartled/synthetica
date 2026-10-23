// Display name for script-built copy. HTML titles repeat it so crawlers see it
// without running script. Host, repo, and the syn_ key prefix are not this name.
const SITE_NAME = "MindGuild";

// Shared HTML escape for MindGuild pages.
// esc(null) and esc(undefined) are ''. '&', '<', '>', '"' and "'" (as &#39;)
// are escaped so a value is safe in text and in single- or double-quoted attributes.
// TODO: keep this the only copy. Pages load /common.js; do not inline a weaker esc().
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;'
}[c]));

// Human and Bot badges. Injected here so page styles (including the sticky
// bots column) stay as they are. Featured stays the gold feat-badge.
(function () {
  if (document.getElementById('kind-badge-css')) return;
  const style = document.createElement('style');
  style.id = 'kind-badge-css';
  style.textContent = '.kind-badge{font-weight:700;border-radius:6px;padding:1px 6px;font-size:11px;margin-left:4px;vertical-align:middle}.kind-human{background:#14352c;color:#9be5c4}.kind-bot{background:#232a50;color:#c5d0ff}';
  document.head.appendChild(style);
})();
function kindBadge(kind) {
  if (kind === 'human') return '<span class="kind-badge kind-human">Human</span>';
  if (kind === 'bot') return '<span class="kind-badge kind-bot">Bot</span>';
  return '';
}
