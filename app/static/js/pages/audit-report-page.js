// v8.2 Phase 3 — Audit Report page (Reports app)
import { route } from '../router.js';
import { api, apiPost } from '../api.js';
import { $, esc, fmtRs, fmtDate, toast, skeletonCards, errorBox } from '../utils.js';

const SVG = {
  audit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>',
  critical: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>',
  warning: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
  info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>',
  refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>',
};

route('/reports/audit', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-secondary">${SVG.audit}</div>
      <div>
        <h2 class="pos-page-header-title">AI Auditor</h2>
        <p class="pos-page-header-sub">Earnings integrity, safe withdrawal, and operational health checks. All offline — no LLM required.</p>
      </div>
      <div class="pos-page-header-actions">
        <button class="btn btn-primary btn-sm" id="audit-run">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.refresh}</span>
          Run Audit
        </button>
      </div>
    </div>
    <div id="audit-out">${skeletonCards(2)}</div>`;

  $('#audit-run').onclick = runAudit;
  await loadLatest();

  async function loadLatest() {
    try {
      const r = await api('/api/audit/latest');
      renderReport(r);
    } catch (e) {
      $('#audit-out').innerHTML = errorBox(e.message);
    }
  }

  function renderReport(data) {
    if (!data.run) {
      $('#audit-out').innerHTML = `<div class="card text-center" style="padding:48px">
        <h3 style="margin-bottom:8px">No audit runs yet</h3>
        <p class="text-dim text-sm" style="margin-bottom:16px">Click "Run Audit" to check earnings integrity, safe withdrawal, stock health, and more.</p>
        <button class="btn btn-primary" id="audit-run-first">Run First Audit</button>
      </div>`;
      $('#audit-run-first').onclick = runAudit;
      return;
    }
    const run = data.run;
    const findings = data.findings || [];
    const critical = findings.filter(f => f.severity === 'critical');
    const warnings = findings.filter(f => f.severity === 'warning');
    const infos = findings.filter(f => f.severity === 'info');

    // Stat cards
    let html = `<div class="grid grid-4" style="gap:12px;margin-bottom:16px">
      <div class="card" style="padding:16px;text-align:center">
        <div class="text-dim text-sm" style="text-transform:uppercase;font-weight:600">Critical</div>
        <div style="font-size:28px;font-weight:700;margin-top:4px;color:var(--danger-text,#DC2626)">${critical.length}</div>
      </div>
      <div class="card" style="padding:16px;text-align:center">
        <div class="text-dim text-sm" style="text-transform:uppercase;font-weight:600">Warnings</div>
        <div style="font-size:28px;font-weight:700;margin-top:4px;color:var(--warning-text,#D97706)">${warnings.length}</div>
      </div>
      <div class="card" style="padding:16px;text-align:center">
        <div class="text-dim text-sm" style="text-transform:uppercase;font-weight:600">Info</div>
        <div style="font-size:28px;font-weight:700;margin-top:4px;color:var(--text-dim,#64748B)">${infos.length}</div>
      </div>
      <div class="card" style="padding:16px;text-align:center">
        <div class="text-dim text-sm" style="text-transform:uppercase;font-weight:600">Last Run</div>
        <div style="font-size:14px;font-weight:600;margin-top:4px">${esc(fmtDate(run.run_at))}</div>
        <div class="text-dim text-sm">${esc(run.trigger)}</div>
      </div>
    </div>`;

    // Safe withdrawal summary
    html += `<div id="audit-safe-withdrawal" style="margin-bottom:16px"></div>`;

    // Findings grouped by severity
    if (findings.length === 0) {
      html += `<div class="card text-center" style="padding:32px">
        <div style="width:48px;height:48px;margin:0 auto 16px;background:var(--success-soft,#f0fdf4);color:var(--success-text,#16a34a);border-radius:14px;display:flex;align-items:center;justify-content:center">
          <span style="display:inline-flex;width:24px;height:24px">${SVG.check}</span>
        </div>
        <h3 style="margin-bottom:8px">All checks passed</h3>
        <p class="text-dim text-sm">No findings. Your earnings, withdrawals, and stock health look good.</p>
      </div>`;
    } else {
      html += renderFindingsSection('Critical', critical, 'danger');
      html += renderFindingsSection('Warnings', warnings, 'warning');
      html += renderFindingsSection('Info', infos, 'info');
    }

    $('#audit-out').innerHTML = html;

    // Load safe withdrawal
    api('/api/audit/safe-withdrawal').then(sw => {
      const el = $('#audit-safe-withdrawal');
      if (el) {
        const isOver = sw.is_over;
        el.innerHTML = `<div class="card" style="padding:16px;margin-bottom:0;background:${isOver ? 'var(--danger-soft,#FEE2E2)' : 'var(--success-soft,#f0fdf4)'};border:1px solid ${isOver ? 'var(--danger,#DC2626)' : 'var(--success,#16A34A)'}">
          <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
            <div>
              <strong style="color:${isOver ? 'var(--danger-text,#DC2626)' : 'var(--success-text,#16A34A)'}">
                ${isOver ? 'Over-withdrawn by ' + fmtRs(sw.over_amount) : 'Safe to withdraw ' + fmtRs(sw.remaining_safe)}
              </strong>
              <div class="text-sm" style="margin-top:2px;color:var(--text-dim,#64748B)">
                Cash: ${fmtRs(sw.cash)} | Stock replacement: ${fmtRs(sw.stock_replacement)} | OpEx: ${fmtRs(sw.operating_expenses)} | Reserve: ${fmtRs(sw.business_reserve)}
              </div>
            </div>
            <div style="text-align:right">
              <div class="text-sm">Withdrawn this month: <strong>${fmtRs(sw.withdrawn_this_month)}</strong></div>
              <div class="text-sm text-dim">Safe limit: ${fmtRs(sw.safe_withdrawal)}</div>
            </div>
          </div>
        </div>`;
      }
    }).catch(() => {});

    // Wire acknowledge buttons
    document.querySelectorAll('[data-ack-finding]').forEach(btn => {
      btn.onclick = async () => {
        const id = parseInt(btn.getAttribute('data-ack-finding'));
        try {
          await apiPost(`/api/audit/findings/${id}/acknowledge`, { reason: '' });
          toast('Finding acknowledged', 'success');
          await loadLatest();
        } catch (e) { toast('Ack failed: ' + e.message, 'error'); }
      };
    });
  }

  function renderFindingsSection(title, findings, severityClass) {
    if (findings.length === 0) return '';
    const icon = title === 'Critical' ? SVG.critical : title === 'Warnings' ? SVG.warning : SVG.info;
    const color = title === 'Critical' ? 'var(--danger-text,#DC2626)' :
                  title === 'Warnings' ? 'var(--warning-text,#D97706)' : 'var(--text-dim,#64748B)';
    return `<div class="card" style="padding:16px;margin-bottom:16px">
      <h3 style="margin:0 0 12px;display:flex;align-items:center;gap:6px">
        <span style="display:inline-flex;width:18px;height:18px;color:${color}">${icon}</span>
        ${title} (${findings.length})
      </h3>
      ${findings.map(f => `<div style="padding:12px;border-left:3px solid ${color};margin-bottom:8px;background:var(--bg-2,#F8FAFC);border-radius:0 8px 8px 0">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px">
          <div style="flex:1">
            <strong>${esc(f.title)}</strong>
            ${f.amount > 0 ? `<span class="chip chip-${severityClass} chip-sm" style="margin-left:6px">${fmtRs(f.amount)}</span>` : ''}
            <div class="text-dim text-sm" style="margin-top:4px;white-space:pre-wrap">${esc(f.detail)}</div>
            <div class="text-dim text-sm" style="margin-top:4px;font-size:11px">
              <span class="chip chip-info chip-sm">${esc(f.domain)}</span>
              <span class="chip chip-info chip-sm">${esc(f.check_key)}</span>
              ${f.status === 'acknowledged' ? '<span class="chip chip-success chip-sm">Acknowledged</span>' : ''}
            </div>
          </div>
          ${f.status === 'open' ? `<button class="btn btn-secondary btn-sm" data-ack-finding="${f.id}">Acknowledge</button>` : ''}
        </div>
      </div>`).join('')}
    </div>`;
  }

  async function runAudit() {
    const btn = $('#audit-run');
    btn.disabled = true;
    btn.innerHTML = '<span style="display:inline-flex;width:14px;height:14px;animation:spin 1s linear infinite"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg></span> Running...';
    try {
      const r = await apiPost('/api/audit/run', {});
      toast(`Audit complete: ${r.findings_count} findings (${r.critical_count} critical, ${r.warning_count} warning, ${r.info_count} info)`, 'success');
      await loadLatest();
    } catch (e) {
      toast('Audit failed: ' + e.message, 'error');
    }
    btn.disabled = false;
    btn.innerHTML = '<span style="display:inline-flex;width:14px;height:14px">' + SVG.refresh + '</span> Run Audit';
  }
});
