// v8.0 Phase 6 — Global Price Push page (HQ pushes price updates to all branches)
import { route } from '../router.js';
import { api, apiPost } from '../api.js';
import { $, esc, fmtRs, fmtDate, toast, openModal, closeModal, skeletonCards, errorBox, emptyState } from '../utils.js';

const SVG = {
  push: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>',
  tag: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg>',
  refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>',
  info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>',
};

route('/insights/price-push', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-info">${SVG.push}</div>
      <div>
        <h2 class="pos-page-header-title">Price Push</h2>
        <p class="pos-page-header-sub">Push price-category updates to all branches. Idempotent — re-delivery never double-applies.</p>
      </div>
      <div class="pos-page-header-actions">
        <button class="btn btn-primary btn-sm" id="pp-new">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.push}</span>
          New Price Push
        </button>
        <button class="btn btn-secondary btn-sm" id="pp-refresh">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.refresh}</span>
          Refresh
        </button>
      </div>
    </div>
    <div id="pp-out">${skeletonCards(2)}</div>`;

  $('#pp-refresh').onclick = loadPushes;
  $('#pp-new').onclick = openNewPushModal;
  await loadPushes();

  async function loadPushes() {
    try {
      const r = await api('/api/hq/price-pushes');
      renderPushes(r.pushes || []);
    } catch (e) {
      $('#pp-out').innerHTML = errorBox(e.message);
    }
  }

  function renderPushes(pushes) {
    if (pushes.length === 0) {
      $('#pp-out').innerHTML = emptyState(
        'No price pushes yet',
        'Push a price update to all registered branches. Each branch applies it idempotently — the audit trail shows the source as HQ.',
        '', ''
      );
      return;
    }
    $('#pp-out').innerHTML = `<div class="card" style="padding:0;overflow:hidden">
      <table class="table">
        <thead>
          <tr>
            <th>Push ID</th><th>Category</th>
            <th style="text-align:right">New Price</th>
            <th>Notes</th><th>Pushed/Applied</th><th>Status</th>
          </tr>
        </thead>
        <tbody>
          ${pushes.map(p => `<tr>
            <td><code style="background:var(--bg-2,#F1F5F9);padding:1px 4px;border-radius:3px;font-size:11px">${esc(p.price_push_id)}</code></td>
            <td><strong>${esc(p.category_code || '?')}</strong> (Cat #${p.category_id})</td>
            <td style="text-align:right">${fmtRs(p.new_sell_price)}</td>
            <td class="text-dim text-sm">${esc(p.notes || '—')}</td>
            <td class="text-dim text-sm">${esc(fmtDate(p.pushed_at))}${p.applied_at ? '<br>applied: ' + esc(fmtDate(p.applied_at)) : ''}</td>
            <td>${p.applied_at
              ? '<span class="chip chip-success chip-sm">Applied</span>'
              : '<span class="chip chip-info chip-sm">Created (HQ)</span>'}</td>
          </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
  }

  function openNewPushModal() {
    openModal('New Price Push', `
      <div class="text-dim text-sm" style="margin-bottom:12px">
        Select a category + new price. We'll create a price_push_id and show you the list of
        branches to deliver to. Each branch's local price_categories.sell_price will be updated
        and the change logged with source='hq'.
      </div>
      <div id="pp-form">Loading categories...</div>`,
      `<button class="btn" data-close>Cancel</button>
       <button class="btn btn-primary" id="pp-create" disabled>
         <span style="display:inline-flex;width:14px;height:14px">${SVG.push}</span>
         Push to All Branches
       </button>`);
    // Load categories
    (async () => {
      try {
        const r = await api('/api/categories');
        const cats = Array.isArray(r) ? r : (r.categories || r.items || []);
        $('#pp-form').innerHTML = `
          <div class="form-group" style="margin-bottom:12px">
            <label class="text-sm"><strong>Category</strong></label>
            <select class="input" id="pp-cat" style="margin-top:4px">
              ${cats.map(c => `<option value="${c.id}" data-price="${c.sell_price}">${esc(c.code || '?')} — ${esc(c.name || '?')} (current: ${fmtRs(c.sell_price)})</option>`).join('')}
            </select>
          </div>
          <div class="form-group" style="margin-bottom:12px">
            <label class="text-sm"><strong>New Sell Price (Rs)</strong></label>
            <input class="input" id="pp-price" type="number" min="1" step="0.01" style="margin-top:4px">
          </div>
          <div class="form-group" style="margin-bottom:0">
            <label class="text-sm"><strong>Notes (optional)</strong></label>
            <input class="input" id="pp-notes" placeholder="e.g. Eid special pricing" style="margin-top:4px">
          </div>`;
        // Pre-fill with current price
        const sel = $('#pp-cat');
        const priceInput = $('#pp-price');
        const updatePrice = () => {
          const opt = sel.options[sel.selectedIndex];
          if (opt) priceInput.value = opt.getAttribute('data-price');
        };
        updatePrice();
        sel.onchange = updatePrice;
        $('#pp-create').disabled = false;
      } catch (e) {
        $('#pp-form').innerHTML = `<div class="text-dim text-sm">Failed to load categories: ${esc(e.message)}</div>`;
      }
    })();
    $('#pp-create').onclick = async () => {
      const catId = parseInt($('#pp-cat')?.value || '0');
      const newPrice = parseFloat($('#pp-price')?.value || '0');
      const notes = $('#pp-notes')?.value.trim() || '';
      if (!catId || newPrice <= 0) {
        toast('Select a category + enter a valid price', 'error'); return;
      }
      try {
        const r = await apiPost('/api/hq/price-push', {
          category_id: catId, new_sell_price: newPrice, notes,
        });
        const targets = r.delivery_targets || [];
        if (targets.length === 0) {
          toast(`Price push ${r.price_push_id} created — no branches with tunnel_url to deliver to`, 'info');
        } else {
          toast(`Price push ${r.price_push_id} created — ${targets.length} branch${targets.length === 1 ? '' : 'es'} to deliver to`, 'success');
        }
        closeModal();
        await loadPushes();
      } catch (e) { toast('Push failed: ' + e.message, 'error'); }
    };
  }
});
