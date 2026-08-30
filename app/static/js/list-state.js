// List state persistence — keeps pagination, filters and sort selections
// alive across navigation (list → detail → back) and across sessions.
//
// STRATEGY: "URL first, storage fallback"
//   • If the URL carries query params, they WIN (deep links, browser
//     Back/Forward) and are saved to localStorage to stay in sync.
//   • If the URL is bare (user clicked a sidebar nav link or a "Back to
//     list" button that drops the query), the last-saved state for that
//     route is restored automatically — filters, sort and page number.
//   • Filter/sort changes update the URL SILENTLY via history.replaceState
//     (no history spam, no router re-render — the page refreshes its own
//     table) and persist to localStorage.
//   • Pagination changes PUSH a history entry via history.pushState, so the
//     browser Back/Forward buttons step through pages. pushState does not
//     fire hashchange, so the page still refreshes its own table; when the
//     user later hits Back, the hash changes and the router re-renders with
//     the URL's params.
//
// USAGE (inside a route handler):
//   import { initListState } from '../list-state.js';
//   route('/things', async (el, path, q) => {
//     const st = initListState('things', q, { q: '', status: '', page: 1 });
//     st.syncUrlIfRestored();                 // mirror restored state into URL
//     // ... render controls with st.val('q') / st.val('status') ...
//     // filter change:      st.replace({ q: 'x' }); loadTable(1);
//     // sort change:        st.replace({ sort_by: 'date' }); loadTable(1);
//     // pagination change:  st.push({ page: 2 }); loadTable(2);
//   });
//
// STORAGE SCHEMA: one localStorage key per route — "bb.list.<key>" holding a
// flat JSON object of the state. Values are always strings or ints (page).

const PREFIX = 'bb.list.';

function readSaved(key) {
  try {
    const raw = localStorage.getItem(PREFIX + key);
    return raw ? (JSON.parse(raw) || {}) : {};
  } catch { return {}; }  // corrupted value — fall back to defaults
}

function coerce(state) {
  if ('page' in state) state.page = Math.max(1, parseInt(state.page) || 1);
  return state;
}

export function initListState(key, urlQuery = {}, defaults = {}) {
  const saved = readSaved(key);
  const state = coerce({ ...defaults, ...saved });

  // URL params override everything (they're explicit intent). When the URL
  // carries ANY list param it is treated as the FULL spec: keys missing
  // from the URL fall back to DEFAULTS (not storage), so browser
  // Back/Forward between pushed states is exact.
  let urlHasParams = false;
  const urlVals = {};
  for (const k of Object.keys(defaults)) {
    const v = urlQuery?.[k];
    if (v !== undefined && v !== null && v !== '') {
      urlVals[k] = v;
      urlHasParams = true;
    }
  }
  if (urlHasParams) {
    Object.assign(state, { ...defaults, ...urlVals });
    persist();  // keep storage in sync with an explicit URL
  }
  coerce(state);

  function persist() {
    try { localStorage.setItem(PREFIX + key, JSON.stringify(state)); } catch { /* quota — ignore */ }
  }

  // Only non-default values are serialized — keeps the URL clean (a fresh
  // visit stays bare, "?sort_order=desc&page=1" noise is not written).
  function queryString() {
    const p = new URLSearchParams();
    for (const [k, v] of Object.entries(state)) {
      if (v === '' || v === null || v === undefined) continue;
      const d = defaults[k];
      const isDefault = (k === 'page') ? Number(v) === Number(d)
        : String(v) === String(d ?? '');
      if (!isDefault) p.set(k, v);
    }
    const s = p.toString();
    return s ? '?' + s : '';
  }

  // The current hash path without its query — e.g. '/bills'
  function currentPath() {
    return location.hash.split('?')[0].slice(1) || '/';
  }

  function apply(method, patch) {
    Object.assign(state, patch);
    coerce(state);
    persist();
    try {
      history[method + 'State'](null, '', '#' + currentPath() + queryString());
    } catch { /* some embedded webviews restrict this — storage still saved */ }
    return { ...state };
  }

  return {
    /** Snapshot of the current state (safe copy). */
    get: () => ({ ...state }),
    /** Value of a single key. */
    val: (k) => state[k],

    /** Silent URL update — no history entry, no router re-render.
     *  Use for filter / search / sort changes. */
    replace: (patch = {}) => apply('replace', patch),

    /** History entry — browser Back/Forward step through it.
     *  Use for pagination. Does not fire hashchange. */
    push: (patch = {}) => apply('push', patch),

    /** After restoring from storage on a bare URL, mirror the state into
     *  the URL so the address bar, sharing and Back/Forward all reflect it.
     *  No-op when the URL already carried params or nothing was restored. */
    syncUrlIfRestored() {
      if (urlHasParams) return;
      const qs = queryString();
      if (!qs) return;  // nothing saved beyond defaults — keep the URL bare
      try {
        history.replaceState(null, '', '#' + currentPath() + qs);
      } catch { /* ignore */ }
    },

    /** True if any non-default state is active (for "clear filters" buttons). */
    isFiltered: () => Object.entries(defaults).some(([k, v]) => {
      const cur = state[k];
      if (k === 'page') return cur > 1;
      return String(cur ?? '') !== String(v ?? '');
    }),
  };
}

/** Forget the saved state for a route key (used by "clear filters"). */
export function clearListState(key) {
  try { localStorage.removeItem(PREFIX + key); } catch { /* ignore */ }
}
