// v4.0 Phase 2 — Expenses page (Reports app)
// Full expense management UI: table + filters + Add Expense modal +
// Recurring panel + category breakdown bar chart + budget-vs-actual cards.
import { route } from '../router.js';
import { api, apiPost, apiPut, apiDelete } from '../api.js';
import { $, esc, fmtRs, fmtDate, toast, openModal, closeModal,
         skeletonCards, errorBox, chartTheme, chartOptions } from '../utils.js';

const SVG = {
  wallet: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12V7H5a2 2 0 0 1 0-4h14v4"/><path d="M3 5v14a2 2 0 0 0 2 2h16v-5"/><path d="M18 12a2 2 0 0 0 0 4h4v-4z"/></svg>',
  calendar: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>',
  plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>',
  refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
  edit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>',
  repeat: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/></svg>',
  chart: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>',
};

let _chartInstance = null;

// ─── Module-level Add Expense modal (reusable from other pages) ───
// opts.expenseType: 'operating' | 'owner_draw' (default 'operating')
// onSaved: async callback fired after a successful save (e.g. to refresh the calling page)
export async function openAddExpenseModalGlobal(opts = {}, onSaved = null) {
  opts = opts || {};
  let cats = [];
  try {
    const r = await api('/api/expense-categories');
    cats = r.categories || [];
  } catch (e) {
    toast('Failed to load categories: ' + e.message, 'error');
    return;
  }
  const today = new Date().toISOString().slice(0, 10);
  openModal(
    'Add Expense',
    `
    <div class="form-group">
      <label class="form-label">Category</label>
      <select class="input" id="exp-cat-select">
        ${cats.map(c => `<option value="${c.id}">${esc(c.name)}${c.is_fixed ? ' (fixed)' : ''}</option>`).join('')}
      </select>
    </div>
    <div class="form-group">
      <label class="form-label">Amount (Rs)</label>
      <input class="input" id="exp-amount" type="number" min="0" step="0.01" placeholder="0">
    </div>
    <div class="form-group">
      <label class="form-label">Description</label>
      <input class="input" id="exp-desc" placeholder="Optional note">
    </div>
    <div class="grid grid-2">
      <div class="form-group">
        <label class="form-label">Payment Method</label>
        <select class="input" id="exp-method">
          <option value="cash">Cash</option>
          <option value="bank">Bank</option>
          <option value="card">Card</option>
          <option value="online">Online</option>
        </select>
      </div>
      <div class="form-group">
        <label class="form-label">Type</label>
        <select class="input" id="exp-type">
          <option value="operating" ${opts.expenseType === 'owner_draw' ? '' : 'selected'}>Operating</option>
          <option value="owner_draw" ${opts.expenseType === 'owner_draw' ? 'selected' : ''}>Owner Draw</option>
        </select>
      </div>
    </div>
    <div class="form-group">
      <label class="form-label">Date</label>
      <input class="input" id="exp-date" type="date" value="${today}">
    </div>
    <p class="text-dim text-sm" style="margin-top:8px">
      ${opts.expenseType === 'owner_draw'
        ? 'Owner draws reduce cash but do <strong>not</strong> reduce net profit. They are tracked as equity reductions.'
        : 'Operating expenses reduce net profit in the P&L statement.'}
    </p>
    `,
    `<button class="btn" data-close>Cancel</button>
     <button class="btn btn-primary" id="exp-save-btn"><span style="display:inline-flex;width:14px;height:14px">${SVG.check}</span> Save Expense</button>`,
  );
  $('#exp-save-btn').onclick = async () => {
    const catId = parseInt($('#exp-cat-select').value, 10);
    const catName = $('#exp-cat-select').selectedOptions[0].text.replace(/ \(fixed\)$/, '');
    const amount = parseFloat($('#exp-amount').value);
    const description = $('#exp-desc').value.trim();
    const payment_method = $('#exp-method').value;
    const expense_type = $('#exp-type').value;
    const date = $('#exp-date').value;
    if (!amount || amount <= 0) { toast('Enter a valid amount', 'error'); return; }
    try {
      await apiPost('/api/expenses', {
        category: catName, amount, description, payment_method,
        category_id: catId, expense_type, date,
      });
      toast('Expense added', 'success');
      closeModal();
      if (onSaved) await onSaved();
    } catch (e) {
      toast('Save failed: ' + e.message, 'error');
    }
  };
}

function statCard(label, value, chipClass, svgIcon, sub = '') {
  return `<div class="stat-card">
    <div class="stat-card-icon ${chipClass}">${svgIcon}</div>
    <div class="stat-card-label">${esc(label)}</div>
    <div class="stat-card-value">${value}</div>
    ${sub ? `<div class="stat-card-delta">${sub}</div>` : ''}
  </div>`;
}

function budgetCard(item) {
  const pct = item.pct || 0;
  const barColor = pct > 100 ? 'var(--danger)' : pct > 80 ? 'var(--warning)' : 'var(--success)';
  return `<div class="card">
    <div style="display:flex;justify-content:space-between;align-items:center">
      <strong>${esc(item.category)}</strong>
      <span class="text-dim text-sm">${pct}% of budget</span>
    </div>
    <div style="margin-top:8px;font-size:22px;font-weight:600">
      ${fmtRs(item.total)} <span class="text-dim text-sm">/ ${fmtRs(item.budget)} budget</span>
    </div>
    <div style="margin-top:8px;height:6px;background:var(--bg-2);border-radius:3px;overflow:hidden">
      <div style="height:100%;width:${Math.min(100, pct)}%;background:${barColor};transition:width .4s"></div>
    </div>
  </div>`;
}

// v8.3: Expenses moved from Reports app to Billing app — Reports should only
// contain reporting views. Expenses are money-out transactions, so they live
// alongside other money pages (Bills, Payments) in the Billing app.
// Old URL /reports/expenses redirects to /bills/expenses for backward compat.
route('/reports/expenses', async (el) => {
  window.location.hash = '#/bills/expenses';
  el.innerHTML = '<div class="card text-center text-dim" style="padding:24px">Redirecting to Billing → Expenses…</div>';
});

route('/bills/expenses', async (el) => {
  const thisMonth = new Date().toISOString().slice(0, 7);
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-secondary">${SVG.wallet}</div>
      <div>
        <h2 class="pos-page-header-title">Expenses</h2>
        <p class="pos-page-header-sub">Track operating expenses, owner draws, recurring bills, and budgets.</p>
      </div>
      <div class="pos-page-header-actions">
        <div class="pos-date-input">
          <span class="pos-date-input-icon">${SVG.calendar}</span>
          <input class="input input-sm" id="exp-month" type="month" value="${thisMonth}">
        </div>
        <button class="btn btn-secondary btn-sm" id="exp-recurring-btn" title="Recurring expenses">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.repeat}</span>
          Recurring
        </button>
        <button class="btn btn-primary btn-sm" id="exp-add-btn">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.plus}</span>
          Add Expense
        </button>
      </div>
    </div>

    <div id="exp-stats">${skeletonCards(3)}</div>

    <div class="card mt-4">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
        <h3>Expenses — <span id="exp-month-label">${thisMonth}</span></h3>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <select class="input input-sm" id="exp-filter-cat" style="width:auto">
            <option value="">All categories</option>
          </select>
          <select class="input input-sm" id="exp-filter-type" style="width:auto">
            <option value="">All types</option>
            <option value="operating">Operating</option>
            <option value="owner_draw">Owner Draw</option>
          </select>
          <button class="btn btn-secondary btn-sm" id="exp-owner-draw-btn" title="Quick owner draw">
            <span style="display:inline-flex;width:14px;height:14px">${SVG.wallet}</span>
            Owner Draw
          </button>
        </div>
      </div>
      <div id="exp-table" class="mt-3">${skeletonCards(2)}</div>
    </div>

    <div class="grid grid-2 mt-4">
      <div class="card">
        <h3>Expenses by Category</h3>
        <div id="exp-chart-wrap" style="height:280px;margin-top:12px"></div>
      </div>
      <div>
        <h3 style="margin-left:12px">Budget vs Actual</h3>
        <div id="exp-budget-cards" class="grid grid-1 mt-2" style="gap:8px"></div>
      </div>
    </div>`;

  $('#exp-month').onchange = loadAll;
  $('#exp-filter-cat').onchange = loadTable;
  $('#exp-filter-type').onchange = loadTable;
  $('#exp-add-btn').onclick = () => openAddExpenseModal();
  $('#exp-recurring-btn').onclick = () => openRecurringModal();
  $('#exp-owner-draw-btn').onclick = () => openAddExpenseModal({ expenseType: 'owner_draw' });

  await loadAll();

  async function loadAll() {
    await Promise.all([loadSummary(), loadTable()]);
  }

  async function loadSummary() {
    const month = $('#exp-month').value;
    $('#exp-month-label').textContent = month;
    try {
      const s = await api(`/api/expenses/summary?month=${month}`);
      // Stat cards
      $('#exp-stats').innerHTML = `
        <div class="grid grid-3">
          ${statCard('Operating Expenses', fmtRs(s.operating_total), 'chip-warning', SVG.wallet,
            `${s.delta_pct >= 0 ? '▲' : '▼'} ${Math.abs(s.delta_pct)}% vs ${s.last_month}`)}
          ${statCard('Owner Draws', fmtRs(s.owner_draw_total), 'chip-info', SVG.wallet, 'Equity reductions')}
          ${statCard('Total Cash Out', fmtRs(s.total), 'chip-danger', SVG.chart, `${s.by_category.length} categories`)}
        </div>`;
      // Populate category filter
      const filterCat = $('#exp-filter-cat');
      const currentFilter = filterCat.value;
      filterCat.innerHTML = '<option value="">All categories</option>' +
        (s.categories || []).map(c => `<option value="${c.id}">${esc(c.name)}${c.is_fixed ? ' (fixed)' : ''}</option>`).join('');
      filterCat.value = currentFilter;
      // Budget cards (only categories with a budget OR with spend this month)
      const budgetItems = (s.by_category || []).filter(b => b.budget > 0 || b.total > 0);
      if (budgetItems.length === 0) {
        $('#exp-budget-cards').innerHTML = '<div class="card text-center text-dim text-sm" style="padding:24px">No budgets set. Edit expense categories to add monthly budgets.</div>';
      } else {
        $('#exp-budget-cards').innerHTML = budgetItems.map(budgetCard).join('');
      }
      // Render bar chart
      renderCategoryChart(s.by_category || []);
    } catch (e) {
      $('#exp-stats').innerHTML = errorBox(e.message);
    }
  }

  async function loadTable() {
    const month = $('#exp-month').value;
    const catId = $('#exp-filter-cat').value;
    const expType = $('#exp-filter-type').value;
    let url = `/api/expenses?month=${month}&limit=200`;
    if (catId) url += `&category_id=${catId}`;
    if (expType) url += `&expense_type=${expType}`;
    try {
      const r = await api(url);
      const rows = r.expenses || [];
      if (rows.length === 0) {
        $('#exp-table').innerHTML = `
          <div class="text-center text-dim" style="padding:32px">
            <p style="font-weight:600;margin-bottom:4px">No expenses recorded for ${esc(month)}</p>
            <p class="text-sm">Click "Add Expense" to record one, or set up a recurring expense.</p>
          </div>`;
        return;
      }
      $('#exp-table').innerHTML = `
        <div style="overflow-x:auto">
        <table class="table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Category</th>
              <th>Description</th>
              <th>Type</th>
              <th>Method</th>
              <th style="text-align:right">Amount</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            ${rows.map(e => `
              <tr>
                <td class="text-sm">${esc(fmtDate(e.date))}</td>
                <td>${esc(e.category_name || e.category || '—')}</td>
                <td class="text-sm text-dim">${esc(e.description || '')}</td>
                <td>${e.expense_type === 'owner_draw'
                    ? '<span class="chip chip-info chip-sm">Owner Draw</span>'
                    : '<span class="chip chip-secondary chip-sm">Operating</span>'}</td>
                <td class="text-sm">${esc(e.payment_method || 'cash')}</td>
                <td style="text-align:right;font-weight:600" class="text-danger">${fmtRs(e.amount)}</td>
                <td><button class="btn-icon btn-icon-danger" data-del="${e.id}" title="Delete">
                  <span style="display:inline-flex;width:14px;height:14px">${SVG.trash}</span>
                </button></td>
              </tr>`).join('')}
          </tbody>
        </table>
        </div>`;
      // Wire delete buttons
      document.querySelectorAll('[data-del]').forEach(btn => {
        btn.onclick = async () => {
          const id = btn.getAttribute('data-del');
          if (!confirm('Delete this expense?')) return;
          try {
            await apiDelete(`/api/expenses/${id}`);
            toast('Expense deleted', 'success');
            await loadAll();
          } catch (e) {
            toast('Delete failed: ' + e.message, 'error');
          }
        };
      });
    } catch (e) {
      $('#exp-table').innerHTML = errorBox(e.message);
    }
  }

  function renderCategoryChart(byCategory) {
    const wrap = $('#exp-chart-wrap');
    if (!wrap) return;
    if (typeof Chart === 'undefined') {
      wrap.innerHTML = '<div class="text-dim text-sm text-center" style="padding:40px">Chart.js not loaded</div>';
      return;
    }
    const data = byCategory.filter(b => b.total > 0).slice(0, 8);
    if (data.length === 0) {
      wrap.innerHTML = '<div class="text-dim text-sm text-center" style="padding:40px">No expense data for this month</div>';
      return;
    }
    const theme = chartTheme();
    if (_chartInstance) { _chartInstance.destroy(); _chartInstance = null; }
    const canvas = document.createElement('canvas');
    wrap.innerHTML = '';
    wrap.appendChild(canvas);
    _chartInstance = new Chart(canvas, chartOptions({
      type: 'bar',
      data: {
        labels: data.map(d => d.category),
        datasets: [{
          label: 'Spend (Rs)',
          data: data.map(d => d.total),
          backgroundColor: data.map((_, i) => theme.colors[i % theme.colors.length]),
          borderRadius: 6,
        }],
      },
      options: {
        indexAxis: 'y',
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (ctx) => {
                const item = data[ctx.dataIndex];
                return `Rs ${item.total.toLocaleString()} / ${item.budget > 0 ? 'budget Rs ' + item.budget.toLocaleString() : 'no budget'}`;
              },
            },
          },
        },
        scales: {
          x: { grid: { color: theme.gridColor }, ticks: { color: theme.textColor } },
          y: { grid: { display: false }, ticks: { color: theme.textColor } },
        },
      },
    }));
  }

  async function openAddExpenseModal(opts = {}) {
    return openAddExpenseModalGlobal(opts, async () => { await loadAll(); });
  }

  async function openRecurringModal() {
    let recurring = [], cats = [];
    try {
      const [r1, r2] = await Promise.all([
        api('/api/recurring-expenses'),
        api('/api/expense-categories'),
      ]);
      recurring = r1.recurring || [];
      cats = r2.categories || [];
    } catch (e) {
      toast('Failed to load: ' + e.message, 'error');
      return;
    }
    openModal(
      'Recurring Expenses',
      `
      <p class="text-dim text-sm" style="margin-bottom:12px">
        Active recurring expenses auto-generate on their day of each month. Generated expenses appear in the table above.
      </p>
      <div id="rec-list" style="margin-bottom:16px">
        ${recurring.length === 0
          ? '<p class="text-dim text-sm text-center" style="padding:16px">No recurring expenses yet. Add one below.</p>'
          : recurring.map(r => `
            <div class="card" style="padding:12px;margin-bottom:8px;display:flex;justify-content:space-between;align-items:center">
              <div>
                <strong>${esc(r.category_name || 'Category ' + r.category_id)}</strong>
                <span class="text-dim text-sm"> — Rs ${r.amount.toLocaleString()} / ${esc(r.payment_method)} / day ${r.day_of_month}</span>
                <div class="text-sm text-dim">${esc(r.description || '')}</div>
              </div>
              <div style="display:flex;gap:6px;align-items:center">
                <label class="text-sm" style="display:flex;align-items:center;gap:4px;cursor:pointer">
                  <input type="checkbox" data-toggle="${r.id}" ${r.active ? 'checked' : ''}>
                  Active
                </label>
                <button class="btn-icon btn-icon-danger" data-rec-del="${r.id}" title="Delete">
                  <span style="display:inline-flex;width:14px;height:14px">${SVG.trash}</span>
                </button>
              </div>
            </div>`).join('')}
      </div>
      <hr style="border:0;border-top:1px solid var(--border);margin:16px 0">
      <h4 style="margin-bottom:8px">Add New Recurring</h4>
      <div class="grid grid-2">
        <div class="form-group">
          <label class="form-label">Category</label>
          <select class="input" id="rec-cat">
            ${cats.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join('')}
          </select>
        </div>
        <div class="form-group">
          <label class="form-label">Amount (Rs)</label>
          <input class="input" id="rec-amount" type="number" min="0" step="0.01" placeholder="0">
        </div>
      </div>
      <div class="grid grid-2">
        <div class="form-group">
          <label class="form-label">Day of Month</label>
          <input class="input" id="rec-day" type="number" min="1" max="31" value="1">
        </div>
        <div class="form-group">
          <label class="form-label">Payment Method</label>
          <select class="input" id="rec-method">
            <option value="cash">Cash</option>
            <option value="bank">Bank</option>
            <option value="online">Online</option>
          </select>
        </div>
      </div>
      <div class="form-group">
        <label class="form-label">Description</label>
        <input class="input" id="rec-desc" placeholder="Optional">
      </div>
      `,
      `<button class="btn" data-close>Close</button>
       <button class="btn btn-secondary" id="rec-generate-btn"><span style="display:inline-flex;width:14px;height:14px">${SVG.refresh}</span> Generate Now</button>
       <button class="btn btn-primary" id="rec-add-btn"><span style="display:inline-flex;width:14px;height:14px">${SVG.plus}</span> Add Recurring</button>`,
    );
    // Wire toggle, delete, generate, add
    document.querySelectorAll('[data-toggle]').forEach(cb => {
      cb.onchange = async () => {
        const id = cb.getAttribute('data-toggle');
        try {
          await apiPut(`/api/recurring-expenses/${id}`, { active: cb.checked });
          toast(cb.checked ? 'Recurring activated' : 'Recurring paused', 'success');
        } catch (e) { toast('Update failed: ' + e.message, 'error'); }
      };
    });
    document.querySelectorAll('[data-rec-del]').forEach(btn => {
      btn.onclick = async () => {
        const id = btn.getAttribute('data-rec-del');
        if (!confirm('Delete this recurring expense? Existing generated expenses are kept.')) return;
        try {
          await apiDelete(`/api/recurring-expenses/${id}`);
          toast('Recurring deleted', 'success');
          closeModal();
          openRecurringModal();
        } catch (e) { toast('Delete failed: ' + e.message, 'error'); }
      };
    });
    $('#rec-generate-btn').onclick = async () => {
      try {
        const r = await apiPost('/api/recurring-expenses/generate', {});
        toast(`Generated ${r.generated}, skipped ${r.skipped}`, 'success');
        closeModal();
        await loadAll();
      } catch (e) { toast('Generate failed: ' + e.message, 'error'); }
    };
    $('#rec-add-btn').onclick = async () => {
      const payload = {
        category_id: parseInt($('#rec-cat').value, 10),
        amount: parseFloat($('#rec-amount').value),
        description: $('#rec-desc').value.trim(),
        payment_method: $('#rec-method').value,
        day_of_month: parseInt($('#rec-day').value, 10) || 1,
        active: true,
      };
      if (!payload.amount || payload.amount <= 0) { toast('Enter a valid amount', 'error'); return; }
      try {
        await apiPost('/api/recurring-expenses', payload);
        toast('Recurring expense added', 'success');
        closeModal();
        openRecurringModal();
      } catch (e) { toast('Add failed: ' + e.message, 'error'); }
    };
  }
});
