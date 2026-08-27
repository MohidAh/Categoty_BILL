// Supplier detail page
import { route, navigate } from '../router.js';
import { api, apiPut, apiDelete } from '../api.js';
import { $, toast, esc, fmt, fmtRs, fmtDate, icon, iconHtml, openModal, closeModal } from '../utils.js';

route('/suppliers/', async (el, path) => {
  const id = path.split('/').pop();
  let s, statement;
  try {
    [s, statement] = await Promise.all([
      api(`/api/suppliers/${id}`),
      api(`/api/suppliers/${id}/statement`),
    ]);
  } catch (e) {
    el.innerHTML = `<div class="empty-state"><div class="empty-state-icon">${icon('alert')}</div><h3>Supplier not found</h3><button class="btn" onclick="location.hash='/suppliers'">Back to Suppliers</button></div>`;
    return;
  }
  const r = s.reliability;
  const st = statement.summary;

  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-success">${iconHtml('store')}</div>
      <div>
        <h2 class="pos-page-header-title">${esc(s.name)}</h2>
        <p class="pos-page-header-sub">Supplier profile &amp; history</p>
      </div>
      <div class="pos-page-header-actions">
        ${st.total_outstanding > 0 ? `<button class="btn btn-secondary" onclick="sendWhatsApp()">${icon('phone', 14)} WhatsApp Reminder</button>` : ''}
        <button class="btn btn-secondary" onclick="openEditModal()">${icon('edit', 14)} Edit</button>
        <button class="btn btn-danger" onclick="deleteSupplier()">${icon('trash', 14)}</button>
      </div>
    </div>

    <div class="grid grid-4 mb-4">
      <div class="kpi kpi-accent">
        <div class="kpi-label">${icon('brain', 12)} Reliability Score</div>
        <div class="kpi-value">${r.score}<span class="text-dim text-sm">/100</span></div>
        <div class="kpi-sub">Freq ${r.components?.frequency || 0} · Pay ${r.components?.payment || 0} · Price ${r.components?.price_stability || 0}</div>
      </div>
      <div class="kpi kpi-success">
        <div class="kpi-label">${icon('wallet', 12)} Total Spent</div>
        <div class="kpi-value">${fmtRs(r.total_spent)}</div>
        <div class="kpi-sub">All confirmed bills</div>
      </div>
      <div class="kpi ${r.outstanding > 0 ? 'kpi-danger' : ''}">
        <div class="kpi-label">${icon('alert', 12)} Outstanding</div>
        <div class="kpi-value">${fmtRs(r.outstanding)}</div>
        <div class="kpi-sub">Unpaid credit</div>
      </div>
      <div class="kpi">
        <div class="kpi-label">${icon('bills', 12)} Bill Count</div>
        <div class="kpi-value">${r.bill_count}</div>
        <div class="kpi-sub">Total transactions</div>
      </div>
    </div>

    <div class="grid grid-2 mb-4">
      <div class="card">
        <h3>Contact Information</h3>
        <div class="stat-list mt-4">
          <div class="stat-row">
            <span class="stat-label flex items-center gap-2">${icon('phone', 14)} Phone</span>
            <span class="stat-value">${esc(s.phone || '—')}</span>
          </div>
          <div class="stat-row">
            <span class="stat-label flex items-center gap-2">${icon('map', 14)} Address</span>
            <span class="stat-value">${esc(s.address || '—')}</span>
          </div>
          ${s.notes ? `<div class="stat-row"><span class="stat-label">Notes</span><span class="stat-value">${esc(s.notes)}</span></div>` : ''}
        </div>
      </div>
      <div class="card">
        <h3>Reliability Breakdown</h3>
        <div class="stat-list mt-4">
          <div class="stat-row">
            <span class="stat-label">Bill Frequency (40 pts)</span>
            <span class="stat-value">${r.components?.frequency || 0}/40</span>
          </div>
          <div class="stat-row">
            <span class="stat-label">Payment Consistency (30 pts)</span>
            <span class="stat-value">${r.components?.payment || 0}/30</span>
          </div>
          <div class="stat-row">
            <span class="stat-label">Price Stability (30 pts)</span>
            <span class="stat-value">${r.components?.price_stability || 0}/30</span>
          </div>
          <div class="stat-row" style="border-top:2px solid var(--border);padding-top:8px">
            <span class="stat-label font-bold">Total Score</span>
            <span class="stat-value text-accent font-bold">${r.score}/100</span>
          </div>
        </div>
      </div>
    </div>

    <div class="card mb-4">
      <div class="card-title">
        <h3>Statement (Running Balance)</h3>
        ${st.total_outstanding > 0 ? `<span class="badge badge-danger">Outstanding: ${fmtRs(st.total_outstanding)}</span>` : `<span class="badge badge-success">Settled</span>`}
      </div>
      <div class="grid grid-4 mb-4">
        <div class="kpi"><div class="kpi-label">Total Purchased</div><div class="kpi-value">${fmtRs(st.total_purchased)}</div></div>
        <div class="kpi kpi-success"><div class="kpi-label">Total Paid</div><div class="kpi-value">${fmtRs(st.total_paid)}</div></div>
        <div class="kpi ${st.total_outstanding > 0 ? 'kpi-danger' : ''}"><div class="kpi-label">Outstanding</div><div class="kpi-value">${fmtRs(st.total_outstanding)}</div></div>
        <div class="kpi"><div class="kpi-label">Bills</div><div class="kpi-value">${st.total_bills}</div></div>
      </div>
      ${statement.statement.length ? `
        <div class="table-wrap">
          <table>
            <thead><tr><th>Date</th><th>Description</th><th class="table-num">Debit (Credit)</th><th class="table-num">Paid</th><th class="table-num">Balance</th><th>Status</th></tr></thead>
            <tbody>
              ${statement.statement.map(e => `<tr ${e.bill_id ? `style="cursor:pointer" onclick="location.hash='/bills/${e.bill_id}'"` : ''} ${e.is_overdue ? 'style="background:var(--danger-soft)"' : ''}>
                <td class="text-sm">${fmtDate(e.date)}</td>
                <td>${esc(e.description)}</td>
                <td class="table-num">${e.debit > 0 ? fmtRs(e.debit) : '—'}</td>
                <td class="table-num text-success">${e.credit > 0 ? fmtRs(e.credit) : '—'}</td>
                <td class="table-num font-semibold">${fmtRs(e.balance)}</td>
                <td>${e.is_overdue ? '<span class="badge badge-danger">overdue</span>' : e.payment_status === 'credit' ? '<span class="badge badge-warning">credit</span>' : '<span class="badge badge-success">paid</span>'}</td>
              </tr>`).join('')}
            </tbody>
          </table>
        </div>` : '<p class="text-dim">No confirmed bills yet.</p>'}
    </div>

    <div class="card">
      <div class="card-title">
        <h3>Bill History (${s.bills.length})</h3>
      </div>
      ${s.bills.length ? `
        <div class="table-wrap">
          <table class="table-clickable">
            <thead><tr><th>ID</th><th>Date</th><th class="table-num">Total</th><th>Payment</th><th>Status</th></tr></thead>
            <tbody>
              ${s.bills.map(b => `<tr onclick="location.hash='/bills/${b.id}'">
                <td class="text-dim">#${b.id}</td>
                <td>${fmtDate(b.bill_date)}</td>
                <td class="table-num">${fmtRs(b.written_total || b.computed_total)}</td>
                <td><span class="badge ${b.payment_status === 'credit' ? 'badge-danger' : ''}">${b.payment_status}</span></td>
                <td><span class="badge ${b.status === 'confirmed' ? 'badge-success' : 'badge-warning'} badge-dot">${b.status}</span></td>
              </tr>`).join('')}
            </tbody>
          </table>
        </div>` : `<div class="empty-state"><div class="empty-state-icon">${icon('inbox')}</div><h3>No bills yet</h3><p>This supplier has no bills recorded</p></div>`}
    </div>`;

  window.openEditModal = () => {
    openModal('Edit Supplier', `
      <div><label>Name</label><input class="input" id="sup-name" value="${esc(s.name)}"></div>
      <div class="mt-3"><label>Phone</label><input class="input" id="sup-phone" value="${esc(s.phone || '')}"></div>
      <div class="mt-3"><label>Address</label><input class="input" id="sup-address" value="${esc(s.address || '')}"></div>
      <div class="mt-3"><label>Notes</label><textarea class="textarea" id="sup-notes" rows="3">${esc(s.notes || '')}</textarea></div>`,
      `<button class="btn btn-secondary" data-modal-close>Cancel</button>
       <button class="btn" onclick="saveEdit()">${icon('save', 14)} Save</button>`);
  };
  window.saveEdit = async () => {
    const payload = {
      name: $('#sup-name').value, phone: $('#sup-phone').value,
      address: $('#sup-address').value, notes: $('#sup-notes').value,
    };
    try {
      await apiPut(`/api/suppliers/${id}`, payload);
      closeModal();
      toast('Saved', 'success');
      navigate('/suppliers/' + id);
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  };
  window.sendWhatsApp = async () => {
    try {
      const r = await api(`/api/suppliers/${id}/whatsapp`);
      if (r.url) {
        window.open(r.url, '_blank');
        toast(`Opening WhatsApp for ${fmtRs(r.total)} outstanding`, 'success');
      } else {
        // No phone — show the message so user can copy it
        openModal('WhatsApp Reminder', `<p>No phone number on file. Copy this message:</p>
          <textarea class="textarea" rows="8" readonly style="font-family:var(--font-mono);font-size:11px">${esc(r.message)}</textarea>`,
          `<button class="btn btn-secondary" data-modal-close>Close</button>
           <button class="btn" onclick="navigator.clipboard.writeText(document.querySelector('.textarea').value); toast('Copied!', 'success')">Copy</button>`);
      }
    } catch (e) {
      toast('Error: ' + e.message, 'error');
    }
  };

  window.deleteSupplier = () => {
    openModal('Delete Supplier', `<p>Delete <strong>${esc(s.name)}</strong>? This cannot be undone.</p>`,
      `<button class="btn btn-secondary" data-modal-close>Cancel</button>
       <button class="btn btn-danger" onclick="confirmDelete()">${icon('trash', 14)} Delete</button>`);
  };
  window.confirmDelete = async () => {
    closeModal();
    try {
      await apiDelete(`/api/suppliers/${id}`);
      toast('Supplier deleted', 'success');
      navigate('/suppliers');
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  };
});
