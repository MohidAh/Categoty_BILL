// v8.19: boot-splash theme bridge (loaded by loading.html on the bundled
// tauri.localhost origin). The splash shows BEFORE the backend is reachable,
// so it cannot call /api/appearance and cannot read the app origin's
// localStorage. Two payload sources:
//
//   1. window.__splashScheme — injected by the Rust shell, which reads
//      data/appearance.json (kept fresh by POST /api/appearance).
//   2. localStorage 'bb-appearance' — only populated when this page is
//      opened from the backend origin (dev mode); on the tauri origin it
//      is simply absent and we fall back to the page's neutral defaults.
//
// applyAppearance() (utils.js) computes the full token set, including the
// legacy primitives the splash CSS consumes (--canvas/--ink/--coral/…).
//
// Ordering is race-proof in both directions:
//   - Rust evals BEFORE this module runs -> payload var is set; we read it
//     at startup below.
//   - Rust evals AFTER this module ran -> the injected script calls
//     window.__setSplashScheme() which we define here.
import { applyAppearance } from './utils.js';

window.__setSplashScheme = (payload) => {
  try { applyAppearance(payload || {}); } catch (e) { /* keep neutral defaults */ }
};

let payload = null;
if (typeof window.__splashScheme === 'object' && window.__splashScheme) {
  payload = window.__splashScheme;
} else {
  try { payload = JSON.parse(localStorage.getItem('bb-appearance') || 'null'); } catch (e) { payload = null; }
}
if (payload) window.__setSplashScheme(payload);
