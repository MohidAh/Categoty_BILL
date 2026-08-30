// v8.0 Phase 4 — Transfer Out page (sender creates a challan)
// Transfer In page (receiver accepts/rejects incoming challans)
import { route, navigate } from '../router.js';
import { api, apiPost } from '../api.js';
import { $, esc, fmtRs, fmtDate, toast, openModal, closeModal, skeletonCards, errorBox, emptyState } from '../utils.js';
import { initListState } from '../list-state.js';

const SVG = {
  out: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>',
  in: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>',
  plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>',
  x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
  alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>',
  refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>',
  info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>',
};

// ─── Transfer Out page ─────────────────────────────────────────────────────

route('/transfers/out', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-warning">${SVG.out}</div>
      <div>
        <h2 class="pos-page-header-title">Transfer Out</h2>
        <p class="pos-page-header-sub">Send stock to another branch. Unit cost is locked at your current average — the receiver's average updates correctly.</p>
      </div>
    </div>
    <div id="tout-out">${skeletonCards(2)}</div>`;

  try {
    const [cfg, branchesR, invR] = await Promise.all([
      api('/api/branch-config'),
      api('/api/hq/branches?active_only=true'),
      api('/api/inventory'),
    ]);
    renderForm(cfg, branchesR.branches || [], invR.items || invR.categories || []);
  } catch (e) {
    $('#tout-out').innerHTML = errorBox(e.message);
  }

  function renderForm(cfg, branches, items) {
    const localBranchId = cfg.branch_id || 'BR-LOCAL';
    // Filter branches: exclude self
    const otherBranches = branches.filter(b => b.branch_id !== localBranchId);
    if (otherBranches.length === 0) {
      $('#tout-out').innerHTML = `<div class="card text-center" style="padding:48px">
        <h3 style="margin-bottom:8px">No other branches registered</h3>
        <p class="text-dim text-sm" style="max-width:400px;margin:0 auto 16px">
          You need at least one other active branch registered on HQ to transfer stock to.
          Go to <a href="#/insights/hq-branches" style="color:inherit;text-decoration:underline">HQ Branches</a> to register one.
        </p>
      </div>`;
      return;
    }
    // Filter items to those with stock > 0
    const stockItems = items.filter(i => (i.stock || i.current_qty || 0) > 0);
    if (stockItems.length === 0) {
      $('#tout-out').innerHTML = `<div class="card text-center" style="padding:48px">
        <h3 style="margin-bottom:8px">No stock to transfer</h3>
        <p class="text-dim text-sm">All your categories have zero stock. Confirm bills to build up stock first.</p>
      </div>`;
      return;
    }
    $('#tout-out').innerHTML = `
      <div class="card" style="padding:16px;margin-bottom:16px">
        <div class="grid grid-2" style="gap:12px;margin-bottom:12px">
          <div class="form-group">
            <label class="text-sm"><strong>From (your branch)</strong></label>
            <input class="input" value="${esc(cfg.branch_name || 'Main Shop')} (${esc(localBranchId)})" readonly style="margin-top:4px;background:var(--bg-2,#F1F5F9)">
          </div>
          <div class="form-group">
            <label class="text-sm"><strong>To (destination branch)</strong></label>
            <select class="input" id="tout-dest" style="margin-top:4px">
              ${otherBranches.map(b => `<option value="${esc(b.branch_id)}">${esc(b.name)} (${esc(b.branch_id)})</option>`).join('')}
            </select>
          </div>
        </div>
        <div class="form-group" style="margin-bottom:0">
          <label class="text-sm"><strong>Notes (optional)</strong></label>
          <input class="input" id="tout-notes" placeholder="e.g. Restocking for Eid season" style="margin-top:4px">
        </div>
      </div>

      <div class="card" style="padding:16px;margin-bottom:16px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
          <h3 style="margin:0">Line Items</h3>
          <button class="btn btn-secondary btn-sm" id="tout-add-line">
            <span style="display:inline-flex;width:12px;height:12px">${SVG.plus}</span>
            Add Line
          </button>
        </div>
        <div id="tout-lines"></div>
        <div id="tout-total" style="margin-top:12px;padding:12px;background:var(--bg-2,#F1F5F9);border-radius:8px;text-align:right"></div>
      </div>

      <div class="card" style="padding:12px;background:var(--bg-2,#F1F5F9);margin-bottom:16px;display:flex;gap:8px;align-items:flex-start">
        <span style="display:inline-flex;width:16px;height:16px;color:var(--text-dim,#64748B);flex-shrink:0;margin-top:2px">${SVG.info}</span>
        <div class="text-sm text-dim">
          <strong>How transfers work:</strong> Your stock is reduced immediately at your current average cost.
          The unit cost is locked into the challan. When the receiver accepts, their stock increases
          and their average cost updates correctly. Your average cost is <strong>unchanged</strong> —
          this is an inventory movement, not a sale (no COGS, no revenue).
        </div>
      </div>

      <button class="btn btn-primary" id="tout-submit" style="width:100%">
        <span style="display:inline-flex;width:14px;height:14px">${SVG.out}</span>
        Create Transfer Challan
      </button>`;

    let lineCounter = 0;
    const lines = [];

    function addLine() {
      const lineId = ++lineCounter;
      lines.push({ id: lineId, category_id: null, qty: 0 });
      const lineDiv = document.createElement('div');
      lineDiv.id = `tout-line-${lineId}`;
      lineDiv.style.cssText = 'display:grid;grid-template-columns:1fr 100px 120px 100px 40px;gap:8px;margin-bottom:8px;align-items:end';
      lineDiv.innerHTML = `
        <div class="form-group" style="margin:0">
          <label class="text-sm">Category</label>
          <select class="input tout-cat" data-line-id="${lineId}" style="margin-top:4px">
            <option value="">— Select —</option>
            ${stockItems.map(i => `<option value="${i.category_id || i.id}" data-avg="${i.avg_cost || i.current_avg_cost || 0}" data-stock="${i.stock || i.current_qty || 0}">${esc(i.code || '?')} — ${esc(i.name || '?')} (${i.stock || i.current_qty || 0} pcs @ ${fmtRs(i.avg_cost || i.current_avg_cost || 0)})</option>`).join('')}
          </select>
        </div>
        <div class="form-group" style="margin:0">
          <label class="text-sm">Qty</label>
          <input class="input tout-qty" data-line-id="${lineId}" type="number" min="1" step="1" placeholder="0" style="margin-top:4px">
        </div>
        <div class="form-group" style="margin:0">
          <label class="text-sm">Unit Cost</label>
          <input class="input tout-unit-cost" data-line-id="${lineId}" readonly style="margin-top:4px;background:var(--bg-2,#F1F5F9)">
        </div>
        <div class="form-group" style="margin:0">
          <label class="text-sm">Line Value</label>
          <input class="input tout-line-value" data-line-id="${lineId}" readonly style="margin-top:4px;background:var(--bg-2,#F1F5F9)">
        </div>
        <button class="btn btn-danger btn-sm tout-remove" data-line-id="${lineId}" style="height:38px">
          <span style="display:inline-flex;width:12px;height:12px">${SVG.trash}</span>
        </button>`;
      $('#tout-lines').appendChild(lineDiv);
      // Wire events
      lineDiv.querySelector('.tout-cat').onchange = (e) => updateLine(lineId);
      lineDiv.querySelector('.tout-qty').oninput = (e) => updateLine(lineId);
      lineDiv.querySelector('.tout-remove').onclick = () => {
        lineDiv.remove();
        const idx = lines.findIndex(l => l.id === lineId);
        if (idx >= 0) lines.splice(idx, 1);
        updateTotal();
      };
    }

    function updateLine(lineId) {
      const lineDiv = $(`#tout-line-${lineId}`);
      if (!lineDiv) return;
      const catSel = lineDiv.querySelector('.tout-cat');
      const qtyInput = lineDiv.querySelector('.tout-qty');
      const ucInput = lineDiv.querySelector('.tout-unit-cost');
      const lvInput = lineDiv.querySelector('.tout-line-value');
      const opt = catSel.options[catSel.selectedIndex];
      const avg = parseFloat(opt?.getAttribute('data-avg') || '0');
      const stock = parseFloat(opt?.getAttribute('data-stock') || '0');
      const qty = parseFloat(qtyInput.value || '0');
      ucInput.value = avg.toFixed(2);
      lvInput.value = (qty * avg).toFixed(2);
      // Update the lines array
      const idx = lines.findIndex(l => l.id === lineId);
      if (idx >= 0) {
        lines[idx] = { id: lineId, category_id: parseInt(catSel.value) || null, qty, avg, stock };
      }
      updateTotal();
    }

    function updateTotal() {
      let totalQty = 0, totalValue = 0;
      for (const l of lines) {
        if (l.category_id && l.qty > 0) {
          totalQty += l.qty;
          totalValue += l.qty * l.avg;
        }
      }
      $('#tout-total').innerHTML = `<strong>Total: ${totalQty} pcs · ${fmtRs(totalValue)}</strong>`;
    }

    $('#tout-add-line').onclick = addLine;
    // Start with one line
    addLine();

    $('#tout-submit').onclick = async () => {
      const validLines = lines.filter(l => l.category_id && l.qty > 0);
      if (validLines.length === 0) {
        toast('Add at least one line with a category and qty', 'error');
        return;
      }
      // Validate stock availability
      for (const l of validLines) {
        if (l.qty > l.stock) {
          toast(`Cannot transfer ${l.qty} pcs — only ${l.stock} in stock`, 'error');
          return;
        }
      }
      const toBranchId = $('#tout-dest').value;
      const notes = $('#tout-notes').value.trim();
      try {
        const r = await apiPost('/api/transfers/out', {
          to_branch_id: toBranchId,
          from_branch_id: localBranchId,
          lines: validLines.map(l => ({ category_id: l.category_id, qty: l.qty })),
          notes,
        });
        toast(`Transfer challan ${r.challan_no} created — ${r.total_qty} pcs, ${fmtRs(r.total_value)}`, 'success');
        // Navigate to Transfer In to show the new challan (well, the out direction)
        navigate('/transfers/in');
      } catch (e) {
        toast('Transfer failed: ' + e.message, 'error');
      }
    };
  }
});

// ─── Transfer In page (lists all challans — in + out) ─────────────────────

route('/transfers/in', async (el, path, q) => {
  // v8.18.5: status filter persists across navigation
  const st = initListState('transfersIn', q, { status: '' });
  st.syncUrlIfRestored();
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-success">${SVG.in}</div>
      <div>
        <h2 class="pos-page-header-title">Transfers</h2>
        <p class="pos-page-header-sub">Incoming challans to accept + history of all transfers.</p>
      </div>
      <div class="pos-page-header-actions">
        <select class="input input-sm" id="tin-filter" style="width:auto">
          <option value="">All</option>
          <option value="in_transit" ${st.val('status') === 'in_transit' ? 'selected' : ''}>In Transit</option>
          <option value="accepted" ${st.val('status') === 'accepted' ? 'selected' : ''}>Accepted</option>
          <option value="rejected" ${st.val('status') === 'rejected' ? 'selected' : ''}>Rejected</option>
        </select>
        <button class="btn btn-secondary btn-sm" id="tin-refresh">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.refresh}</span>
          Refresh
        </button>
      </div>
    </div>
    <div id="tin-out">${skeletonCards(2)}</div>`;

  $('#tin-filter').onchange = () => { st.replace({ status: $('#tin-filter').value }); loadTransfers(); };
  $('#tin-refresh').onclick = loadTransfers;
  await loadTransfers();

  async function loadTransfers() {
    try {
      const status = $('#tin-filter').value;
      const r = await api(`/api/transfers?${status ? 'status=' + status + '&' : ''}limit=100`);
      renderTransfers(r.transfers || []);
    } catch (e) {
      $('#tin-out').innerHTML = errorBox(e.message);
    }
  }

  function renderTransfers(transfers) {
    if (transfers.length === 0) {
      $('#tin-out').innerHTML = emptyState(
        'No transfers yet',
        'Transfer challans created by you or sent to you will appear here. Use the Transfer Out page to send stock to another branch.',
        '', ''
      );
      return;
    }
    $('#tin-out').innerHTML = `<div class="card" style="padding:0;overflow:hidden">
      <table class="table">
        <thead>
          <tr>
            <th>Challan No</th><th>From → To</th>
            <th style="text-align:right">Qty</th>
            <th style="text-align:right">Value</th>
            <th>Status</th><th>Created</th><th></th>
          </tr>
        </thead>
        <tbody>
          ${transfers.map(t => `<tr>
            <td><strong><code style="background:var(--bg-2,#F1F5F9);padding:1px 4px;border-radius:3px;font-size:11px">${esc(t.challan_no)}</code></strong></td>
            <td><span class="text-dim text-sm">${esc(t.from_branch_id)}</span> → <strong>${esc(t.to_branch_id)}</strong></td>
            <td style="text-align:right">${t.total_qty}</td>
            <td style="text-align:right">${fmtRs(t.total_value)}</td>
            <td>${t.status === 'in_transit'
              ? '<span class="chip chip-warning chip-sm">In Transit</span>'
              : t.status === 'accepted'
                ? '<span class="chip chip-success chip-sm">Accepted</span>'
                : '<span class="chip chip-danger chip-sm">Rejected</span>'}</td>
            <td class="text-dim text-sm">${esc(fmtDate(t.created_at))}</td>
            <td>
              ${t.status === 'in_transit'
                ? `<button class="btn btn-success btn-sm" data-accept="${t.id}">Accept</button>
                   <button class="btn btn-danger btn-sm" data-reject="${t.id}">Reject</button>`
                : `<button class="btn btn-secondary btn-sm" data-view="${t.id}">View</button>`}
            </td>
          </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
    // Wire buttons
    document.querySelectorAll('[data-accept]').forEach(btn => {
      btn.onclick = async () => {
        const id = parseInt(btn.getAttribute('data-accept'));
        if (!confirm('Accept this transfer? Stock will be added at the locked unit cost.')) return;
        try {
          await apiPost(`/api/transfers/${id}/accept`, {});
          toast('Transfer accepted — stock updated', 'success');
          await loadTransfers();
        } catch (e) { toast('Accept failed: ' + e.message, 'error'); }
      };
    });
    document.querySelectorAll('[data-reject]').forEach(btn => {
      btn.onclick = async () => {
        const id = parseInt(btn.getAttribute('data-reject'));
        const reason = prompt('Reject reason (optional):') || '';
        if (!confirm('Reject this transfer? No stock will be added.')) return;
        try {
          await apiPost(`/api/transfers/${id}/reject`, { reason });
          toast('Transfer rejected', 'info');
          await loadTransfers();
        } catch (e) { toast('Reject failed: ' + e.message, 'error'); }
      };
    });
    document.querySelectorAll('[data-view]').forEach(btn => {
      btn.onclick = async () => {
        const id = parseInt(btn.getAttribute('data-view'));
        try {
          const r = await api(`/api/transfers/${id}`);
          const items = r.items || [];
          openModal(`Challan ${r.challan.challan_no}`, `
            <div class="text-dim text-sm" style="margin-bottom:8px">
              <strong>From:</strong> ${esc(r.challan.from_branch_id)} →
              <strong>To:</strong> ${esc(r.challan.to_branch_id)}<br>
              <strong>Status:</strong> ${esc(r.challan.status)} ·
              <strong>Total:</strong> ${r.challan.total_qty} pcs · ${fmtRs(r.challan.total_value)}
            </div>
            <table class="table" style="font-size:13px">
              <thead><tr><th>Category</th><th style="text-align:right">Qty</th><th style="text-align:right">Unit Cost</th><th style="text-align:right">Line Value</th></tr></thead>
              <tbody>
                ${items.map(i => `<tr>
                  <td><strong>${esc(i.category_code || '?')}</strong> (Cat #${i.category_id})</td>
                  <td style="text-align:right">${i.qty}</td>
                  <td style="text-align:right">${fmtRs(i.unit_cost)}</td>
                  <td style="text-align:right">${fmtRs(i.line_value)}</td>
                </tr>`).join('')}
              </tbody>
            </table>`,
            `<button class="btn" data-close>Close</button>`);
        } catch (e) { toast('View failed: ' + e.message, 'error'); }
      };
    });
  }
});
