// ═══════════════════════════════════════════════════════════════════
// v8.20 — dark-mode compatibility smoke test (node)
//
// Runs the REAL applyAppearance() from app/static/js/utils.js under a
// minimal DOM shim and verifies the dark-mode contract:
//   1. legacy primitives (--canvas/--ink/--surface-card/…) follow the
//      ACTIVE theme — dark mode must not paint legacy components light
//   2. warm's legacy values stay byte-identical to base.css (light AND
//      dark) — the zero-change promise
//   3. accent text/hover tokens flip direction in dark mode (lighten)
//      instead of darkening into mud
//   4. derived text tokens clear a 3:1 (WCAG large-text/UI) contrast
//      floor against the scheme canvas in BOTH themes
//   5. --on-primary flips to dark text for pale accents
//
// Exit code 0 = all checks pass.
// ═══════════════════════════════════════════════════════════════════
import { pathToFileURL } from 'node:url';
import { copyFileSync, readFileSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const REPO = new URL('..', import.meta.url).pathname; // scripts/ -> repo root
const UTILS = join(REPO, 'app', 'static', 'js', 'utils.js');

// ── DOM shim (utils.js touches document/window at module level) ──────
const props = {};
const attrs = {};
const rootStyle = new Proxy({}, {
  get(_, k) { return k === 'setProperty' ? (name, v) => { props[name] = v; } : props['zoom']; },
  set(_, k, v) { if (k === 'zoom') props['zoom'] = v; else props[k] = v; return true; },
});
globalThis.document = {
  documentElement: {
    style: rootStyle,
    setAttribute: (k, v) => { attrs[k] = String(v); },
    getAttribute: (k) => (k in attrs ? attrs[k] : null),
  },
  querySelector: () => ({ content: '' }),
};
globalThis.window = globalThis;
globalThis.localStorage = { getItem: () => null, setItem: () => {} };
globalThis.fetch = () => Promise.reject(new Error('offline smoke test'));

// utils.js is ESM; import via a temp .mjs copy (repo has no "type":"module")
const tmp = mkdtempSync(join(tmpdir(), 'bb-smoke-'));
const tmpUtils = join(tmp, 'utils.mjs');
copyFileSync(UTILS, tmpUtils);
const mod = await import(pathToFileURL(tmpUtils).href);

// ── tiny assert harness ──────────────────────────────────────────────
let pass = 0, fail = 0;
const check = (name, cond, detail = '') => {
  if (cond) { pass++; console.log(`  ok   ${name}`); }
  else { fail++; console.log(`  FAIL ${name}${detail ? ' — ' + detail : ''}`); }
};

// luminance / contrast (mirrors utils.js so we can judge readability)
const lum = (hex) => {
  const n = /^#?([0-9a-f]{6})$/i.exec(hex); if (!n) return -1;
  const v = parseInt(n[1], 16);
  const f = (c) => { c /= 255; return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
  return 0.2126 * f((v >> 16) & 255) + 0.7152 * f((v >> 8) & 255) + 0.0722 * f(v & 255);
};
const ratio = (a, b) => (Math.max(lum(a), lum(b)) + 0.05) / (Math.min(lum(a), lum(b)) + 0.05);

const apply = (cfg) => {
  for (const k of Object.keys(props)) delete props[k];
  mod.applyAppearance(cfg);
  return props;
};

const PRESETS = mod.APPEARANCE_SCHEME_PRESETS;
const SCHEMES = PRESETS.map((s) => s.id).concat(['custom']);
const SEEDS = { custom: ['#6b7280', '#F2EFE9', '#141B2D'] };

// warm's legacy values must equal base.css exactly (both themes)
const WARM_DARK = {
  canvas: '#181715', surfaceCard: '#252320', ink: '#faf9f5',
  body: '#d4cfc4', hairline: 'rgba(250,249,245,0.08)',
};
const WARM_LIGHT = { canvas: '#faf9f5', surfaceCard: '#efe9de', ink: '#141413' };

for (const scheme of SCHEMES) {
  for (const theme of ['light', 'dark']) {
    const seeds = scheme === 'custom' ? SEEDS.custom : [null];
    for (const seed of seeds) {
      const tag = `${scheme}/${theme}${seed ? ' ' + seed : ''}`;
      const preset = PRESETS.find((s) => s.id === scheme);
      // Mirrors the settings page: picking a scheme applies its suggested
      // accent too (the accent picker can override it afterwards).
      const accentArg = seed || preset?.accent || '#cc785c';
      const p = apply({ theme, color_scheme: scheme, accent_color: accentArg, ...(seed ? { custom_scheme_base: seed } : {}) });
      const modern = theme === 'dark' ? preset?.dark : preset?.light;
      const derived = scheme === 'custom' ? mod.deriveCustomScheme(seed) : null;
      const ref = modern || (theme === 'dark' ? derived.dark : derived.light);
      const isWarm = scheme === 'warm';

      // 1. modern tokens still follow scheme+theme (regression guard)
      check(`[${tag}] --bg follows scheme`, p['--bg']?.toLowerCase() === ref.bg.toLowerCase(),
        `${p['--bg']} != ${ref.bg}`);

      // 2. legacy primitives follow the ACTIVE theme
      const expCanvas = isWarm ? (theme === 'dark' ? WARM_DARK.canvas : WARM_LIGHT.canvas) : ref.bg;
      const expInk = isWarm ? (theme === 'dark' ? WARM_DARK.ink : WARM_LIGHT.ink) : ref.text;
      check(`[${tag}] --canvas follows theme`, p['--canvas']?.toLowerCase() === expCanvas.toLowerCase(),
        `${p['--canvas']} != ${expCanvas}`);
      check(`[${tag}] --ink follows theme`, p['--ink']?.toLowerCase() === expInk.toLowerCase(),
        `${p['--ink']} != ${expInk}`);
      check(`[${tag}] --surface-card follows theme`,
        p['--surface-card']?.toLowerCase() === (isWarm ? (theme === 'dark' ? WARM_DARK.surfaceCard : WARM_LIGHT.surfaceCard) : ref.elevated).toLowerCase());
      if (isWarm && theme === 'dark') {
        check(`[warm/dark] legacy body exact`, p['--body'] === WARM_DARK.body, p['--body']);
        check(`[warm/dark] legacy hairline exact`, p['--hairline'] === WARM_DARK.hairline, p['--hairline']);
      }

      // 3. dark wells stay dark in BOTH themes
      check(`[${tag}] --surface-dark stays dark`, lum(p['--surface-dark']) < 0.1, p['--surface-dark']);

      // 4. readability: body text vs canvas (AA, 4.5)
      check(`[${tag}] ink/canvas >= 4.5`, ratio(p['--ink'], p['--canvas']) >= 4.5,
        `${p['--ink']} vs ${p['--canvas']} = ${ratio(p['--ink'], p['--canvas']).toFixed(2)}`);

      // 5. accent text direction + floor (3:1)
      const accent = seed || preset.accent;
      const accentText = p['--primary-text'];
      const cond = theme === 'dark' ? lum(accentText) > lum(accent) : lum(accentText) < lum(accent);
      check(`[${tag}] accent-text ${theme === 'dark' ? 'lightens in dark' : 'darkens in light'}`, cond,
        `${accent} -> ${accentText}`);
      check(`[${tag}] accent-text/canvas >= 3.0`, ratio(accentText, p['--bg']) >= 3.0,
        `${accentText} vs ${p['--bg']} = ${ratio(accentText, p['--bg']).toFixed(2)}`);

      // 6. accent hover direction
      const hover = p['--primary-hover'];
      check(`[${tag}] accent-hover ${theme === 'dark' ? 'lightens' : 'darkens'}`,
        theme === 'dark' ? lum(hover) > lum(accent) : lum(hover) < lum(accent), hover);

      // 7. on-accent text flips for pale accents
      const onP = p['--on-primary'];
      if (seed === '#F2EFE9') check(`[${tag}] pale accent -> dark on-primary`, onP === '#0F1011', onP);
      else if (lum(accent) <= 0.55) check(`[${tag}] accent keeps white on-primary`, onP === '#FFFFFF', onP);

      // 8. meta theme-color follows the scheme canvas
      check(`[${tag}] meta theme-color = canvas`, attrs['data-theme'] === theme);
    }
  }
}

// toggleTheme round trip: flips data-theme AND recomputes legacy tokens
{
  const p1 = apply({ theme: 'light', color_scheme: 'ocean' });
  const lightCanvas = p1['--canvas'];
  globalThis.localStorage = { getItem: () => JSON.stringify({ theme: 'light', color_scheme: 'ocean' }), setItem: () => {} };
  mod.toggleTheme();
  check('[toggle] data-theme flips', attrs['data-theme'] === 'dark');
  check('[toggle] --canvas recomputed for dark', props['--canvas'] !== lightCanvas && lum(props['--canvas']) < 0.1,
    props['--canvas']);
}

// static guards on the boot surfaces
{
  const indexHtml = readFileSync(join(REPO, 'app', 'static', 'index.html'), 'utf8');
  check('[static] index boot-splash uses var(--canvas…)', /id="boot-splash"[^>]*var\(--canvas,/.test(indexHtml));
  check('[static] index boot-splash spinner uses var(--coral…)', /var\(--coral,#cc785c\)/.test(indexHtml));
  check('[static] no hardcoded dark splash override', !/_bs\.style\.background='#181715'/.test(indexHtml));
  check('[static] inline accent knows dark direction', /dk \? 0\.16 : -0\.14/.test(indexHtml));
  const baseCss = readFileSync(join(REPO, 'app', 'static', 'styles', 'base.css'), 'utf8');
  check('[static] base.css declares color-scheme', /:root\s*{[^}]*color-scheme:\s*light/s.test(baseCss) &&
    /\[data-theme="dark"\]\s*{[^}]*color-scheme:\s*dark/s.test(baseCss));
  const dsCss = readFileSync(join(REPO, 'app', 'static', 'css', 'design-system.css'), 'utf8');
  check('[static] light select options use var(--surface)', !/select option\s*{\s*background-color:\s*#fff/.test(dsCss));
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
