// Shared HTML escape for Synthetica pages.
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
