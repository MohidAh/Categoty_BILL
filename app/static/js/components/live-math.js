// v8.18.19 — LIVE MATH: click any calculated number to see HOW it was computed.
//
// WHY: users doubted the system's numbers. Now every key metric (margins,
// avg cost, COGS bridge, P&L…) renders as a clickable value; clicking opens
// a modal with the full step-by-step math — inputs, formula, the expression
// with live numbers substituted in, the result, which sales/bills were
// counted (provenance), and plain-language notes.
//
// The traces come from GET /api/calc/trace?metric=… built by app/calc_explain.py
// FROM THE SAME source functions the reports use — the math you see is the
// math you get.
//
// USAGE from any page:
//   import { lm } from '../components/live-math.js';
//   `${lm('overall_margin', {}, fmtPct(r.actual_overall_margin))}`
//   `${lm('category_margin', { category_id: c.id }, fmtPct(c.margin_pct))}`
//   `${lm('avg_cost', { category_id: i.id }, fmtRs(i.avg_cost))}`
//
// lm() only renders the button; a single document-level click listener
// (installed once, below) opens the modal. Nothing else to wire.

import { api } from '../api.js';
import { $, esc, fmt, fmtRs, openModal, closeModal } from '../utils.js';

const SVG_CALC = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:0.85em;height:0.85em;vertical-align:-0.08em"><rect x="4" y="2" width="16" height="20" rx="2"/><line x1="8" y1="6" x2="16" y2="6"/><line x1="8" y1="10" x2="8" y2="10.01"/><line x1="12" y1="10" x2="12" y2="10.01"/><line x1="16" y1="10" x2="16" y2="10.01"/><line x1="8" y1="14" x2="8" y2="14.01"/><line x1="12" y1="14" x2="12" y2="14.01"/><line x1="16" y1="14" x2="16" y2="14.01"/><line x1="8" y1="18" x2="12" y2="18"/></svg>';

/**
 * Render a clickable live-math value.
 * @param {string} metric   one of /api/calc/metrics (e.g. 'overall_margin')
 * @param {object} params   { category_id, month, start, end } as needed
 * @param {string} inner    the display HTML (already formatted value)
 */
export function lm(metric, params = {}, inner = '') {
  const p = JSON.stringify(params || {});
  return `<button type="button" class="live-math" data-lm="${esc(metric)}" data-lm-params="${esc(p)}" title="See the live math — how this number is calculated">${inner}${SVG_CALC}</button>`;
}

// ── value formatting per unit ──────────────────────────────────────────────
function fmtUnit(v, unit) {
  if (v == null || isNaN(v)) return '—';
  if (unit === 'percent') return `${fmt(v)}%`;
  if (unit === 'rs') return fmtRs(v);
  if (unit === 'qty') return fmt(v);
  return String(v);
}

function skeletonSteps() {
  return [1, 2, 3, 4].map(() => `
    <div class="lm-step" style="opacity:0.5">
      <div class="lm-step-n">…</div>
      <div class="lm-step-main"><div class="lm-step-label">Loading the math…</div></div>
      <div class="lm-step-val">—</div>
    </div>`).join('');
}

// ── the modal ──────────────────────────────────────────────────────────────
// Request-epoch guard: only the NEWEST openLiveMath call may touch the
// modal DOM. Without it, a slow older trace could render into a modal the
// user already closed (null crash) or one already re-opened for a different
// metric (wrong math shown). Found by the v8_18_19 E2E.
let _epoch = 0;

export async function openLiveMath(metric, params = {}) {
  const myEpoch = ++_epoch;
  openModal('Live Math', `
    <div id="lm-body" style="padding:8px 2px">
      ${skeletonSteps()}
    </div>`,
    '', 'Loading the calculation…');
  let t;
  try {
    const q = new URLSearchParams({ metric, ...(params || {}) }).toString();
    t = await api(`/api/calc/trace?${q}`);
  } catch (e) {
    if (myEpoch !== _epoch) return;  // superseded — leave the newer modal alone
    const b = $('#lm-body');
    if (b) b.innerHTML = `
      <div class="alert alert-danger" style="margin:12px 0">
        <strong>Could not load the math:</strong> ${esc(e.message)}
      </div>`;
    return;
  }
  if (myEpoch !== _epoch) return;  // a newer modal took over while we fetched
  const body = $('#lm-body');
  if (!body) return;  // modal was closed while fetching

  const stepsHtml = (t.steps || []).map(s => `
    <div class="lm-step ${s.is_result ? 'lm-step-result' : ''}">
      <div class="lm-step-n">${s.n}</div>
      <div class="lm-step-main">
        <div class="lm-step-label">${esc(s.label)}</div>
        ${s.formula ? `<div class="lm-step-formula">${esc(s.formula)}</div>` : ''}
        ${s.expression ? `<div class="lm-step-expr">${esc(s.expression)} =</div>` : ''}
        ${s.note ? `<div class="lm-step-note">${esc(s.note)}</div>` : ''}
      </div>
      <div class="lm-step-val">${esc(fmtUnit(s.value, s.unit))}</div>
    </div>`).join('');

  const evts = t.events || [];
  const eventsHtml = evts.length ? `
    <div class="lm-section">
      <div class="lm-section-title">How it built up — event by event</div>
      <div class="table-wrap" style="max-height:300px;overflow:auto">
        <table class="table table-sm lm-events">
          <thead><tr>
            <th>When</th><th>Event</th>
            <th class="table-num">Qty ±</th><th class="table-num">Pool Rs ±</th>
            <th class="table-num">Qty after</th><th class="table-num">Avg after</th>
          </tr></thead>
          <tbody>
            ${evts.map(e => `
              <tr class="lm-evt-${esc(e.type)}">
                <td class="text-dim">${esc((e.at || '').slice(0, 16))}</td>
                <td>${esc(e.label)}</td>
                <td class="table-num ${e.qty_delta >= 0 ? 'text-success' : 'text-danger'}">${e.qty_delta >= 0 ? '+' : ''}${fmt(e.qty_delta)}</td>
                <td class="table-num ${e.value_delta >= 0 ? 'text-success' : 'text-danger'}">${e.value_delta >= 0 ? '+' : '−'}${fmt(Math.abs(e.value_delta))}</td>
                <td class="table-num">${fmt(e.qty_after)}</td>
                <td class="table-num font-semibold">${fmtRs(e.avg_after)}</td>
              </tr>`).join('')}
          </tbody>
        </table>
      </div>
      ${t.replay_matches_state === false
        ? `<div class="alert alert-warning" style="margin-top:8px">The replay above ends at different numbers than the stored state — the note at the bottom explains what this means.</div>`
        : (t.replay_matches_state === true
          ? `<div class="text-xs text-success" style="margin-top:6px">✓ Replay matches the stored stock state exactly.</div>`
          : '')}
    </div>` : '';

  const provHtml = (t.provenance || []).length ? `
    <div class="lm-section">
      <div class="lm-section-title">What was counted</div>
      ${t.provenance.map(p => `
        <div class="lm-prov-row"><span>${esc(p.label)}</span><span class="font-semibold">${esc(p.value)}</span></div>`).join('')}
    </div>` : '';

  const notesHtml = (t.notes || []).length ? `
    <div class="lm-section">
      <div class="lm-section-title">Notes</div>
      <ul class="lm-notes">${t.notes.map(n => `<li>${esc(n)}</li>`).join('')}</ul>
    </div>` : '';

  body.innerHTML = `
    <div class="lm-head">
      <div>
        <div class="lm-title">${esc(t.title)}</div>
        ${t.subtitle ? `<div class="lm-subtitle">${esc(t.subtitle)}</div>` : ''}
      </div>
      <div class="lm-result ${t.result_unit === 'percent' ? 'lm-result-pct' : 'lm-result-rs'}">
        ${esc(fmtUnit(t.result, t.result_unit))}
      </div>
    </div>
    <div class="lm-steps">${stepsHtml}</div>
    ${eventsHtml}${provHtml}${notesHtml}
    <div class="lm-footer">
      <span class="text-dim text-xs">Computed ${esc(t.as_of || '')} · from the same formulas the reports use</span>
      <button class="btn btn-sm" id="lm-copy">Copy the math</button>
    </div>`;

  // Copy a plain-text version of the whole trace
  const copyBtn = $('#lm-copy');
  if (copyBtn) copyBtn.onclick = () => {
    const lines = [`${t.title} = ${fmtUnit(t.result, t.result_unit)}`, ''];
    for (const s of t.steps || []) {
      lines.push(`${s.n}. ${s.label}${s.expression ? ` — ${s.expression} = ${fmtUnit(s.value, s.unit)}` : ` = ${fmtUnit(s.value, s.unit)}`}`);
    }
    for (const p of t.provenance || []) lines.push(`• ${p.label}: ${p.value}`);
    for (const n of t.notes || []) lines.push(`ℹ ${n}`);
    const text = lines.join('\n');
    const done = () => { const b = $('#lm-copy'); if (b) { b.textContent = 'Copied ✓'; setTimeout(() => { if (b) b.textContent = 'Copy the math'; }, 1600); } };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done));
    } else fallbackCopy(text, done);
  };
}

function fallbackCopy(text, done) {
  const ta = document.createElement('textarea');
  ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
  document.body.appendChild(ta); ta.select();
  try { document.execCommand('copy'); } catch { /* ignore */ }
  document.body.removeChild(ta);
  if (done) done();
}

// ── single global delegated listener (installed once per page load) ───────
if (!window.__liveMathWired) {
  window.__liveMathWired = true;
  document.addEventListener('click', (e) => {
    const btn = e.target.closest && e.target.closest('[data-lm]');
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();
    let params = {};
    try { params = JSON.parse(btn.dataset.lmParams || '{}'); } catch { /* ignore */ }
    openLiveMath(btn.dataset.lm, params);
  });
}
