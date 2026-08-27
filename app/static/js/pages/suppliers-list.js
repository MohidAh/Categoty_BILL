// Suppliers list page — renders inside the Billing app shell.
import { route, navigate } from '../router.js';
import { emptyState, errorState } from '../core/states.js';
import { api, apiPost, apiPut, apiDelete } from '../api.js';
import { $, $$, toast, esc, icon, iconHtml, debounce, openModal, closeModal, skeletonCards, errorBox } from '../utils.js';

// Shared SVG icon set
const SVG = {
  plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>',
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>',
  store: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 9l1-5h16l1 5"/><path d="M4 9v11a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1V9"/><line x1="9" y1="13" x2="15" y2="13"/></svg>',
  phone: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z"/></svg>',
  map: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>',
  bills: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
  save: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>',
};

route('/suppliers', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-success">${SVG.store}</div>
      <div>
        <h2 class="pos-page-header-title">Suppliers</h2>
        <p class="pos-page-header-sub">Manage your supplier relationships and contact details.</p>
      </div>
      <div class="pos-page-header-actions">
        <button class="btn" id="s-add-btn">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.plus}</span>
          Add Supplier
        </button>
      </div>
    </div>

    <div class="card mb-4">
      <div class="filter-bar">
        <div class="search-input filter-search">
          ${SVG.search}
          <input class="input" id="s-search" placeholder="Search by name, phone, or address">
        </div>
      </div>
    </div>

    <div id="s-list" class="grid grid-3">${skeletonCards(3)}</div>`;

  $('#s-add-btn').onclick = () => openSupplierModal();

  const debouncedSearch = debounce(() => loadSuppliers($('#s-search').value), 350);
  $('#s-search').oninput = debouncedSearch;

  await loadSuppliers('');

  async function loadSuppliers(q) {
    let list;
    try {
      list = await api(`/api/suppliers?q=${encodeURIComponent(q)}`);
    } catch (e) {
      $('#s-list').innerHTML = `<div style="grid-column:1/-1">${errorBox(e.message, "location.reload()")}</div>`;
      return;
    }
    $('#s-list').innerHTML = list.length ? list.map(s => `
      <div class="card card-hover supplier-card" data-id="${s.id}">
        <div class="flex justify-between items-center">
          <h3 style="margin:0">${esc(s.name)}</h3>
          <div class="row-actions" style="opacity:1">
            <button class="btn btn-ghost btn-sm btn-icon" title="View" data-view="${s.id}">${SVG.bills}</button>
            <button class="btn btn-ghost btn-sm btn-icon" title="Delete" data-delete="${s.id}" data-name="${esc(s.name).replace(/"/g, '&quot;')}">${SVG.trash}</button>
          </div>
        </div>
        <div class="supplier-meta mt-2">
          ${s.phone ? `<div class="supplier-meta-row"><span style="display:inline-flex;width:12px;height:12px;vertical-align:-2px;margin-right:6px;color:var(--text-tertiary)">${SVG.phone}</span>${esc(s.phone)}</div>` : ''}
          ${s.address ? `<div class="supplier-meta-row"><span style="display:inline-flex;width:12px;height:12px;vertical-align:-2px;margin-right:6px;color:var(--text-tertiary)">${SVG.map}</span>${esc(s.address)}</div>` : ''}
        </div>
      </div>`).join('') : `<div class="empty-state" style="grid-column:1/-1">
        <div class="empty-state-icon">${SVG.store}</div>
        <h3>No suppliers yet</h3>
        <p>Add your first supplier to start tracking bills</p>
        <button class="btn" id="s-empty-add">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.plus}</span>
          Add Supplier
        </button>
      </div>`;

    // Wire up card + button clicks (no inline onclick)
    $$('.supplier-card').forEach(card => {
      card.onclick = (e) => {
        // Don't trigger when clicking action buttons
        if (e.target.closest('.row-actions')) return;
        navigate('/suppliers/' + card.dataset.id);
      };
    });
    $$('[data-view]').forEach(b => b.onclick = (e) => {
      e.stopPropagation();
      navigate('/suppliers/' + b.dataset.view);
    });
    $$('[data-delete]').forEach(b => b.onclick = (e) => {
      e.stopPropagation();
      confirmDeleteSupplier(parseInt(b.dataset.delete), b.dataset.name);
    });
    const emptyAdd = $('#s-empty-add');
    if (emptyAdd) emptyAdd.onclick = () => openSupplierModal();
  }

  async function confirmDeleteSupplier(id, name) {
    if (!confirm(`Delete supplier "${name}"? This cannot be undone.`)) return;
    try {
      await apiDelete(`/api/suppliers/${id}`);
      toast('Supplier deleted', 'success');
      loadSuppliers($('#s-search').value);
    } catch (e) {
      toast('Delete failed: ' + e.message, 'error');
    }
  }

  async function openSupplierModal(id = null) {
    let s = { name: '', phone: '', address: '', notes: '' };
    if (id) {
      s = await api(`/api/suppliers/${id}`);
    }
    openModal(id ? 'Edit Supplier' : 'Add Supplier', `
      <div><label>Name</label><input class="input" id="sup-name" value="${esc(s.name)}"></div>
      <div class="mt-3"><label>Phone</label><input class="input" id="sup-phone" value="${esc(s.phone || '')}"></div>
      <div class="mt-3"><label>Address</label><input class="input" id="sup-address" value="${esc(s.address || '')}"></div>
      <div class="mt-3"><label>Notes</label><textarea class="textarea" id="sup-notes" rows="3">${esc(s.notes || '')}</textarea></div>`,
      `<button class="btn btn-secondary" data-modal-close>Cancel</button>
       <button class="btn" id="sup-save-btn">
         <span style="display:inline-flex;width:14px;height:14px">${SVG.save}</span>
         Save
       </button>`);
    $('#sup-save-btn').onclick = () => saveSupplier(id);
  }

  async function saveSupplier(id) {
    const payload = {
      name: $('#sup-name').value,
      phone: $('#sup-phone').value,
      address: $('#sup-address').value,
      notes: $('#sup-notes').value,
    };
    if (!payload.name) { toast('Name is required', 'error'); return; }
    try {
      if (id) await apiPut(`/api/suppliers/${id}`, payload);
      else await apiPost('/api/suppliers', payload);
      closeModal();
      toast('Supplier saved', 'success');
      loadSuppliers($('#s-search').value);
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  }
});
