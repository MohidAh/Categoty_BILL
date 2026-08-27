// v8.15.0: appearance engine lives in utils.js (design.md Claude-warm system).
// This module previously held a divergent Linear-era copy of the theme logic
// (dark default, no accent/density/radius support) — it is now a thin re-export
// so every importer shares ONE implementation.
export {
  initTheme,
  toggleTheme,
  applyAppearance,
  initAppearance,
  getAppearance,
  getCachedAppearance,
  cacheAppearance,
  normalizeAppearance,
  APPEARANCE_DEFAULTS,
  APPEARANCE_ACCENT_PRESETS,
} from '../utils.js';
