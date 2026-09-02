// v8.18.13 — Staff Salary page (Billing app)
// Manage staff salaries: fixed monthly salary per employee, 4 paid off-days
// per month (fewer taken = extra working days paid at salary/30), salary
// advances during the month, and the auto-computed final payable.
// Saving a record posts an OPERATING expense under 'Salaries' automatically,
// so payroll is deducted from Gross Profit in Actual Earnings / P&L.
import { route } from '../router.js';
import { api, apiPost, apiPut, apiDelete } from '../api.js';
import { $, $$, esc, fmtRs, fmtDate, toast, openModal, closeModal,
         skeletonCards, errorBox, btnBusy, btnOk } from '../utils.js';
import { initListState } from '../list-state.js';

const SVG = {
  users: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
  wallet: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12V7H5a2 2 0 0 1 0-4h14v4"/><path d="M3 5v14a2 2 0 0 0 2 2h16v-5"/><path d="M18 12a2 2 0 0 0 0 4h4v-4z"/></svg>',
  calendar: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>',
  plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
  edit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>',
  history: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>',
  clock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>',
  arrowDown: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><polyline points="19 12 12 19 5 12"/></svg>',
};

function statCard(label, value, chipClass, svgIcon, sub = '') {
  return `<div class="stat-card">
    <div class="stat-card-icon ${chipClass}">${svgIcon}</div>
    <div class="stat-card-label">${esc(label)}</div>
    <div class="stat-card-value">${value}</div>
    ${sub ? `<div class="stat-card-delta">${sub}</div>` : ''}
  </div>`;
}

// per-day rate = monthly salary / 30 — the client's formula (mirrors app/salary.py)
function comp_perDay(monthlySalary) {
  const s = parseFloat(monthlySalary) || 0;
  return s > 0 ? s / 30 : 0;
}

route('/bills/salary', async (el, path, q) => {
  const st = initListState('salary', q, { month: '' });
  st.syncUrlIfRestored();
  const thisMonth = st.val('month') || new Date().toISOString().slice(0, 7);
  st.replace({ month: thisMonth });
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-secondary">${SVG.users}</div>
      <div>
        <h2 class="pos-page-header-title">Staff Salary</h2>
        <p class="pos-page-header-sub">Monthly payroll: 4 paid off-days allowed — take fewer and the rest count as extra working days (salary ÷ 30 per day). Advances are deducted from the final payable.</p>
      </div>
      <div class="pos-page-header-actions">
        <div class="pos-date-input">
          <span class="pos-date-input-icon">${SVG.calendar}</span>
          <input class="input input-sm" id="sal-month" type="month" value="${thisMonth}">
        </div>
        <button class="btn btn-secondary btn-sm" id="sal-advance-btn" title="Record a salary advance">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.arrowDown}</span>
          Advance
        </button>
        <button class="btn btn-primary btn-sm" id="sal-add-staff-btn">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.plus}</span>
          Add Staff
        </button>
      </div>
    </div>

    <div id="sal-stats">${skeletonCards(4)}</div>

    <div class="card mt-4">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
        <h3>Payroll — <span id="sal-month-label">${thisMonth}</span></h3>
        <span class="text-dim text-sm" id="sal-hint">Set off-days taken, then Save — salary is posted to Operating Expenses automatically.</span>
      </div>
      <div id="sal-table" class="mt-3">${skeletonCards(2)}</div>
    </div>

    <div class="card mt-4">
      <h3>Advances This Month</h3>
      <div id="sal-advances" class="mt-3">${skeletonCards(2)}</div>
    </div>`;

  $('#sal-month').onchange = () => { st.replace({ month: $('#sal-month').value }); loadAll(); };
  $('#sal-add-staff-btn').onclick = () => openAddStaffModal();
  $('#sal-advance-btn').onclick = () => openAdvanceModal();

  let monthData = null;
  // unsaved off-days typed by the user — survives table re-renders
  // (e.g. recording an advance mid-edit must not wipe the input)
  const unsavedOff = {};

  await loadAll();

  async function loadAll() {
    $('#sal-month-label').textContent = $('#sal-month').value;
    await Promise.all([loadMonth(), loadAdvances()]);
  }

  async function loadMonth() {
    const month = $('#sal-month').value;
    try {
      monthData = await api(`/api/salary/month?month=${month}`);
      const t = monthData.totals;
      $('#sal-stats').innerHTML = `
        <div class="grid grid-4">
          ${statCard('Payroll Cost', fmtRs(t.payroll_cost), 'chip-warning', SVG.wallet,
            `${t.with_salary} of ${t.employees} staff with salary`)}
          ${statCard('Advances Given', fmtRs(t.advances_total), 'chip-info', SVG.arrowDown, 'deducted from payable')}
          ${statCard('Final Payable', fmtRs(t.final_payable_total), 'chip-danger', SVG.wallet, 'salary + extra − advances')}
          ${statCard('Paid So Far', fmtRs(t.paid_total), 'chip-success', SVG.check, 'this month')}
        </div>`;
      renderTable();
    } catch (e) {
      $('#sal-stats').innerHTML = errorBox(e.message);
      $('#sal-table').innerHTML = errorBox(e.message);
    }
  }

  function renderTable() {
    const emps = (monthData && monthData.employees) || [];
    const allowed = monthData ? monthData.allowed_off_days_default : 4;
    if (emps.length === 0) {
      $('#sal-table').innerHTML = `
        <div class="text-center text-dim" style="padding:32px">
          <p style="font-weight:600;margin-bottom:4px">No staff added yet</p>
          <p class="text-sm">Click "Add Staff" with a fixed monthly salary to start managing payroll.</p>
        </div>`;
      return;
    }
    $('#sal-table').innerHTML = `
      <div style="overflow-x:auto">
      <table class="table">
        <thead>
          <tr>
            <th>Employee</th>
            <th style="text-align:right">Monthly Salary</th>
            <th style="text-align:center">Off Days Taken</th>
            <th style="text-align:center">Extra Days</th>
            <th style="text-align:right">Extra Pay</th>
            <th style="text-align:right">Advances</th>
            <th style="text-align:right">Final Payable</th>
            <th>Status</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          ${emps.map(e => {
            const rec = e.record;
            const salary = e.monthly_salary;
            // off-days shown = unsaved input if the user typed one, else the record's
            const offShown = unsavedOff[e.id] !== undefined
              ? Math.max(0, parseInt(unsavedOff[e.id]) || 0)
              : (rec ? rec.off_days_taken : 0);
            // live preview math from the SHOWN off-days (matches the backend formula)
            const extraShown = Math.max(0, allowed - offShown);
            const extraPayShown = Math.round(extraShown * comp_perDay(salary) * 100) / 100;
            const finalShown = Math.round((salary + extraPayShown - e.advances_total) * 100) / 100;
            const comp = e.computed;
            const needsSave = comp.needs_save || (rec && rec.off_days_taken !== offShown) || (!rec && offShown > 0);
            return `<tr data-sal-row="${e.id}">
              <td>
                <div class="font-semibold">${esc(e.name)}</div>
                <div class="text-sm text-dim">${esc(e.role)}${e.phone ? ' · ' + esc(e.phone) : ''}</div>
              </td>
              <td style="text-align:right">
                ${salary > 0 ? fmtRs(salary) : `<button class="btn btn-secondary btn-sm" data-sal-set-salary="${e.id}">Set Salary</button>`}
              </td>
              <td style="text-align:center">
                <input class="input input-sm" type="number" min="0" max="31" step="1"
                       data-sal-offdays="${e.id}" value="${offShown}"
                       style="width:70px;text-align:center"
                       ${rec && rec.status === 'paid' ? 'disabled title="Record already paid — delete it to change"' : ''}>
                <div class="text-xs text-dim" style="margin-top:2px">of ${allowed} allowed</div>
              </td>
              <td style="text-align:center">${extraShown} <span class="text-xs text-dim">× ${fmtRs(comp_perDay(salary))}</span></td>
              <td style="text-align:right" class="text-success">${fmtRs(extraPayShown)}</td>
              <td style="text-align:right">
                <div>${fmtRs(e.advances_total)}</div>
                ${e.advances.length ? `<button class="btn btn-ghost btn-sm" data-sal-adv-list="${e.id}" style="padding:2px 6px;font-size:12px">${e.advances.length} entries</button>` : ''}
              </td>
              <td style="text-align:right;font-weight:700">${fmtRs(finalShown)}</td>
              <td>${rec
                ? (rec.status === 'paid'
                    ? `<span class="chip chip-success chip-sm">Paid</span><div class="text-xs text-dim">${esc(fmtDate(rec.paid_date))}</div>`
                    : `<span class="chip chip-warning chip-sm">Draft</span>${needsSave ? '<div class="text-xs" style="color:var(--warning-text)">needs save</div>' : ''}`)
                : (offShown > 0
                    ? '<span class="chip chip-secondary chip-sm">Not saved</span><div class="text-xs" style="color:var(--warning-text)">needs save</div>'
                    : '<span class="chip chip-secondary chip-sm">Not saved</span>')}</td>
              <td>
                <div class="flex gap-1" style="flex-wrap:wrap">
                  ${(!rec || rec.status !== 'paid') ? `
                    <button class="btn btn-secondary btn-sm" data-sal-save="${e.id}" title="Save off-days (posts Salaries expense)">Save</button>
                    ${finalShown > 0 ? `<button class="btn btn-primary btn-sm" data-sal-pay="${e.id}" title="Mark paid">Pay</button>` : ''}
                  ` : `
                    <button class="btn btn-secondary btn-sm" data-sal-history="${e.id}" title="Salary history">${SVG.history}</button>
                    <button class="btn-icon btn-icon-danger" data-sal-del-rec="${rec.id}" title="Delete record (removes expense + cash)">
                      <span style="display:inline-flex;width:14px;height:14px">${SVG.trash}</span>
                    </button>
                  `}
                  ${(!rec || rec.status !== 'paid') ? `
                    <button class="btn-icon" data-sal-history="${e.id}" title="Salary history">
                      <span style="display:inline-flex;width:14px;height:14px">${SVG.history}</span>
                    </button>
                  ` : ''}
                </div>
              </td>
            </tr>`;
          }).join('')}
        </tbody>
      </table>
      </div>`;

    // wire row actions
    $$('[data-sal-set-salary]').forEach(b => b.onclick = () => openSetSalaryModal(parseInt(b.dataset.salSetSalary)));
    $$('[data-sal-save]').forEach(b => b.onclick = () => saveRecord(parseInt(b.dataset.salSave)));
    $$('[data-sal-pay]').forEach(b => b.onclick = () => openPayModal(parseInt(b.dataset.salPay)));
    $$('[data-sal-history]').forEach(b => b.onclick = () => openHistoryModal(parseInt(b.dataset.salHistory)));
    $$('[data-sal-adv-list]').forEach(b => b.onclick = () => openAdvanceListModal(parseInt(b.dataset.salAdvList)));
    $$('[data-sal-del-rec]').forEach(b => b.onclick = async () => {
      if (!confirm('Delete this salary record? The linked Salaries expense and cash entries are removed too.')) return;
      try {
        await apiDelete(`/api/salary/records/${b.dataset.salDelRec}`);
        toast('Salary record deleted', 'success');
        await loadAll();
      } catch (e) { toast('Delete failed: ' + e.message, 'error'); }
    });
    // live preview when off-days input changes (remember unsaved value)
    $$('[data-sal-offdays]').forEach(inp => {
      inp.oninput = () => {
        const id = parseInt(inp.dataset.salOffdays);
        unsavedOff[id] = inp.value;
        const row = monthData.employees.find(x => x.id === id);
        if (!row) return;
        const salary = row.monthly_salary;
        const allowed = monthData.allowed_off_days_default;
        const taken = Math.max(0, parseInt(inp.value) || 0);
        const extraDays = Math.max(0, allowed - taken);
        const perDay = comp_perDay(salary);
        const tr = inp.closest('tr');
        if (!tr) return;
        const cells = tr.querySelectorAll('td');
        // cells: 0 emp, 1 salary, 2 offdays, 3 extra days, 4 extra pay, 5 advances, 6 final, 7 status, 8 actions
        cells[3].innerHTML = `${extraDays} <span class="text-xs text-dim">× ${fmtRs(perDay)}</span>`;
        cells[4].textContent = fmtRs(Math.round(extraDays * perDay * 100) / 100);
        cells[6].textContent = fmtRs(Math.round((salary + extraDays * perDay - row.advances_total) * 100) / 100);
      };
    });
  }

  async function loadAdvances() {
    const month = $('#sal-month').value;
    try {
      const r = await api(`/api/salary/advances?month=${month}&limit=50`);
      const rows = r.advances || [];
      if (rows.length === 0) {
        $('#sal-advances').innerHTML = `
          <p class="text-dim text-sm text-center" style="padding:16px">
            No advances recorded for ${esc(month)}. Use the "Advance" button when staff take part of their salary early — it's deducted from their final payable.
          </p>`;
        return;
      }
      $('#sal-advances').innerHTML = `
        <div style="overflow-x:auto">
        <table class="table">
          <thead><tr><th>Date</th><th>Employee</th><th>Note</th><th style="text-align:right">Amount</th><th></th></tr></thead>
          <tbody>${rows.map(a => `
            <tr>
              <td class="text-sm">${esc(fmtDate(a.date))}</td>
              <td class="font-semibold">${esc(a.employee_name || '#' + a.employee_id)}</td>
              <td class="text-sm text-dim">${esc(a.description || '')}</td>
              <td style="text-align:right;font-weight:600" class="text-danger">${fmtRs(a.amount)}</td>
              <td><button class="btn-icon btn-icon-danger" data-sal-adv-del="${a.id}" title="Delete advance">
                <span style="display:inline-flex;width:14px;height:14px">${SVG.trash}</span>
              </button></td>
            </tr>`).join('')}</tbody>
        </table>
        </div>`;
      $$('[data-sal-adv-del]').forEach(b => b.onclick = async () => {
        if (!confirm('Delete this advance? Its cash drawer entry is removed and the salary record is recalculated.')) return;
        try {
          await apiDelete(`/api/salary/advances/${b.dataset.salAdvDel}`);
          toast('Advance deleted', 'success');
          await loadAll();
        } catch (e) { toast('Delete failed: ' + e.message, 'error'); }
      });
    } catch (e) {
      $('#sal-advances').innerHTML = errorBox(e.message);
    }
  }

  // ── Modals ──────────────────────────────────────────────────

  async function saveRecord(employeeId) {
    const inp = $(`[data-sal-offdays="${employeeId}"]`);
    const off = inp ? Math.max(0, parseInt(inp.value) || 0) : 0;
    const month = $('#sal-month').value;
    try {
      const rec = await apiPost('/api/salary/records', {
        employee_id: employeeId, month, off_days_taken: off,
      });
      delete unsavedOff[employeeId];  // saved — stop preserving the input
      toast(`Saved — final payable ${fmtRs(rec.final_payable)} (expense posted under Salaries)`, 'success');
      await loadAll();
    } catch (e) {
      toast('Save failed: ' + e.message, 'error');
    }
  }

  function openAddStaffModal() {
    openModal(
      'Add Staff (with salary)',
      `
      <div class="form-group">
        <label class="form-label">Name</label>
        <input class="input" id="st-name" placeholder="Full name" autofocus>
      </div>
      <div class="grid grid-2">
        <div class="form-group">
          <label class="form-label">Phone (optional)</label>
          <input class="input" id="st-phone" placeholder="03001234567">
        </div>
        <div class="form-group">
          <label class="form-label">Role</label>
          <select class="input" id="st-role">
            <option value="cashier">Cashier</option>
            <option value="manager">Manager</option>
            <option value="admin">Admin</option>
          </select>
        </div>
      </div>
      <div class="form-group">
        <label class="form-label">Fixed Monthly Salary (Rs)</label>
        <input class="input" id="st-salary" type="number" min="0" step="any" placeholder="0">
      </div>
      <p class="text-dim text-sm" style="margin-top:8px">
        Staff appear on this page and in Settings → Employees (where you can also set a POS login PIN).
      </p>
      `,
      `<button class="btn" data-close>Cancel</button>
       <button class="btn btn-primary" id="st-save-btn"><span style="display:inline-flex;width:14px;height:14px">${SVG.check}</span> Add Staff</button>`,
    );
    $('#st-save-btn').onclick = async () => {
      const name = $('#st-name').value.trim();
      const salary = parseFloat($('#st-salary').value);
      if (!name) { toast('Name is required', 'error'); return; }
      if (isNaN(salary) || salary < 0) { toast('Enter a valid salary', 'error'); return; }
      try {
        await apiPost('/api/salary/employees', {
          name, phone: $('#st-phone').value.trim(), role: $('#st-role').value,
          monthly_salary: salary,
        });
        toast('Staff added', 'success');
        closeModal();
        await loadAll();
      } catch (e) { toast('Save failed: ' + e.message, 'error'); }
    };
  }

  function openSetSalaryModal(employeeId) {
    const emp = monthData.employees.find(x => x.id === employeeId);
    if (!emp) return;
    openModal(
      'Set Monthly Salary',
      `
      <div class="form-group">
        <label class="form-label">${esc(emp.name)} — Fixed Monthly Salary (Rs)</label>
        <input class="input" id="ss-salary" type="number" min="0" step="any" value="${emp.monthly_salary || 0}" autofocus>
      </div>
      <p class="text-dim text-sm">Draft salary records for this employee are recalculated immediately. Paid records keep their saved amounts.</p>
      `,
      `<button class="btn" data-close>Cancel</button>
       <button class="btn btn-primary" id="ss-save-btn"><span style="display:inline-flex;width:14px;height:14px">${SVG.check}</span> Save Salary</button>`,
    );
    $('#ss-save-btn').onclick = async () => {
      const salary = parseFloat($('#ss-salary').value);
      if (isNaN(salary) || salary < 0) { toast('Enter a valid salary', 'error'); return; }
      try {
        await apiPut(`/api/salary/employees/${employeeId}`, { monthly_salary: salary });
        toast('Salary updated', 'success');
        closeModal();
        await loadAll();
      } catch (e) { toast('Save failed: ' + e.message, 'error'); }
    };
  }

  function openAdvanceModal(employeeId = null) {
    const emps = (monthData && monthData.employees) || [];
    const today = new Date().toISOString().slice(0, 10);
    if (emps.length === 0) { toast('Add staff first', 'error'); return; }
    openModal(
      'Record Salary Advance',
      `
      <p class="text-dim text-sm" style="margin-bottom:12px">
        Cash given to staff before the month-end payout. It leaves the drawer now and is deducted from their final payable.
      </p>
      <div class="form-group">
        <label class="form-label">Employee</label>
        <select class="input" id="adv-emp">
          ${emps.map(e => `<option value="${e.id}" ${employeeId === e.id ? 'selected' : ''}>${esc(e.name)}</option>`).join('')}
        </select>
      </div>
      <div class="grid grid-2">
        <div class="form-group">
          <label class="form-label">Amount (Rs)</label>
          <input class="input" id="adv-amount" type="number" min="1" step="any" placeholder="0" autofocus>
        </div>
        <div class="form-group">
          <label class="form-label">Date</label>
          <input class="input" id="adv-date" type="date" value="${today}">
        </div>
      </div>
      <div class="form-group">
        <label class="form-label">Note (optional)</label>
        <input class="input" id="adv-desc" placeholder="e.g. Eid advance, medical...">
      </div>
      `,
      `<button class="btn" data-close>Cancel</button>
       <button class="btn btn-primary" id="adv-save-btn"><span style="display:inline-flex;width:14px;height:14px">${SVG.check}</span> Save Advance</button>`,
    );
    $('#adv-save-btn').onclick = async () => {
      const amount = parseFloat($('#adv-amount').value);
      if (!amount || amount <= 0) { toast('Enter a valid amount', 'error'); return; }
      try {
        await apiPost('/api/salary/advances', {
          employee_id: parseInt($('#adv-emp').value, 10),
          amount, date: $('#adv-date').value,
          description: $('#adv-desc').value.trim(),
        });
        toast('Advance recorded — deducted from final payable', 'success');
        closeModal();
        await loadAll();
      } catch (e) { toast('Save failed: ' + e.message, 'error'); }
    };
  }

  function openAdvanceListModal(employeeId) {
    const emp = monthData.employees.find(x => x.id === employeeId);
    if (!emp) return;
    const rows = emp.advances || [];
    openModal(
      `Advances — ${emp.name} (${monthData.month})`,
      rows.length === 0
        ? '<p class="text-dim text-sm text-center" style="padding:16px">No advances this month.</p>'
        : `<div style="overflow-x:auto">
           <table class="table">
             <thead><tr><th>Date</th><th>Note</th><th style="text-align:right">Amount</th></tr></thead>
             <tbody>${rows.map(a => `
               <tr>
                 <td class="text-sm">${esc(fmtDate(a.date))}</td>
                 <td class="text-sm text-dim">${esc(a.description || '')}</td>
                 <td style="text-align:right;font-weight:600" class="text-danger">${fmtRs(a.amount)}</td>
               </tr>`).join('')}</tbody>
           </table>
           <div class="text-right mt-2" style="font-weight:700">Total: <span class="text-danger">${fmtRs(emp.advances_total)}</span></div>
           </div>`,
      `<button class="btn" data-close>Close</button>`,
    );
  }

  async function openPayModal(employeeId) {
    const emp = monthData.employees.find(x => x.id === employeeId);
    if (!emp) return;
    const inp = $(`[data-sal-offdays="${employeeId}"]`);
    const off = inp ? Math.max(0, parseInt(inp.value) || 0) : 0;
    // Live math preview from the current off-days input
    const salary = emp.monthly_salary;
    const extraDays = Math.max(0, monthData.allowed_off_days_default - off);
    const extraPay = Math.round(extraDays * comp_perDay(salary) * 100) / 100;
    const payable = Math.round((salary + extraPay - emp.advances_total) * 100) / 100;
    const rec = emp.record;
    const finalAmount = payable;
    const today = new Date().toISOString().slice(0, 10);
    openModal(
      `Pay Salary — ${emp.name}`,
      `
      <div class="stat-list">
        <div class="stat-row"><span>Monthly Salary</span><span>${fmtRs(salary)}</span></div>
        <div class="stat-row"><span>Extra working days (${extraDays} × ${fmtRs(salary / 30)})</span><span class="text-success">+ ${fmtRs(extraPay)}</span></div>
        <div class="stat-row"><span>Advances taken this month</span><span class="text-danger">− ${fmtRs(emp.advances_total)}</span></div>
        <div class="stat-row" style="border-top:2px solid var(--border)"><span class="font-bold">Final Payable</span><span class="font-bold">${fmtRs(finalAmount)}</span></div>
      </div>
      ${off !== (rec ? rec.off_days_taken : 0) ? `
        <div class="alert alert-warning text-sm mt-2" style="padding:8px 12px">
          Off-days changed but not saved — paying will save them first automatically.
        </div>` : ''}
      <div class="grid grid-2 mt-3">
        <div class="form-group">
          <label class="form-label">Payment Method</label>
          <select class="input" id="pay-method">
            <option value="cash">Cash</option>
            <option value="bank">Bank</option>
            <option value="card">Card</option>
            <option value="online">Online</option>
          </select>
        </div>
        <div class="form-group">
          <label class="form-label">Date</label>
          <input class="input" id="pay-date" type="date" value="${today}">
        </div>
      </div>
      <p class="text-dim text-sm" style="margin-top:8px">
        The Salaries expense is already in your P&L — paying just moves this amount out of the drawer (if cash) and marks the record paid.
      </p>
      `,
      `<button class="btn" data-close>Cancel</button>
       <button class="btn btn-primary" id="pay-save-btn"><span style="display:inline-flex;width:14px;height:14px">${SVG.check}</span> Mark Paid (${fmtRs(finalAmount)})</button>`,
    );
    $('#pay-save-btn').onclick = async () => {
      const btn = $('#pay-save-btn');
      if (!btnBusy(btn, 'Paying…')) return;
      try {
        // 1. save the off-days (creates the record if missing)
        const rec = await apiPost('/api/salary/records', {
          employee_id: employeeId, month: $('#sal-month').value, off_days_taken: off,
        });
        // 2. mark paid
        await apiPost(`/api/salary/records/${rec.id}/pay`, {
          payment_method: $('#pay-method').value, date: $('#pay-date').value,
        });
        toast(`Salary paid — ${fmtRs(finalAmount)} to ${emp.name}`, 'success');
        closeModal();
        await loadAll();
      } catch (e) {
        toast('Pay failed: ' + e.message, 'error');
        btnOk(btn);
      }
    };
  }

  async function openHistoryModal(employeeId) {
    const emp = monthData.employees.find(x => x.id === employeeId);
    if (!emp) return;
    try {
      const r = await api(`/api/salary/history/${employeeId}`);
      const hist = r.history || [];
      openModal(
        `Salary History — ${emp.name}`,
        hist.length === 0
          ? '<p class="text-dim text-sm text-center" style="padding:16px">No salary records yet. Save a record from the payroll table first.</p>'
          : `<div style="overflow-x:auto;max-height:60vh">
             <table class="table">
               <thead><tr>
                 <th>Month</th><th style="text-align:right">Salary</th>
                 <th style="text-align:center">Off Days</th><th style="text-align:center">Extra Days</th>
                 <th style="text-align:right">Extra Pay</th><th style="text-align:right">Advances</th>
                 <th style="text-align:right">Final Payable</th><th>Status</th>
               </tr></thead>
               <tbody>${hist.map(h => `
                 <tr>
                   <td class="font-semibold">${esc(h.month)}</td>
                   <td style="text-align:right">${fmtRs(h.monthly_salary)}</td>
                   <td style="text-align:center">${h.off_days_taken}</td>
                   <td style="text-align:center">${h.extra_working_days}</td>
                   <td style="text-align:right" class="text-success">${fmtRs(h.extra_day_pay)}</td>
                   <td style="text-align:right" class="text-danger">${fmtRs(h.advances_total)}</td>
                   <td style="text-align:right;font-weight:700">${fmtRs(h.final_payable)}</td>
                   <td>${h.status === 'paid'
                     ? `<span class="chip chip-success chip-sm">Paid</span><div class="text-xs text-dim">${esc(fmtDate(h.paid_date))}</div>`
                     : '<span class="chip chip-warning chip-sm">Draft</span>'}</td>
                 </tr>`).join('')}</tbody>
             </table>
             </div>`,
        `<button class="btn" data-close>Close</button>`,
      );
    } catch (e) {
      toast('Could not load history: ' + e.message, 'error');
    }
  }
});
