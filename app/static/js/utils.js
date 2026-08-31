// Utility functions: formatters, toast, modal, loading, icons

export const $ = (sel, el = document) => el.querySelector(sel);
export const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];

// ---------- Formatters ----------
// v8.5.5: maximumFractionDigits: 2 so avg_cost shows "191.87" not "192"
// For integer amounts (Rs 1,441,370), no decimals are shown automatically.
export const fmt = (n) => {
  if (n == null || isNaN(n)) return '';
  return Number(n).toLocaleString('en-PK', { maximumFractionDigits: 2 });
};

export const fmtRs = (n) => {
  if (n == null || isNaN(n)) return '—';
  return 'Rs ' + fmt(n);
};

export const fmtDate = (d) => d ? d.slice(0, 10) : '—';

// v8.4: fmtPct now expects the value to ALREADY be a percentage (e.g. 22.05 for 22.05%).
// Previously it multiplied by 100, which double-multiplied API values that were
// already percentages (margin_pct, monthly_margin, etc.).
// Callers passing decimals (0.22) should use fmtDecimalPct() instead.
export const fmtPct = (n, decimals = 1) => {
  if (n == null || isNaN(n)) return '—';
  return parseFloat(n).toFixed(decimals) + '%';
};
// For values that are decimals (0.22 = 22%) — multiplies by 100 first
export const fmtDecimalPct = (n, decimals = 1) => {
  if (n == null || isNaN(n)) return '—';
  return (parseFloat(n) * 100).toFixed(decimals) + '%';
};

export const esc = (s) => {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');  // C8 fix: single quotes are now escaped too
};

// v8.18.6: Bill flags must render as text, never '[object Object]'.
// Legacy bills stored cost-overrun warnings as objects ({message: "..."}).
// The API now flattens them server-side, but this keeps every renderer safe
// even if an object slips through any other path.
export const flagText = (f) => {
  if (f == null) return '';
  if (typeof f === 'object') {
    if (f.message) return String(f.message);
    try { return JSON.stringify(f); } catch { return 'Warning'; }
  }
  return String(f);
};

// ---------- Icons (inline SVG) ----------
const ICONS = {
  dashboard: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/></svg>',
  bills: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="16" y2="17"/></svg>',
  suppliers: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 9l1-5h16l1 5"/><path d="M4 9v11a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1V9"/><line x1="9" y1="13" x2="15" y2="13"/></svg>',
  reports: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>',
  insights: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg>',
  settings: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>',
  plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>',
  upload: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>',
  download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>',
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>',
  sun: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>',
  moon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
  edit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>',
  x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
  alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
  phone: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z"/></svg>',
  map: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>',
  file: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>',
  image: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>',
  inbox: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/></svg>',
  trendUp: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/></svg>',
  trendDown: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 18 13.5 8.5 8.5 13.5 1 6"/><polyline points="17 18 23 18 23 12"/></svg>',
  wallet: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12V7H5a2 2 0 0 1 0-4h14v4"/><path d="M3 5v14a2 2 0 0 0 2 2h16v-5"/><path d="M18 12a2 2 0 0 0 0 4h4v-4z"/></svg>',
  users: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
  save: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>',
  backup: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/></svg>',
  lock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>',
  brain: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96.44 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24 2.5 2.5 0 0 1 1.98-3A2.5 2.5 0 0 1 9.5 2Z"/><path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96.44 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-1.98-3A2.5 2.5 0 0 0 14.5 2Z"/></svg>',
  arrowLeft: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>',
  chevronLeft: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="15 18 9 12 15 6"/></svg>',
  chevronRight: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"/></svg>',
  store: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 9l1-5h16l1 5"/><path d="M4 9v11a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1V9"/><line x1="9" y1="13" x2="15" y2="13"/></svg>',
};

export function icon(name, size = 16) {
  const svg = ICONS[name] || '';
  return `<span class="icon" style="display:inline-flex;width:${size}px;height:${size}px">${svg}</span>`;
}

export function iconHtml(name, cls = '') {
  return `<span class="icon-wrap ${cls}">${ICONS[name] || ''}</span>`;
}

// ---------- Toast ----------
let toastTimer;
export function toast(msg, type = 'info', options = {}) {
  const container = $('#toast-container');
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  const iconSvg = type === 'success' ? ICONS.check
    : type === 'error' ? ICONS.alert
    : ICONS.inbox;  // L16 fix: distinct icon for info (not same as error)
  const actionHtml = options.action
    ? `<button class="toast-action" id="toast-action-${Date.now()}">${esc(options.action.label)}</button>`
    : '';
  el.innerHTML = `<span class="toast-icon" style="width:14px;height:14px;color:var(--${type === 'success' ? 'success' : type === 'error' ? 'danger' : 'accent'}-text)">${iconSvg}</span>
    <span class="toast-msg">${esc(msg)}</span>
    ${actionHtml}`;
  container.appendChild(el);
  if (options.action) {
    const btn = el.querySelector('button');
    if (btn) btn.onclick = () => {
      options.action.onClick();
      removeToast(el);
    };
  }
  const duration = options.duration || 3800;
  setTimeout(() => removeToast(el), duration);
}

function removeToast(el) {
  if (!el.parentNode) return;
  el.style.opacity = '0';
  el.style.transform = 'translateX(8px)';
  el.style.transition = 'all 0.2s ease';
  setTimeout(() => el.remove(), 200);
}

// Expose toast globally for non-module scripts (e.g., inline SW update notification)
window.__billbookToast = toast;

// ---------- Loading ----------
export function showLoading(text = 'Working...') {
  $('#loading-text').textContent = text;
  $('#loading-overlay').hidden = false;
}
export function hideLoading() {
  $('#loading-overlay').hidden = true;
}

// ---------- v8.18.0: Button busy-state helpers (double-click protection) ----------
// btnBusy() returns false if the button is ALREADY busy — callers use that as
// their re-entry guard. btnOk() restores the original label.
export function btnBusy(btn, text = 'Working…') {
  if (!btn || btn.disabled) return false;
  btn.disabled = true;
  btn.dataset.origHtml = btn.innerHTML;
  btn.innerHTML = `<span class="spinner-sm"></span> ${esc(text)}`;
  return true;
}
export function btnOk(btn) {
  if (!btn) return;
  btn.disabled = false;
  if (btn.dataset.origHtml) {
    btn.innerHTML = btn.dataset.origHtml;
    delete btn.dataset.origHtml;
  }
}

// ---------- Modal ----------
export function openModal(title, contentHtml, actionsHtml = '', subtitle = '') {
  const root = $('#modal-root');
  root.innerHTML = `
    <div class="modal-overlay" data-close>
      <div class="modal" onclick="event.stopPropagation()">
        <div class="modal-header">
          <div>
            <h2>${esc(title)}</h2>
            ${subtitle ? `<p class="modal-subtitle">${esc(subtitle)}</p>` : ''}
          </div>
          <button class="btn btn-ghost btn-icon btn-sm modal-x" data-modal-close title="Close (Esc)">${ICONS.x}</button>
        </div>
        <div class="modal-body">${contentHtml}</div>
        <div class="modal-actions">${actionsHtml}</div>
      </div>
    </div>`;
  // Click on overlay (outside modal) closes
  root.querySelector('[data-close]').addEventListener('click', (e) => {
    if (e.target.hasAttribute('data-close')) closeModal();
  });
  // Any button with data-modal-close inside closes the modal
  root.querySelectorAll('[data-modal-close]').forEach(btn => {
    btn.addEventListener('click', closeModal);
  });
  // Esc key closes
  const escHandler = (e) => {
    if (e.key === 'Escape') {
      closeModal();
      document.removeEventListener('keydown', escHandler);
    }
  };
  document.addEventListener('keydown', escHandler);
}
export function closeModal() {
  $('#modal-root').innerHTML = '';
}
// Expose to window so inline onclick="closeModal()" works
window.closeModal = closeModal;

// ---------- Appearance engine (v8.15.0 — design.md Claude-warm system) ----------
// Prior to v8.15.0 only `data-theme` was applied from localStorage: accent
// color, density and font scale were saved by the Appearance settings page
// but NEVER applied — the branding settings were effectively dead controls.
// This engine applies the full design.md token set and syncs with the server
// so choices follow the account across devices.

export const APPEARANCE_DEFAULTS = {
  theme: 'light',            // design.md: cream canvas is the brand default floor
  color_scheme: 'warm',      // v8.18.7: whole-system color scheme (canvas + tones + default accent)
  accent_color: '#cc785c',   // design.md: signature coral primary
  density: 'comfortable',
  font_scale: '100',
  serif_headings: true,      // design.md: serif display headlines (Cormorant Garamond)
  radius: 'standard',        // design.md rounded scale: 4/6/8/12/16
};

// v8.18.7: Whole-system color schemes. Each scheme restyles the entire
// token set — canvas/surface/borders/text tones for BOTH light and dark —
// and suggests a matching accent. The accent itself stays user-definable
// (the Brand Accent picker overrides the scheme's suggestion).
// 'warm' replicates the previous default exactly, so existing installs
// see zero visual change until they pick a scheme.
export const APPEARANCE_SCHEME_PRESETS = [
  {
    id: 'warm', name: 'Coral Warm', accent: '#cc785c', desc: 'Creams & coral — the BillBook signature',
    light: { bg: '#FFFFFF', surface: '#FFFFFF', elevated: '#F7F8F8', hover: 'rgba(0,0,0,0.03)', bgInput: 'rgba(0,0,0,0.02)', border: 'rgba(0,0,0,0.08)', borderStrong: 'rgba(0,0,0,0.12)', borderSubtle: 'rgba(0,0,0,0.04)', text: '#08090A', text2: '#62666D', muted: '#8A8F98', textQuat: '#D0D6E0' },
    dark:  { bg: '#08090A', surface: '#0F1011', elevated: '#18191A', hover: 'rgba(255,255,255,0.05)', bgInput: 'rgba(255,255,255,0.02)', border: 'rgba(255,255,255,0.05)', borderStrong: 'rgba(255,255,255,0.08)', borderSubtle: 'rgba(255,255,255,0.04)', text: '#F7F8F8', text2: '#8A8F98', muted: '#62666D', textQuat: '#3E3E44' },
  },
  {
    id: 'ocean', name: 'Ocean Blue', accent: '#3E7BB6', desc: 'Cool blues — calm and professional',
    light: { bg: '#F5F8FB', surface: '#FFFFFF', elevated: '#EBF1F6', hover: 'rgba(31,59,92,0.04)', bgInput: 'rgba(31,59,92,0.03)', border: 'rgba(31,59,92,0.10)', borderStrong: 'rgba(31,59,92,0.16)', borderSubtle: 'rgba(31,59,92,0.05)', text: '#0C1620', text2: '#55677A', muted: '#7C8DA0', textQuat: '#C9D4DE' },
    dark:  { bg: '#080B10', surface: '#0E141B', elevated: '#16202B', hover: 'rgba(160,200,255,0.06)', bgInput: 'rgba(160,200,255,0.03)', border: 'rgba(160,200,255,0.07)', borderStrong: 'rgba(160,200,255,0.11)', borderSubtle: 'rgba(160,200,255,0.05)', text: '#F0F6FC', text2: '#8DA0B5', muted: '#5F7183', textQuat: '#36444F' },
  },
  {
    id: 'forest', name: 'Forest Sage', accent: '#4E7D62', desc: 'Soft greens — natural and restful',
    light: { bg: '#F6F8F5', surface: '#FFFFFF', elevated: '#ECF1EA', hover: 'rgba(38,66,47,0.04)', bgInput: 'rgba(38,66,47,0.03)', border: 'rgba(38,66,47,0.10)', borderStrong: 'rgba(38,66,47,0.16)', borderSubtle: 'rgba(38,66,47,0.05)', text: '#101812', text2: '#5A6B5E', muted: '#7E8F82', textQuat: '#CBD5CC' },
    dark:  { bg: '#080D09', surface: '#0E1510', elevated: '#16211A', hover: 'rgba(170,230,190,0.05)', bgInput: 'rgba(170,230,190,0.03)', border: 'rgba(170,230,190,0.06)', borderStrong: 'rgba(170,230,190,0.10)', borderSubtle: 'rgba(170,230,190,0.05)', text: '#F2F8F3', text2: '#93A898', muted: '#62736A', textQuat: '#38443C' },
  },
  {
    id: 'violet', name: 'Violet Dusk', accent: '#7B6BB5', desc: 'Lavender & plum — distinctive and modern',
    light: { bg: '#F8F6FA', surface: '#FFFFFF', elevated: '#F0ECF5', hover: 'rgba(76,60,110,0.04)', bgInput: 'rgba(76,60,110,0.03)', border: 'rgba(76,60,110,0.10)', borderStrong: 'rgba(76,60,110,0.16)', borderSubtle: 'rgba(76,60,110,0.05)', text: '#131020', text2: '#62597A', muted: '#867C9E', textQuat: '#D0CADF' },
    dark:  { bg: '#0C0A10', surface: '#131019', elevated: '#1E1926', hover: 'rgba(200,180,255,0.06)', bgInput: 'rgba(200,180,255,0.03)', border: 'rgba(200,180,255,0.07)', borderStrong: 'rgba(200,180,255,0.11)', borderSubtle: 'rgba(200,180,255,0.05)', text: '#F6F4FB', text2: '#9A93AE', muted: '#6B657F', textQuat: '#403B50' },
  },
  {
    id: 'slate', name: 'Slate Mono', accent: '#5A6474', desc: 'Neutral grays — understated utility',
    light: { bg: '#F7F8F9', surface: '#FFFFFF', elevated: '#EEF0F2', hover: 'rgba(30,36,44,0.04)', bgInput: 'rgba(30,36,44,0.03)', border: 'rgba(30,36,44,0.10)', borderStrong: 'rgba(30,36,44,0.16)', borderSubtle: 'rgba(30,36,44,0.05)', text: '#0E1116', text2: '#59616C', muted: '#7F8894', textQuat: '#CDD3DA' },
    dark:  { bg: '#0A0B0D', surface: '#101114', elevated: '#191B1F', hover: 'rgba(200,210,220,0.05)', bgInput: 'rgba(200,210,220,0.03)', border: 'rgba(200,210,220,0.06)', borderStrong: 'rgba(200,210,220,0.10)', borderSubtle: 'rgba(200,210,220,0.05)', text: '#F5F7F8', text2: '#8E959D', muted: '#646A72', textQuat: '#3C4046' },
  },
];

// design.md accent presets (colors: primary, accent-teal, accent-amber)
export const APPEARANCE_ACCENT_PRESETS = [
  { name: 'Coral',   value: '#cc785c' },
  { name: 'Teal',    value: '#5db8a6' },
  { name: 'Amber',   value: '#e8a55a' },
];
const APPEARANCE_RADIUS = {
  compact:  ['4px', '6px', '8px',  '10px', '12px'],
  standard: ['6px', '8px', '10px', '12px', '16px'],
  roomy:    ['8px', '10px', '12px', '16px', '20px'],
};

function _hexToRgb(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex);
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
function _shade(hex, pct) {
  // pct < 0 darkens, > 0 lightens
  const rgb = _hexToRgb(hex); if (!rgb) return hex;
  const f = (c) => Math.max(0, Math.min(255, Math.round(pct < 0 ? c * (1 + pct) : c + (255 - c) * pct)));
  return '#' + rgb.map((c) => f(c).toString(16).padStart(2, '0')).join('');
}
function _alpha(hex, a) {
  const rgb = _hexToRgb(hex); if (!rgb) return hex;
  return `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${a})`;
}

export function normalizeAppearance(cfg) {
  const c = { ...APPEARANCE_DEFAULTS, ...(cfg || {}) };
  c.theme = (c.theme === 'dark') ? 'dark' : 'light';
  if (!APPEARANCE_SCHEME_PRESETS.some(s => s.id === c.color_scheme)) c.color_scheme = 'warm';
  if (!/^#[0-9a-fA-F]{6}$/.test(c.accent_color || '')) c.accent_color = APPEARANCE_DEFAULTS.accent_color;
  c.density = (c.density === 'compact') ? 'compact' : 'comfortable';
  const fs = parseInt(c.font_scale, 10);
  c.font_scale = String(!isNaN(fs) && fs >= 90 && fs <= 120 ? fs : 100);
  c.serif_headings = (c.serif_headings === true || c.serif_headings === '1' || c.serif_headings === 1);
  if (!APPEARANCE_RADIUS[c.radius]) c.radius = 'standard';
  return c;
}

export function applyAppearance(cfg) {
  const c = normalizeAppearance(cfg);
  const root = document.documentElement;

  // Theme
  root.setAttribute('data-theme', c.theme);
  localStorage.setItem('bb-theme', c.theme);

  // v8.18.7: Whole-system color scheme. Canvas/surface/border/text tokens
  // for the ACTIVE theme are set as inline root styles, which override both
  // the :root (dark) and [data-theme="light"] blocks in design-system.css —
  // every component that consumes var(--bg/--surface/--text…) restyles.
  const scheme = APPEARANCE_SCHEME_PRESETS.find(s => s.id === c.color_scheme) || APPEARANCE_SCHEME_PRESETS[0];
  const toks = c.theme === 'dark' ? scheme.dark : scheme.light;
  root.setAttribute('data-scheme', scheme.id);
  const canvasVars = {
    '--bg': toks.bg,
    '--surface': toks.surface,
    '--elevated': toks.elevated,
    '--hover': toks.hover,
    '--bg-input': toks.bgInput,
    '--border': toks.border,
    '--border-strong': toks.borderStrong,
    '--border-subtle': toks.borderSubtle,
    '--text': toks.text,
    '--text-2': toks.text2,
    '--muted': toks.muted,
    '--text-quaternary': toks.textQuat,
  };
  for (const [k, v] of Object.entries(canvasVars)) root.style.setProperty(k, v);

  // Density + typography + radius flags (CSS hooks live in base.css)
  root.setAttribute('data-density', c.density);
  root.setAttribute('data-serif', c.serif_headings ? 'on' : 'off');
  root.setAttribute('data-radius', c.radius);

  // Font & UI scale — uniform zoom on the root (px-based CSS scales reliably)
  const zoom = String(parseInt(c.font_scale, 10) / 100);
  root.style.zoom = zoom;

  // Accent: derive the full token family from one hex (design.md color roles)
  const hex = c.accent_color;
  const vars = {
    '--coral': hex,
    '--coral-active': _shade(hex, -0.14),
    '--coral-text': _shade(hex, -0.22),
    '--coral-soft': _alpha(hex, 0.12),
    '--coral-disabled': _alpha(hex, 0.35),
    '--accent': hex,
    '--accent-hover': _shade(hex, -0.14),
    '--accent-soft': _alpha(hex, 0.12),
    '--accent-text': _shade(hex, -0.22),
    '--accent-border': _alpha(hex, 0.25),
    '--primary': hex,
    '--primary-hover': _shade(hex, -0.14),
    '--primary-soft': _alpha(hex, 0.12),
    '--primary-text': _shade(hex, -0.22),
    '--primary-border': _alpha(hex, 0.25),
  };
  for (const [k, v] of Object.entries(vars)) root.style.setProperty(k, v);

  // Radius scale (design.md: xs/sm/md/lg/xl)
  const r = APPEARANCE_RADIUS[c.radius];
  const radii = { '--radius-xs': r[0], '--radius-sm': r[1], '--radius': r[2], '--radius-md': r[2], '--radius-lg': r[3], '--radius-xl': r[4] };
  for (const [k, v] of Object.entries(radii)) root.style.setProperty(k, v);
  // Legacy Linear radius names used by older components
  root.style.setProperty('--radius-control', r[1]);
  root.style.setProperty('--radius-card', r[2]);
  root.style.setProperty('--radius-modal', r[3]);

  // Mobile chrome color — track the scheme's canvas (not a hardcoded cream)
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = toks.bg;
  return c;
}

export function cacheAppearance(cfg) {
  try { localStorage.setItem('bb-appearance', JSON.stringify(cfg)); } catch (e) { /* private mode */ }
}
export function getCachedAppearance() {
  try { return JSON.parse(localStorage.getItem('bb-appearance') || 'null'); } catch (e) { return null; }
}

let _appearanceSynced = false;
export async function initAppearance() {
  // 1) Instant: apply cached choices so there is no flash of defaults.
  applyAppearance(getCachedAppearance());
  // 2) Authoritative: pull from the server so settings follow the account
  //    (and any device). Silent on failure — login screen, offline, etc.
  try {
    const res = await fetch('/api/appearance', { credentials: 'same-origin' });
    if (res.ok) {
      const cfg = await res.json();
      applyAppearance(cfg);
      cacheAppearance(cfg);
      _appearanceSynced = true;
    }
  } catch (e) { /* offline / pre-login — cached values stay applied */ }
  return _appearanceSynced;
}

export function getAppearance() {
  return normalizeAppearance(getCachedAppearance());
}

// Backward-compatible theme API (shell topbar / launcher / sidebar toggles)
export function initTheme() {
  applyAppearance(getCachedAppearance());
}
export function toggleTheme() {
  const cur = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
  const next = cur === 'dark' ? 'light' : 'dark';
  const cfg = getAppearance();
  cfg.theme = next;
  applyAppearance(cfg);
  cacheAppearance(cfg);
  // Best-effort server sync so the toggle follows the account too
  fetch('/api/appearance', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': (document.querySelector('meta[name="csrf-token"]') || {}).content || '' },
    body: JSON.stringify({ theme: next }),
  }).catch(() => {});
  return next;
}

// ---------- Empty state helper ----------
export function emptyState(title, msg, actionLabel = '', actionFn = '') {
  return `<div class="empty-state">
    <div class="empty-state-icon">${ICONS.inbox}</div>
    <h3>${esc(title)}</h3>
    <p>${esc(msg)}</p>
    ${actionLabel ? `<button class="btn" onclick="${actionFn}">${actionLabel}</button>` : ''}
  </div>`;
}

// ---------- Skeleton loaders ----------
export function skeletonRows(count = 5, cols = 6) {
  return `<table><tbody>${Array.from({length: count}).map(() =>
    `<tr>${Array.from({length: cols}).map(() =>
      `<td><div class="skeleton" style="height:14px;width:${60 + Math.random() * 40}%"></div></td>`
    ).join('')}</tr>`
  ).join('')}</tbody></table>`;
}

export function skeletonKpis(count = 4) {
  return `<div class="grid grid-${count}">${Array.from({length: count}).map(() =>
    `<div class="kpi"><div class="skeleton" style="height:11px;width:60px;margin-bottom:8px"></div>
     <div class="skeleton" style="height:26px;width:80px;margin-bottom:6px"></div>
     <div class="skeleton" style="height:11px;width:90px"></div></div>`
  ).join('')}</div>`;
}

export function skeletonCards(count = 3) {
  return `<div class="grid grid-${count}">${Array.from({length: count}).map(() =>
    `<div class="card"><div class="skeleton" style="height:18px;width:120px;margin-bottom:16px"></div>
     <div class="skeleton" style="height:14px;width:100%;margin-bottom:8px"></div>
     <div class="skeleton" style="height:14px;width:80%;margin-bottom:8px"></div>
     <div class="skeleton" style="height:14px;width:90%"></div></div>`
  ).join('')}</div>`;
}

// ---------- Error display ----------
export function errorBox(message, retryFn = '') {
  return `<div class="empty-state">
    <div class="empty-state-icon" style="background:var(--danger-soft);color:var(--danger-text)">${ICONS.alert}</div>
    <h3>Something went wrong</h3>
    <p>${esc(message)}</p>
    ${retryFn ? `<button class="btn" onclick="${retryFn}">Try Again</button>` : ''}
  </div>`;
}

// ---------- Sparkline (mini SVG line chart) ----------
export function sparkline(values, options = {}) {
  if (!values || values.length < 2) return '';
  const {
    width = 80, height = 24, color = 'currentColor', fillOpacity = 0.15,
    strokeWidth = 1.5,
  } = options;
  const max = Math.max(...values);
  const min = Math.min(...values);
  const range = max - min || 1;
  const stepX = width / (values.length - 1);
  const points = values.map((v, i) => {
    const x = i * stepX;
    const y = height - ((v - min) / range) * (height - 4) - 2;
    return [x, y];
  });
  // Smooth path via simple line segments
  const linePath = points.map((p, i) => (i === 0 ? `M ${p[0]},${p[1]}` : `L ${p[0]},${p[1]}`)).join(' ');
  const fillPath = `${linePath} L ${width},${height} L 0,${height} Z`;
  const lastX = points[points.length - 1][0];
  const lastY = points[points.length - 1][1];
  return `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" style="display:block">
    <path d="${fillPath}" fill="${color}" fill-opacity="${fillOpacity}" stroke="none"/>
    <path d="${linePath}" fill="none" stroke="${color}" stroke-width="${strokeWidth}" stroke-linejoin="round" stroke-linecap="round"/>
    <circle cx="${lastX}" cy="${lastY}" r="1.5" fill="${color}"/>
  </svg>`;
}

// ---------- Debounce ----------
export function debounce(fn, delay = 400) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), delay);
  };
}

// ---------- Chart.js theme-aware colors ----------
// Returns color set based on current data-theme attribute.
// SnowUI Free Style: charts must adapt to light/dark mode.
export function chartTheme() {
  const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
  return {
    isDark,
    tickColor: isDark ? '#a09d96' : '#6c6a64',
    gridColor: isDark ? 'rgba(250,249,245,0.06)' : 'rgba(20,20,19,0.06)',
    textColor: isDark ? '#d4cfc4' : '#3d3d3a',
    primary: '#cc785c',
    primarySoft: 'rgba(204, 120, 92, 0.12)',
    accent: '#5db8a6',
    accentSoft: 'rgba(93, 184, 166, 0.12)',
    warning: '#d4a017',
    success: '#5db872',
    danger: '#c64545',
    // v8.9: cream/coral/dark-navy palette for multi-series charts
    colors: isDark
      ? ['#cc785c', '#5db8a6', '#d4a017', '#5db872', '#c64545', '#e8a55a', '#a09d96', '#8e8b82']
      : ['#cc785c', '#5db8a6', '#d4a017', '#5db872', '#c64545', '#e8a55a', '#6c6a64', '#8e8b82'],
  };
}

// Build standard Chart.js options that adapt to current theme
export function chartOptions(extra = {}) {
  const t = chartTheme();
  return {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        position: 'bottom',
        labels: { color: t.tickColor, ...((extra.plugins?.legend?.labels) || {}) },
      },
      ...extra.plugins,
    },
    scales: {
      y: { ticks: { color: t.tickColor }, grid: { color: t.gridColor }, ...(extra.scales?.y || {}) },
      x: { ticks: { color: t.tickColor }, grid: { display: false }, ...(extra.scales?.x || {}) },
    },
    ...extra,
  };
}
