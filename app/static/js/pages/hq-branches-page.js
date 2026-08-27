// v8.0 Phase 2 — HQ Branches page (on HQ instance)
// Lists registered branches, generates registration codes, revokes branches.
import { route } from '../router.js';
import { api, apiPost, apiDelete } from '../api.js';
import { $, esc, toast, openModal, closeModal, skeletonCards, errorBox } from '../utils.js';

const SVG = {
  branch: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 3v12"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/></svg>',
  plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
  refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>',
  copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>',
  clock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>',
  alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>',
  hq: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 21h18"/><path d="M5 21V7l8-4v18"/><path d="M19 21V11l-6-4"/></svg>',
};

route('/insights/hq-branches', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-info">${SVG.hq}</div>
      <div>
        <h2 class="pos-page-header-title">HQ Branch Registry</h2>
        <p class="pos-page-header-sub">Register new branches via 6-digit code, view registered branches, revoke access.</p>
      </div>
      <div class="pos-page-header-actions">
        <button class="btn btn-primary btn-sm" id="hq-gen-code">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.plus}</span>
          Generate Code
        </button>
        <button class="btn btn-secondary btn-sm" id="hq-refresh">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.refresh}</span>
          Refresh
        </button>
      </div>
    </div>
    <div id="hq-out">${skeletonCards(2)}</div>`;

  $('#hq-refresh').onclick = loadBranches;
  $('#hq-gen-code').onclick = generateCode;
  await loadBranches();

  async function loadBranches() {
    try {
      const r = await api('/api/hq/branches');
      renderBranches(r.branches || []);
    } catch (e) {
      $('#hq-out').innerHTML = errorBox(e.message);
    }
  }

  function renderBranches(branches) {
    if (branches.length === 0) {
      $('#hq-out').innerHTML = `<div class="card text-center" style="padding:48px">
        <div style="width:48px;height:48px;margin:0 auto 16px;background:var(--bg-2,#F1F5F9);color:var(--text-dim,#64748B);border-radius:14px;display:flex;align-items:center;justify-content:center">
          <span style="display:inline-flex;width:24px;height:24px">${SVG.branch}</span>
        </div>
        <h3 style="margin-bottom:8px">No branches registered yet</h3>
        <p class="text-dim text-sm" style="max-width:400px;margin:0 auto 16px">
          Click "Generate Code" above to get a 6-digit registration code. Share it with a branch
          owner — they'll enter it on their Branch Settings page to register.
        </p>
      </div>`;
      return;
    }
    const activeN = branches.filter(b => b.active).length;
    const revokedN = branches.length - activeN;
    let html = `<div class="grid grid-3" style="gap:12px;margin-bottom:16px">
      <div class="card" style="padding:16px;text-align:center">
        <div class="text-dim text-sm" style="text-transform:uppercase;font-weight:600">Total Branches</div>
        <div style="font-size:28px;font-weight:700;margin-top:4px">${branches.length}</div>
      </div>
      <div class="card" style="padding:16px;text-align:center">
        <div class="text-dim text-sm" style="text-transform:uppercase;font-weight:600">Active</div>
        <div style="font-size:28px;font-weight:700;margin-top:4px;color:var(--success-text,#16A34A)">${activeN}</div>
      </div>
      <div class="card" style="padding:16px;text-align:center">
        <div class="text-dim text-sm" style="text-transform:uppercase;font-weight:600">Revoked</div>
        <div style="font-size:28px;font-weight:700;margin-top:4px;color:var(--danger-text,#DC2626)">${revokedN}</div>
      </div>
    </div>
    <div class="card" style="padding:0;overflow:hidden">
      <table class="table">
        <thead>
          <tr>
            <th>Name</th><th>Branch ID</th><th>Region</th>
            <th>Last Seen</th><th>Status</th><th></th>
          </tr>
        </thead>
        <tbody>
          ${branches.map(b => `<tr>
            <td><strong>${esc(b.name)}</strong></td>
            <td><code style="background:var(--bg-2,#F1F5F9);padding:1px 4px;border-radius:3px;font-size:11px">${esc(b.branch_id)}</code></td>
            <td>${esc(b.region || '—')}</td>
            <td>${esc(b.last_seen || 'Never')}</td>
            <td>${b.active
              ? '<span class="chip chip-success chip-sm">Active</span>'
              : '<span class="chip chip-danger chip-sm">Revoked</span>'}</td>
            <td>${b.active ? `<button class="btn btn-danger btn-sm" data-revoke="${b.id}">
              <span style="display:inline-flex;width:12px;height:12px">${SVG.trash}</span>
              Revoke
            </button>` : ''}</td>
          </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
    $('#hq-out').innerHTML = html;
    // Wire revoke buttons
    document.querySelectorAll('[data-revoke]').forEach(btn => {
      btn.onclick = async () => {
        const id = parseInt(btn.getAttribute('data-revoke'));
        if (!confirm('Revoke this branch? Its sync token will no longer work.')) return;
        try {
          await apiDelete(`/api/hq/branches/${id}`);
          toast('Branch revoked', 'success');
          await loadBranches();
        } catch (e) { toast('Revoke failed: ' + e.message, 'error'); }
      };
    });
  }

  async function generateCode() {
    try {
      const r = await apiPost('/api/hq/branches/code', {});
      openModal('Branch Registration Code', `
        <div class="text-dim text-sm" style="margin-bottom:12px">
          Share this 6-digit code with a branch owner. They'll enter it on their
          <strong>Settings → Branch</strong> page to register their instance with this HQ.
        </div>
        <div style="text-align:center;padding:24px;background:var(--bg-2,#F1F5F9);border-radius:12px;margin-bottom:12px">
          <div style="font-size:48px;font-weight:800;letter-spacing:8px;color:var(--accent,#2563EB);font-family:monospace">${esc(r.code)}</div>
          <div class="text-dim text-sm" style="margin-top:8px;display:flex;align-items:center;justify-content:center;gap:4px">
            <span style="display:inline-flex;width:12px;height:12px">${SVG.clock}</span>
            Expires in ${Math.floor(r.expires_in / 60)} minutes
          </div>
        </div>
        <div class="text-dim text-sm" style="display:flex;gap:6px;align-items:flex-start">
          <span style="display:inline-flex;width:14px;height:14px;color:var(--warning-text,#D97706);flex-shrink:0;margin-top:2px">${SVG.alert}</span>
          <span>This code is <strong>single-use</strong>. Once a branch registers with it, it cannot be reused. Generate a new code for each branch.</span>
        </div>`,
        `<button class="btn" data-close>Close</button>
         <button class="btn btn-primary" id="hq-copy-code">
           <span style="display:inline-flex;width:14px;height:14px">${SVG.copy}</span>
           Copy Code
         </button>`);
      $('#hq-copy-code').onclick = () => {
        navigator.clipboard.writeText(r.code).then(() => {
          toast('Code copied to clipboard', 'success');
          closeModal();
        }).catch(() => toast('Copy failed — select and copy manually', 'error'));
      };
    } catch (e) {
      toast('Code generation failed: ' + e.message, 'error');
    }
  }
});
