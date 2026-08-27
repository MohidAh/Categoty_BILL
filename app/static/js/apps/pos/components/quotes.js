// POS Quotes — extracted from pos.js Phase 5
import { route, navigate } from '../../../router.js';
import { api, apiPost, apiDelete } from '../../../api.js';
import { $, esc, fmt, fmtRs, fmtDate, icon, toast, openModal, closeModal, skeletonCards, errorBox } from '../../../utils.js';
import { renderKioskBar, initKioskBar } from '../../../components/kiosk-bar.js';

route('/pos/quotes', async (el) => {
  el.innerHTML = `
    ${renderKioskBar('/pos/quotes')}
    <div class="kiosk-content">
      <div class="topbar">
        <div class="topbar-title">
          <button class="btn btn-ghost btn-icon" onclick="location.hash='/pos'">${icon('arrowLeft', 16)}</button>
          <div><h1>Quotations</h1></div>
        </div>
        <div class="topbar-actions">
          <select class="select select-sm" id="q-filter">
            <option value="">All</option>
            <option value="open">Open</option>
            <option value="converted">Converted</option>
          </select>
        </div>
      </div>
      <div class="card"><div id="quotes-list">${skeletonCards(3)}</div></div>
    </div>`;
  initKioskBar();
  async function loadQuotes(status = '') {
    try {
      const r = await api(`/api/quotations${status ? '?status=' + status : ''}`);
      const list = r.quotations || [];
      $('#quotes-list').innerHTML = list.length ? `
        <div class="table-wrap"><table class="table-clickable">
          <thead><tr><th>Quote #</th><th>Date</th><th>Customer</th><th>Valid Until</th><th class="table-num">Total</th><th>Status</th><th></th></tr></thead>
          <tbody>${list.map(q => `<tr>
            <td class="font-mono text-sm">${esc(q.quote_no)}</td>
            <td class="text-sm">${fmtDate(q.created_at)}</td>
            <td>${esc(q.customer_name || 'Walk-in')}</td>
            <td class="text-sm">${esc(q.valid_until || '—')}</td>
            <td class="table-num font-semibold">${fmtRs(q.total)}</td>
            <td><span class="badge ${q.status==='open'?'badge-success':q.status==='converted'?'badge-accent':'badge-warning'}">${q.status}</span></td>
            <td>
              <button class="btn btn-ghost btn-sm btn-icon" onclick="window.open('/api/quotations/${q.id}/receipt','_blank')" title="Print"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:14px;height:14px"><polyline points="6 9 6 2 18 2 18 9"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><rect x="6" y="14" width="12" height="8"/></svg></button>
              ${q.status === 'open' ? `<button class="btn btn-sm" onclick="convertQuote(${q.id})">Convert</button>` : ''}
              <button class="btn btn-ghost btn-sm btn-icon" onclick="deleteQuote(${q.id})" title="Delete">${icon('trash', 12)}</button>
            </td>
          </tr>`).join('')}</tbody>
        </table></div>` : '<div class="empty-state"><h3>No quotations yet</h3><p class="text-dim">Save a cart as a quote from the POS screen (F12).</p><button class="btn mt-2" onclick="location.hash=\'/pos\'">Go to POS</button></div>';
    } catch (e) {
      $('#quotes-list').innerHTML = errorBox(e.message, "location.reload()");
    }
  }
  $('#q-filter').onchange = (e) => loadQuotes(e.target.value);
  window.convertQuote = async (qid) => {
    try {
      const q = await api(`/api/quotations/${qid}`);
      // Load quote items into cart and navigate to POS
      // We'll stash the items in localStorage so the POS page can pick them up
      localStorage.setItem('pos-pending-quote', JSON.stringify(q));
      navigate('/pos');
      setTimeout(() => {
        // Trigger recall by dispatching a custom event
        window.dispatchEvent(new CustomEvent('pos:load-quote', { detail: q }));
      }, 400);
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  };
  window.deleteQuote = async (qid) => {
    if (!confirm('Delete this quotation?')) return;
    try { await apiDelete(`/api/quotations/${qid}`); toast('Deleted', 'success'); loadQuotes($('#q-filter').value); }
    catch (e) { toast('Error', 'error'); }
  };
  loadQuotes();
});
