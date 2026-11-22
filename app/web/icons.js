// Tiny stroke-icon set (24x24). Usage: <span data-ic="phone"></span> or svgIcon("phone", "lg")
const GK_ICONS = {
  phone:'<path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2z"/>',
  mic:'<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
  shield:'<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M9 12l2 2 4-4"/>',
  shieldx:'<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M9.5 9.5l5 5M14.5 9.5l-5 5"/>',
  calendar:'<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
  calcheck:'<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4M9 15l2 2 4-4"/>',
  check:'<path d="M5 12l5 5 9-10"/>', checks:'<path d="M2 13l4 4 8-9M10 15l2 2 9-10"/>', x:'<path d="M6 6l12 12M18 6L6 18"/>',
  send:'<path d="M4 12l16-8-6 16-3-7z"/>', bell:'<path d="M6 16v-5a6 6 0 0 1 12 0v5l2 2H4zM10 21h4"/>',
  moon:'<path d="M20 14A8 8 0 1 1 10 4a6 6 0 0 0 10 10z"/>', inbox:'<path d="M3 13l3-8h12l3 8v6H3zM3 13h5l1 3h6l1-3h5"/>',
  chat:'<path d="M4 5h16v11H9l-5 4z"/>', user:'<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  users:'<circle cx="9" cy="8" r="3.5"/><path d="M2 20a7 7 0 0 1 14 0M16 5a3.5 3.5 0 0 1 0 7M18 20a6 6 0 0 0-3-5"/>',
  clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>', zap:'<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>',
  lock:'<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>', play:'<path d="M7 4l13 8-13 8z"/>',
  refresh:'<path d="M20 11a8 8 0 0 0-14-4L4 9M4 4v5h5M4 13a8 8 0 0 0 14 4l2-2M20 20v-5h-5"/>',
  chart:'<path d="M4 20V10M10 20V4M16 20v-8M22 20H2"/>', db:'<ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>',
  bot:'<rect x="4" y="8" width="16" height="11" rx="3"/><path d="M12 8V4M9 13h.01M15 13h.01M9 17h6"/>', mail:'<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/>',
  globe:'<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18"/>', alert:'<path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18h.01"/>',
  flask:'<path d="M9 3h6M10 3v6L4 20h16L14 9V3M7 15h10"/>', star:'<path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z"/>',
  list:'<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>', eye:'<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  target:'<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>', mapbolt:'<path d="M12 21s7-6 7-11a7 7 0 0 0-14 0c0 5 7 11 7 11z"/><circle cx="12" cy="10" r="2.5"/>',
  wa:'<path d="M4 20l1.5-4.5A8.5 8.5 0 1 1 8.5 18.5z"/><path d="M9 9c0 3 3 6 6 6l1.5-1.5-2-1-1 .8c-1-.4-2-1.3-2.4-2.4l.8-1-1-2z"/>',
  layers:'<path d="M12 3l9 5-9 5-9-5zM3 13l9 5 9-5M3 17l9 5 9-5"/>', tool:'<path d="M14 6a4 4 0 0 0-5 5l-6 6 3 3 6-6a4 4 0 0 0 5-5l-3 3-2-2z"/>',
};
function svgIcon(name, cls = "") { return `<svg class="ic ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${GK_ICONS[name] || ""}</svg>`; }
function iconEl(name, cls = "") { const s = document.createElement("span"); s.style.display = "inline-flex"; s.innerHTML = svgIcon(name, cls); return s; }
function hydrateIcons(root = document) { root.querySelectorAll("[data-ic]").forEach((el) => { if (!el.dataset.done) { el.insertAdjacentHTML("afterbegin", svgIcon(el.dataset.ic, el.dataset.cls || "")); el.dataset.done = 1; } }); }
document.addEventListener("DOMContentLoaded", () => hydrateIcons());
