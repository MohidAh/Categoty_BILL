// v8.0 Phase 5 — Central Purchases page (HQ records bulk buys + distributes to branches)
import { route } from '../router.js';
import { api, apiPost } from '../api.js';
import { $, esc, fmtRs, fmtDate, toast, openModal, closeModal, skeletonCards, errorBox, emptyState } from '../utils.js';

const SVG = {
  hq: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 21h18"/><path d="M5 21V7l8-4v18"/><path d="M19 21V11l-6-4"/></svg>',
  plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
  refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>',
  distribute: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>',
  info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>',
};

route('/central-purchases', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-info">${SVG.hq}</div>
      <div>
        <h2 class="pos-page-header-title">Central Purchases</h2>
        <p class="pos-page-header-sub">Record bulk buys at Central Warehouse, then distribute to branches at the central unit cost.</p>
      </div>
      <div class="pos-page-header-actions">
        <button class="btn btn-primary btn-sm" id="cp-new">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.plus}</span>
          New Central Buy
        </button>
        <button class="btn btn-secondary btn-sm" id="cp-refresh">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.refresh}</span>
          Refresh
        </button>
      </div>
    </div>
    <div id="cp-out">${skeletonCards(2)}</div>`;

  $('#cp-refresh').onclick = loadPurchases;
  $('#cp-new').onclick = openNewPurchaseModal;
  await loadPurchases();

  async function loadPurchases() {
    try {
      const r = await api('/api/central-purchases');
      renderPurchases(r.purchases || []);
    } catch (e) {
      $('#cp-out').innerHTML = errorBox(e.message);
    }
  }

  function renderPurchases(purchases) {
    if (purchases.length === 0) {
      $('#cp-out').innerHTML = emptyState(
        'No central purchases yet',
        'Record a bulk buy at Central Warehouse, then distribute it to branches. Branches receive stock at the central unit cost — their moving average updates correctly.',
        '', ''
      );
      return;
    }
    $('#cp-out').innerHTML = `<div class="card" style="padding:0;overflow:hidden">
      <table class="table">
        <thead>
          <tr>
            <th>Purchase No</th><th>Supplier</th>
            <th style="text-align:right">Qty</th>
            <th style="text-align:right">Cost</th>
            <th>Status</th><th>Created</th><th></th>
          </tr>
        </thead>
        <tbody>
          ${purchases.map(p => `<tr>
            <td><strong><code style="background:var(--bg-2,#F1F5F9);padding:1px 4px;border-radius:3px;font-size:11px">${esc(p.purchase_no)}</code></strong></td>
            <td>${esc(p.supplier_name || '—')}</td>
            <td style="text-align:right">${p.total_qty}</td>
            <td style="text-align:right">${fmtRs(p.total_cost)}</td>
            <td>${p.status === 'recorded'
              ? '<span class="chip chip-warning chip-sm">Recorded</span>'
              : p.status === 'partial'
                ? '<span class="chip chip-info chip-sm">Partial</span>'
                : '<span class="chip chip-success chip-sm">Distributed</span>'}</td>
            <td class="text-dim text-sm">${esc(fmtDate(p.created_at))}</td>
            <td>
              <button class="btn btn-secondary btn-sm" data-view="${p.id}">View</button>
              ${p.status !== 'distributed' ? `<button class="btn btn-primary btn-sm" data-distribute="${p.id}">Distribute</button>` : ''}
            </td>
          </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
    document.querySelectorAll('[data-view]').forEach(btn => {
      btn.onclick = () => viewPurchase(parseInt(btn.getAttribute('data-view')));
    });
    document.querySelectorAll('[data-distribute]').forEach(btn => {
      btn.onclick = () => openDistributeModal(parseInt(btn.getAttribute('data-distribute')));
    });
  }

  async function viewPurchase(id) {
    try {
      const r = await api(`/api/central-purchases/${id}`);
      const items = r.items || [];
      openModal(`Central Purchase ${r.purchase.purchase_no}`, `
        <div class="text-dim text-sm" style="margin-bottom:8px">
          <strong>Supplier:</strong> ${esc(r.purchase.supplier_name || '—')} ·
          <strong>Status:</strong> ${esc(r.purchase.status)} ·
          <strong>Total:</strong> ${r.purchase.total_qty} pcs · ${fmtRs(r.purchase.total_cost)}
        </div>
        <table class="table" style="font-size:13px">
          <thead><tr><th>Category</th><th style="text-align:right">Qty</th><th style="text-align:right">Unit Cost</th><th style="text-align:right">Distributed</th><th style="text-align:right">Remaining</th></tr></thead>
          <tbody>
            ${items.map(i => `<tr>
              <td><strong>${esc(i.category_code || '?')}</strong> (Cat #${i.category_id})</td>
              <td style="text-align:right">${i.qty}</td>
              <td style="text-align:right">${fmtRs(i.unit_cost)}</td>
              <td style="text-align:right;color:var(--success-text,#16A34A)">${i.distributed_qty}</td>
              <td style="text-align:right;color:${i.remaining_qty > 0 ? 'var(--warning-text,#D97706)' : 'var(--text-dim,#64748B)'}">${i.remaining_qty}</td>
            </tr>`).join('')}
          </tbody>
        </table>`,
        `<button class="btn" data-close>Close</button>`);
    } catch (e) { toast('View failed: ' + e.message, 'error'); }
  }

  function openNewPurchaseModal() {
    openModal('New Central Purchase', `
      <div class="text-dim text-sm" style="margin-bottom:12px">
        Record a bulk buy at Central Warehouse. Stock is added to local state at the central unit cost.
        You can then distribute it to branches — they receive it at this locked price.
      </div>
      <div class="form-group" style="margin-bottom:12px">
        <label class="text-sm"><strong>Supplier Name (optional)</strong></label>
        <input class="input" id="cp-supplier" placeholder="e.g. ABC Trading" style="margin-top:4px">
      </div>
      <div class="form-group" style="margin-bottom:12px">
        <label class="text-sm"><strong>Lines (JSON)</strong></label>
        <textarea class="input" id="cp-lines" rows="6" style="margin-top:4px;font-family:monospace;font-size:12px" placeholder='[
  {"category_id": 1, "qty": 10000, "unit_cost": 180},
  {"category_id": 2, "qty": 5000, "unit_cost": 350}
]'></textarea>
      </div>
      <div class="form-group" style="margin-bottom:0">
        <label class="text-sm"><strong>Notes (optional)</strong></label>
        <input class="input" id="cp-notes" placeholder="e.g. Eid bulk buy" style="margin-top:4px">
      </div>`,
      `<button class="btn" data-close>Cancel</button>
       <button class="btn btn-primary" id="cp-create">Create</button>`);
    $('#cp-create').onclick = async () => {
      const supplier = $('#cp-supplier').value.trim();
      const linesRaw = $('#cp-lines').value.trim();
      const notes = $('#cp-notes').value.trim();
      let lines;
      try { lines = JSON.parse(linesRaw); }
      catch (e) { toast('Invalid JSON: ' + e.message, 'error'); return; }
      if (!Array.isArray(lines) || lines.length === 0) {
        toast('Lines must be a non-empty array', 'error'); return;
      }
      try {
        const r = await apiPost('/api/central-purchases', { supplier_name: supplier, lines, notes });
        toast(`Central purchase ${r.purchase_no} created — ${r.total_qty} pcs, ${fmtRs(r.total_cost)}`, 'success');
        closeModal();
        await loadPurchases();
      } catch (e) { toast('Create failed: ' + e.message, 'error'); }
    };
  }

  async function openDistributeModal(purchaseId) {
    try {
      const r = await api(`/api/central-purchases/${purchaseId}`);
      const branchesR = await api('/api/hq/branches?active_only=true');
      const otherBranches = (branchesR.branches || []).filter(b => b.branch_id !== 'BR-CENTRAL');
      const remainingItems = r.items.filter(i => i.remaining_qty > 0);
      if (remainingItems.length === 0) {
        toast('No remaining items to distribute', 'info'); return;
      }
      if (otherBranches.length === 0) {
        toast('No branches registered to distribute to', 'error'); return;
      }
      openModal(`Distribute ${r.purchase.purchase_no}`, `
        <div class="text-dim text-sm" style="margin-bottom:12px">
          Select a destination branch + quantities per line. The unit_cost is locked at the central bulk-buy price.
        </div>
        <div class="form-group" style="margin-bottom:12px">
          <label class="text-sm"><strong>To Branch</strong></label>
          <select class="input" id="dist-branch" style="margin-top:4px">
            ${otherBranches.map(b => `<option value="${esc(b.branch_id)}">${esc(b.name)} (${esc(b.branch_id)})</option>`).join('')}
          </select>
        </div>
        <div class="form-group" style="margin-bottom:0">
          <label class="text-sm"><strong>Lines (JSON) — max remaining per category</strong></label>
          <textarea class="input" id="dist-lines" rows="6" style="margin-top:4px;font-family:monospace;font-size:12px" placeholder='${JSON.stringify(remainingItems.map(i => ({category_id: i.category_id, qty: i.remaining_qty, remaining: i.remaining_qty})), null, 0)}'></textarea>
        </div>`,
        `<button class="btn" data-close>Cancel</button>
         <button class="btn btn-primary" id="dist-confirm">
           <span style="display:inline-flex;width:14px;height:14px">${SVG.distribute}</span>
           Distribute
         </button>`);
      $('#dist-confirm').onclick = async () => {
        const toBranch = $('#dist-branch').value;
        const linesRaw = $('#dist-lines').value.trim();
        let lines;
        try { lines = JSON.parse(linesRaw); }
        catch (e) { toast('Invalid JSON: ' + e.message, 'error'); return; }
        try {
          const r2 = await apiPost(`/api/central-purchases/${purchaseId}/distribute`, {
            to_branch_id: toBranch, lines,
          });
          toast(`Distributed ${r2.total_qty} pcs via ${r2.challan_no} (${r2.purchase_status})`, 'success');
          closeModal();
          await loadPurchases();
        } catch (e) { toast('Distribute failed: ' + e.message, 'error'); }
      };
    } catch (e) { toast('Open distribute failed: ' + e.message, 'error'); }
  }
});
