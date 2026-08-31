// v5.0 Phase 7 — Cash Buckets page (Reports app)
import { route } from '../router.js';
import { api, apiPost } from '../api.js';
import { $, esc, fmt, fmtRs, fmtDate, toast, openModal, closeModal,
         skeletonCards, errorBox } from '../utils.js';

const SVG = {
  wallet: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12V7H5a2 2 0 0 1 0-4h14v4"/><path d="M3 5v14a2 2 0 0 0 2 2h16v-5"/><path d="M18 12a2 2 0 0 0 0 4h4v-4z"/></svg>',
  calendar: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>',
  plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>',
  alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
};

function bucketBar(label, value, maxValue, color, sublabel = '') {
  const pct = maxValue > 0 ? Math.min(100, Math.abs(value) / maxValue * 100) : 0;
  return `<div style="margin-bottom:16px">
    <div style="display:flex;justify-content:space-between;margin-bottom:4px">
      <span style="font-weight:600">${esc(label)}</span>
      <span style="font-weight:700;color:${color}">${fmtRs(value)}</span>
    </div>
    ${sublabel ? `<div class="text-dim text-sm" style="margin-bottom:4px">${sublabel}</div>` : ''}
    <div style="height:8px;background:var(--bg-2, #f3f4f6);border-radius:4px;overflow:hidden">
      <div style="height:100%;width:${pct}%;background:${color};transition:width .6s ease;border-radius:4px"></div>
    </div>
  </div>`;
}

// v8.3: Cash Buckets moved from Reports app to Billing app — it's a cash
// management action (how much can I withdraw), not a reporting view.
// Old URL /reports/cash-buckets redirects to /bills/cash-buckets for backward compat.
route('/reports/cash-buckets', async (el) => {
  window.location.hash = '#/bills/cash-buckets';
  el.innerHTML = '<div class="card text-center text-dim" style="padding:24px">Redirecting to Billing → Cash Buckets…</div>';
});

route('/bills/cash-buckets', async (el) => {
  const today = new Date().toISOString().slice(0, 10);
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-secondary">${SVG.wallet}</div>
      <div>
        <h2 class="pos-page-header-title">Cash Buckets</h2>
        <p class="pos-page-header-sub">Sales − Stock Replacement − Operating Expenses − Business Reserve = Available for Owner Withdrawal.</p>
      </div>
      <div class="pos-page-header-actions">
        <div class="pos-date-input">
          <span class="pos-date-input-icon">${SVG.calendar}</span>
          <input class="input input-sm" id="cb-date" type="date" value="${today}">
        </div>
        <button class="btn btn-secondary btn-sm" id="cb-inject-btn" title="Record owner capital injection (initial investment, top-up, partner contribution, bank loan)">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.plus}</span>
          Capital Injection
        </button>
        <button class="btn btn-primary btn-sm" id="cb-withdraw-btn">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.plus}</span>
          Withdraw
        </button>
      </div>
    </div>
    <div id="cb-out">${skeletonCards(2)}</div>`;

  $('#cb-date').onchange = loadAll;
  $('#cb-withdraw-btn').onclick = () => openWithdrawModal();
  $('#cb-inject-btn').onclick = () => openCapitalInjectionModal();
  await loadAll();

  async function loadAll() {
    try {
      const [buckets, reserve, withdrawals, injections] = await Promise.all([
        api(`/api/profit/cash-buckets?date=${$('#cb-date').value}`),
        api('/api/stock-reserve'),
        api('/api/owner-withdrawals?limit=20'),
        api('/api/capital-injections?limit=20'),
      ]);
      renderBuckets(buckets, reserve);
      renderWithdrawals(withdrawals.withdrawals || []);
      renderInjections(injections.injections || []);
    } catch (e) {
      $('#cb-out').innerHTML = errorBox(e.message);
    }
  }

  function renderBuckets(b, sr) {
    const maxValue = Math.max(b.sales, b.cogs, b.buckets.operating_expenses,
                              b.buckets.business_reserve, b.buckets.owner_withdrawal, 1);
    const reserveColor = sr.color === 'green' ? 'var(--success, #16a34a)'
                       : sr.color === 'amber' ? 'var(--warning, #d97706)'
                       : 'var(--danger, #dc2626)';
    // v8.2: Fetch safe-withdrawal verdict for the banner
    let verdictBanner = '';
    api('/api/audit/safe-withdrawal').then(sw => {
      const banner = $('#cb-verdict-banner');
      if (!banner) return;
      const isOver = sw.is_over;
      const color = isOver ? 'var(--danger-text,#DC2626)' : 'var(--success-text,#16A34A)';
      const bg = isOver ? 'var(--danger-soft,#FEE2E2)' : 'var(--success-soft,#f0fdf4)';
      const border = isOver ? 'var(--danger,#DC2626)' : 'var(--success,#16A34A)';
      const text = isOver
        ? `Over-withdrawn by ${fmtRs(sw.over_amount)}`
        : `Safe to withdraw ${fmtRs(sw.remaining_safe)} this month`;
      banner.innerHTML = `<div class="card" style="padding:12px;background:${bg};border:1px solid ${border};margin-bottom:16px;display:flex;gap:12px;align-items:center">
        <span style="display:inline-flex;width:20px;height:20px;color:${color}">${isOver ? SVG.alert : SVG.check}</span>
        <div style="flex:1">
          <strong style="color:${color}">${text}</strong>
          <div class="text-sm" style="color:${color};margin-top:2px">
            Cash: ${fmtRs(sw.cash)} | Safe limit: ${fmtRs(sw.safe_withdrawal)} | Withdrawn: ${fmtRs(sw.withdrawn_this_month)}
          </div>
        </div>
      </div>`;
    }).catch(() => {});

    $('#cb-out').innerHTML = `
      <div id="cb-verdict-banner"></div>
      <div class="grid grid-2" style="gap:16px;margin-bottom:16px">
        <div class="card" style="padding:24px">
          <h3 style="margin-bottom:12px">Cash Waterfall (Month-to-Date)</h3>
          ${bucketBar('Sales', b.sales, maxValue, 'var(--success, #16a34a)', 'Total revenue this month')}
          ${bucketBar('− Stock Replacement', b.buckets.stock_replacement, maxValue, 'var(--danger, #dc2626)', 'COGS — reinvest to maintain stock')}
          ${bucketBar('− Operating Expenses', b.buckets.operating_expenses, maxValue, 'var(--danger, #dc2626)', 'Excludes owner draws')}
          ${bucketBar('− Business Reserve', b.buckets.business_reserve, maxValue, 'var(--warning, #d97706)', `${b.business_reserve_pct}% of gross profit`)}
          <div style="border-top:2px solid var(--border);margin-top:12px;padding-top:12px">
            ${bucketBar('= Available for Withdrawal', b.available_for_withdrawal, maxValue,
              b.available_for_withdrawal >= 0 ? 'var(--info, #3b82f6)' : 'var(--danger, #dc2626)',
              'Cash minus reserve obligations')}
          </div>
          ${b.buckets.owner_withdrawal > 0 ? `
            <div style="margin-top:12px;padding:10px;background:var(--bg-2, #f3f4f6);border-radius:8px;font-size:13px">
              <strong>Already withdrawn this month: ${fmtRs(b.buckets.owner_withdrawal)}</strong>
            </div>` : ''}
          ${b.capital_injections_total > 0 ? `
            <div style="margin-top:8px;padding:10px;background:var(--success-soft, #f0fdf4);border:1px solid var(--success, #16a34a);border-radius:8px;font-size:13px;color:var(--success-text, #15803d);display:flex;justify-content:space-between;align-items:center">
              <span><strong>Owner-invested capital (all-time):</strong></span>
              <strong style="font-size:15px">+${fmtRs(b.capital_injections_total)}</strong>
            </div>
            <div class="text-xs text-dim" style="margin-top:4px">Already reflected in Cash in Drawer (${fmtRs(b.cash_in_drawer)}). Capital is equity, not revenue — withdrawing it counts as an owner draw.</div>
          ` : `
            <div style="margin-top:8px;padding:10px;background:var(--warning-soft, #fef3c7);border:1px solid var(--warning, #d97706);border-radius:8px;font-size:12px;color:var(--warning-text, #a16207)">
              <strong>Is "Available for Withdrawal" negative?</strong>
              Record a Capital Injection to credit the cash drawer for the initial stock investment you made before using BillBook.
            </div>
          `}
        </div>

        <div class="card" style="padding:24px">
          <h3 style="margin-bottom:12px">Stock Reserve</h3>
          <div style="text-align:center;padding:16px 0">
            <div class="text-dim text-sm" style="text-transform:uppercase;font-weight:600">Days of Cover</div>
            <div style="font-size:42px;font-weight:800;color:${reserveColor};margin:8px 0">
              ${sr.stock_reserve_days.toFixed(1)}
            </div>
            <div class="text-dim text-sm">target: ${sr.stock_reserve_target_days.toFixed(0)} days
              (${sr.gap > 0 ? 'short by ' + sr.gap.toFixed(1) + ' days' : 'surplus ' + Math.abs(sr.gap).toFixed(1) + ' days'})
            </div>
          </div>
          <div style="padding:12px;background:${reserveColor}22;border-radius:8px;color:${reserveColor};font-weight:600;font-size:13px;margin-bottom:12px">
            ${esc(sr.recommendation)}
          </div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:13px">
            <div><span class="text-dim">Daily COGS avg (30d):</span><br><strong>${fmtRs(sr.daily_cogs_avg_30d)}</strong></div>
            <div><span class="text-dim">Cash in drawer:</span><br><strong>${fmtRs(sr.cash_in_drawer)}</strong></div>
            <div><span class="text-dim">Safe weekly withdrawal:</span><br><strong style="color:var(--success-text, #16a34a)">${fmtRs(sr.safe_withdrawal_weekly)}</strong></div>
            <div><span class="text-dim">COGS last 30d:</span><br><strong>${fmtRs(sr.cogs_30d_total)}</strong></div>
          </div>
        </div>
      </div>`;

    // Color-code the reserve card border
    const reserveCard = document.querySelectorAll('.card')[1];
    if (reserveCard) {
      reserveCard.style.borderColor = reserveColor;
      reserveCard.style.borderWidth = '2px';
    }
  }

  function renderWithdrawals(withdrawals) {
    if (withdrawals.length === 0) return;
    const wrap = document.createElement('div');
    wrap.className = 'card';
    wrap.style.marginTop = '16px';
    wrap.innerHTML = `
      <h3 style="margin-bottom:12px">Recent Owner Withdrawals</h3>
      <div style="overflow-x:auto">
      <table class="table">
        <thead><tr><th>Date</th><th>Amount</th><th>Method</th><th>Notes</th></tr></thead>
        <tbody>
          ${withdrawals.map(w => `<tr>
            <td class="text-sm">${esc(fmtDate(w.created_at))}</td>
            <td style="font-weight:600;color:var(--danger-text, #dc2626)">${fmtRs(w.amount)}</td>
            <td class="text-sm">${esc(w.payment_method)}</td>
            <td class="text-sm text-dim">${esc(w.notes || '')}</td>
          </tr>`).join('')}
        </tbody>
      </table>
      </div>`;
    $('#cb-out').appendChild(wrap);
  }

  // v8.12.1: Render capital injections history table (mirror of withdrawals)
  function renderInjections(injections) {
    if (injections.length === 0) return;
    const wrap = document.createElement('div');
    wrap.className = 'card';
    wrap.style.marginTop = '16px';
    const sourceLabel = (s) => ({
      owner_pocket: "Owner's Pocket",
      partner: "Partner",
      bank_loan: "Bank Loan",
      opening_balance: "Opening Balance",
    })[s] || s;
    wrap.innerHTML = `
      <h3 style="margin-bottom:12px">Recent Capital Injections
        <span class="text-sm text-dim" style="font-weight:400">— owner-invested equity (NOT revenue)</span>
      </h3>
      <div style="overflow-x:auto">
      <table class="table">
        <thead><tr><th>Date</th><th>Amount</th><th>Source</th><th>Method</th><th>Notes</th></tr></thead>
        <tbody>
          ${injections.map(inj => `<tr>
            <td class="text-sm">${esc(fmtDate(inj.created_at))}</td>
            <td style="font-weight:600;color:var(--success-text, #16a34a)">+${fmtRs(inj.amount)}</td>
            <td><span class="badge badge-success">${esc(sourceLabel(inj.source))}</span></td>
            <td class="text-sm">${esc(inj.payment_method || 'cash')}</td>
            <td class="text-sm text-dim">${esc(inj.notes || '')}</td>
          </tr>`).join('')}
        </tbody>
      </table>
      </div>`;
    $('#cb-out').appendChild(wrap);
  }

  // v8.12.1: Capital injection modal — records owner-invested capital
  async function openCapitalInjectionModal() {
    let sources = [];
    try {
      const r = await api('/api/capital-injections/sources');
      sources = r.sources || [];
    } catch {}
    if (!sources.length) {
      sources = [
        {code: 'owner_pocket', label: "Owner's Pocket (personal savings)"},
        {code: 'partner', label: "Partner Contribution"},
        {code: 'bank_loan', label: "Bank Loan"},
        {code: 'opening_balance', label: "Opening Balance (one-time fix for Day 1)"},
      ];
    }
    // Read current withdrawal number to show in the helper banner
    let currentAvailable = null;
    let currentCash = null;
    try {
      const b = await api(`/api/profit/cash-buckets?date=${$('#cb-date').value}`);
      currentAvailable = b.available_for_withdrawal;
      currentCash = b.cash_in_drawer;
    } catch {}

    const today = new Date().toISOString().slice(0, 10);
    openModal('Record Capital Injection', `
      <div class="card" style="background:var(--success-soft, #f0fdf4);border:1px solid var(--success, #16a34a);padding:12px;margin-bottom:16px">
        <strong style="color:var(--success-text, #15803d)">Capital injections credit cash_drawer (+amount).</strong>
        <div class="text-sm" style="color:var(--success-text, #15803d);margin-top:4px">
          Use this to record money <em>you</em> put into the business — initial investment, top-ups,
          partner contributions, or bank loans. Capital is equity, not revenue: it does NOT inflate
          your profit, but it does fix a negative "Available for Withdrawal" caused by supplier bills
          being confirmed before any sale was recorded.
        </div>
        ${currentAvailable !== null && currentAvailable < 0 ? `
          <div class="text-sm" style="margin-top:8px;color:var(--danger-text, #b91c1c)">
            <strong>Current available for withdrawal:</strong> ${fmtRs(currentAvailable)} (negative).
            Record an injection of at least <strong>${fmtRs(Math.abs(currentAvailable))}</strong> to bring it back to zero.
          </div>` : ''}
      </div>
      <div class="form-group">
        <label class="form-label">Amount (Rs)</label>
        <input class="input" id="ci-amount" type="number" min="0.01" step="0.01" placeholder="e.g. 200000" autofocus>
      </div>
      <div class="form-group">
        <label class="form-label">Source</label>
        <select class="input" id="ci-source">
          ${sources.map(s => `<option value="${esc(s.code)}">${esc(s.label)}</option>`).join('')}
        </select>
      </div>
      <div class="form-group">
        <label class="form-label">Payment Method</label>
        <select class="input" id="ci-method">
          <option value="cash">Cash</option>
          <option value="bank">Bank Transfer</option>
        </select>
      </div>
      <div class="form-group">
        <label class="form-label">Date (defaults to today)</label>
        <input class="input" id="ci-date" type="date" value="${today}">
      </div>
      <div class="form-group">
        <label class="form-label">Notes (optional)</label>
        <input class="input" id="ci-notes" placeholder="e.g. Initial investment to set up the shop">
      </div>
      <div class="form-group">
        <label class="form-label">Manager PIN <span class="text-danger">*</span></label>
        <input class="input" id="ci-pin" type="password" placeholder="Admin PIN required" autocomplete="off">
        <div class="text-xs text-dim" style="margin-top:4px">Capital injections are equity events — admin PIN required for audit trail.</div>
      </div>`,
      `<button class="btn btn-secondary" data-modal-close>Cancel</button>
       <button class="btn btn-primary" id="ci-submit">Record Injection</button>`);

    $('#ci-submit').onclick = async () => {
      const amount = parseFloat($('#ci-amount').value);
      const source = $('#ci-source').value;
      const method = $('#ci-method').value;
      const date = $('#ci-date').value;
      const notes = $('#ci-notes').value;
      const pin = $('#ci-pin').value;
      if (!amount || amount <= 0) { toast('Enter a valid amount', 'error'); return; }
      if (!pin) { toast('Manager PIN required', 'error'); return; }
      $('#ci-submit').disabled = true;
      $('#ci-submit').textContent = 'Recording...';
      try {
        await apiPost('/api/capital-injections', {
          amount, source, payment_method: method, notes, date, manager_pin: pin
        });
        toast(`Capital injection of Rs ${fmt(amount)} recorded`, 'success');
        closeModal();
        loadAll();
      } catch (e) {
        const detail = e?.detail?.detail || e?.detail || e.message;
        if (e.status === 403) {
          toast('Wrong PIN — capital injection not recorded', 'error');
        } else {
          toast('Failed: ' + (typeof detail === 'string' ? detail : JSON.stringify(detail)), 'error');
        }
        $('#ci-submit').disabled = false;
        $('#ci-submit').textContent = 'Record Injection';
      }
    };
  }

  async function openWithdrawModal() {
    // v8.2: Fetch safe-withdrawal amount for live feedback
    let safeData = null;
    try {
      safeData = await api('/api/audit/safe-withdrawal');
    } catch {}
    const safeLimit = safeData ? safeData.remaining_safe : 0;
    const isAlreadyOver = safeData ? safeData.is_over : false;

    openModal('Owner Withdrawal', `
      <div class="card" style="background:var(--bg-warning-soft, #fef3c7);padding:12px;margin-bottom:16px">
        <strong>Owner withdrawals reduce cash but are NOT operating expenses.</strong>
        They are tracked as equity reductions, separate from P&L.
      </div>
      ${isAlreadyOver ? `<div class="card" style="padding:10px;background:var(--danger-soft,#FEE2E2);border:1px solid var(--danger,#DC2626);margin-bottom:12px;color:var(--danger-text,#DC2626);font-size:13px">
        <strong>Warning:</strong> You are already over-withdrawn by Rs ${fmtRs(safeData.over_amount)} this month.
      </div>` : ''}
      <div class="form-group">
        <label class="form-label">Amount (Rs)</label>
        <input class="input" id="ow-amount" type="number" min="0" step="0.01" placeholder="0" autofocus>
      </div>
      <div id="ow-feedback" style="padding:8px 12px;border-radius:8px;margin-bottom:12px;font-size:13px;font-weight:600;display:none"></div>
      <div class="form-group">
        <label class="form-label">Payment Method</label>
        <select class="input" id="ow-method">
          <option value="cash">Cash</option>
          <option value="bank">Bank Transfer</option>
        </select>
      </div>
      <div class="form-group">
        <label class="form-label">Notes</label>
        <input class="input" id="ow-notes" placeholder="Optional">
      </div>
      <div id="ow-pin-section" style="display:none">
        <div class="card" style="padding:10px;background:var(--danger-soft,#FEE2E2);border:1px solid var(--danger,#DC2626);margin-bottom:12px">
          <strong style="color:var(--danger-text,#DC2626)">Over-safe withdrawal — Manager PIN required</strong>
          <div class="text-sm" style="color:var(--danger-text,#DC2626);margin-top:2px">
            This withdrawal exceeds your safe limit. Enter your manager PIN to proceed.
            The over-withdrawal will be logged to the auditor.
          </div>
        </div>
        <div class="form-group">
          <label class="form-label">Manager PIN</label>
          <input class="input" id="ow-pin" type="password" inputmode="numeric" maxlength="8" placeholder="Enter PIN">
        </div>
      </div>`,
      `<button class="btn" data-close>Cancel</button>
       <button class="btn btn-primary" id="ow-save-btn" disabled>
         <span style="display:inline-flex;width:14px;height:14px">${SVG.check}</span>
         Record Withdrawal
       </button>`);

    const amountInput = $('#ow-amount');
    const feedback = $('#ow-feedback');
    const pinSection = $('#ow-pin-section');
    const saveBtn = $('#ow-save-btn');

    // Live feedback as amount is typed
    amountInput.addEventListener('input', () => {
      const amount = parseFloat(amountInput.value) || 0;
      if (amount <= 0) {
        feedback.style.display = 'none';
        pinSection.style.display = 'none';
        saveBtn.disabled = true;
        return;
      }
      saveBtn.disabled = false;
      if (amount > safeLimit && safeLimit > 0) {
        const over = amount - safeLimit;
        feedback.style.display = 'block';
        feedback.style.background = 'var(--danger-soft,#FEE2E2)';
        feedback.style.color = 'var(--danger-text,#DC2626)';
        feedback.innerHTML = `<span style="display:inline-flex;width:14px;height:14px;vertical-align:middle;margin-right:4px">${SVG.alert}</span>Exceeds safe limit by Rs ${fmtRs(over)}. Manager PIN required.`;
        pinSection.style.display = 'block';
      } else if (safeLimit <= 0) {
        // Already over-withdrawn — any amount requires PIN
        feedback.style.display = 'block';
        feedback.style.background = 'var(--danger-soft,#FEE2E2)';
        feedback.style.color = 'var(--danger-text,#DC2626)';
        feedback.innerHTML = `<span style="display:inline-flex;width:14px;height:14px;vertical-align:middle;margin-right:4px">${SVG.alert}</span>Already over-withdrawn. Manager PIN required.`;
        pinSection.style.display = 'block';
      } else {
        feedback.style.display = 'block';
        feedback.style.background = 'var(--success-soft,#f0fdf4)';
        feedback.style.color = 'var(--success-text,#16A34A)';
        feedback.innerHTML = `<span style="display:inline-flex;width:14px;height:14px;vertical-align:middle;margin-right:4px">${SVG.check}</span>Within safe limit (Rs ${fmtRs(safeLimit)} available)`;
        pinSection.style.display = 'none';
      }
    });

    saveBtn.onclick = async () => {
      const amount = parseFloat(amountInput.value);
      if (!amount || amount <= 0) { toast('Enter a valid amount', 'error'); return; }
      const isOverSafe = amount > safeLimit || safeLimit <= 0;
      if (isOverSafe) {
        const pin = $('#ow-pin').value.trim();
        if (!pin) { toast('Manager PIN required for over-safe withdrawal', 'error'); return; }
        // Verify PIN via the security endpoint — FAIL CLOSED on error
        try {
          const pinR = await apiPost('/api/security/verify-pin', { pin });
          if (!pinR.ok) { toast('Invalid PIN', 'error'); return; }
        } catch (e) {
          // FAIL CLOSED: if the endpoint is unreachable, reject the withdrawal
          toast('Cannot verify PIN (security endpoint unreachable). Withdrawal blocked.', 'error');
          return;
        }
      }
      try {
        await apiPost('/api/owner-withdrawals', {
          amount,
          payment_method: $('#ow-method').value,
          notes: $('#ow-notes').value.trim() + (isOverSafe ? ' [OVER-SAFE: PIN verified]' : ''),
        });
        toast(isOverSafe ? 'Over-safe withdrawal recorded (logged to auditor)' : 'Withdrawal recorded', 'success');
        closeModal();
        await loadAll();
      } catch (e) {
        toast('Withdrawal failed: ' + e.message, 'error');
      }
    };

    amountInput.focus();
  }
});
