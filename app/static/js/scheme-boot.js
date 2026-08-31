// v8.19: scheme-boot — applies the saved color scheme on the PRE-LOGIN
// pages (login.html, license.html). These pages are served by the backend
// origin but never import the app bundle, so before this script they only
// flipped data-theme and kept the static base.css cream/coral tokens —
// i.e. the login and license screens ignored the color scheme entirely.
//
// We apply the localStorage cache (mirrored from /api/appearance by the
// main app on every sync) — instant, no network, works pre-auth. The
// server itself stays authoritative for logged-in sessions.
//
// Also exposes window.bbApplyTheme() so the pages' inline theme toggles
// can flip light/dark WITHOUT leaving stale scheme tokens behind (the old
// inline toggle only swapped data-theme, which broke the canvas colors
// once a scheme was active).
import { applyAppearance, getCachedAppearance } from '/static/js/utils.js';

applyAppearance(getCachedAppearance());

window.bbApplyTheme = (theme) => {
  const cfg = getCachedAppearance() || {};
  cfg.theme = theme === 'dark' ? 'dark' : 'light';
  applyAppearance(cfg);   // recomputes every token for the new theme
  return cfg.theme;
};
