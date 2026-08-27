// v7.2 — Approval Queue page (THE BACKBONE of the AI system)
// Renders pending_actions as action cards with what/why/impact + Approve/Edit/Reject.
// PIN required for price changes. Badge count on launcher. Grouped batches.

import { route, navigate, reload } from '../router.js';
import { api, apiPost, apiPut } from '../api.js';
import { $, esc, fmtRs, fmtDate, toast, openModal, closeModal,
         skeletonCards, errorBox, emptyState } from '../utils.js';

const SVG = {
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>',
  x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
  alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
  lock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>',
  clock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>',
  robot: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="10" rx="2"/><circle cx="12" cy="5" r="2"/><path d="M12 7v4"/><line x1="8" y1="16" x2="8" y2="16"/><line x1="16" y1="16" x2="16" y2="16"/></svg>',
  edit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>',
  layers: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></svg>',
  tag: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg>',
  cart: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="9" cy="21" r="1"/><circle cx="20" cy="21" r="1"/><path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6"/></svg>',
  receipt: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21V5a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v16l4-3 4 3 4-3 4 3z"/></svg>',
  mail: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/></svg>',
  megaphone: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 11l18-5v12L3 14v-3z"/><path d="M11.6 16.8a3 3 0 1 1-5.8-1.6"/></svg>',
};

// Map action_type to icon + human label
const ACTION_META = {
  'apply_price_suggestion': { label: 'Price Change', icon: SVG.tag, pin: true, color: 'var(--warning-text,#D97706)' },
  'draft_purchase_order': { label: 'Purchase Order Draft', icon: SVG.cart, pin: false, color: 'var(--accent-text,#2563EB)' },
  'confirm_bill': { label: 'Bill Confirmation', icon: SVG.receipt, pin: false, color: 'var(--success-text,#16A34A)' },
  'draft_expense': { label: 'Expense Draft', icon: SVG.receipt, pin: false, color: 'var(--danger-text,#DC2626)' },
  'draft_recurring_expense': { label: 'Recurring Expense Draft', icon: SVG.clock, pin: false, color: 'var(--danger-text,#DC2626)' },
  'queue_whatsapp_reminder': { label: 'WhatsApp Reminder', icon: SVG.mail, pin: false, color: 'var(--success-text,#16A34A)' },
  'draft_dead_stock_promo': { label: 'Dead Stock Promotion', icon: SVG.megaphone, pin: false, color: 'var(--accent-text,#2563EB)' },
  'happy_hour_rule': { label: 'Happy-Hour Rule', icon: SVG.clock, pin: true, color: 'var(--warning-text,#D97706)' },
  'customer_broadcast': { label: 'Customer Broadcast', icon: SVG.megaphone, pin: false, color: 'var(--accent-text,#2563EB)' },
};

let _pendingCount = 0;

// Export for launcher badge
export async function refreshPendingCount() {
  try {
    const r = await api('/api/pending-actions?status=pending&limit=1');
    _pendingCount = r.count || 0;
    // Update sidebar badge if present
    const badge = document.getElementById('approval-badge');
    if (badge) {
      badge.textContent = _pendingCount;
      badge.style.display = _pendingCount > 0 ? 'flex' : 'none';
    }
    // Also update any in-page count badges
    document.querySelectorAll('[data-pending-count]').forEach(el => {
      el.textContent = String(_pendingCount);
      el.style.display = _pendingCount > 0 ? '' : 'none';
    });
    return _pendingCount;
  } catch { return 0; }
}

route('/insights/approval-queue', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-warning">${SVG.robot}</div>
      <div>
        <h2 class="pos-page-header-title">
          Approval Queue
          <span id="aq-count-badge" data-pending-count style="display:none;background:var(--warning,#D97706);color:white;border-radius:10px;padding:2px 8px;font-size:12px;font-weight:600;margin-left:8px;vertical-align:middle">0</span>
        </h2>
        <p class="pos-page-header-sub">AI-drafted actions waiting for your approval. Nothing executes without your decision.</p>
      </div>
      <div class="pos-page-header-actions">
        <select class="input input-sm" id="aq-filter" style="width:auto">
          <option value="pending">Pending</option>
          <option value="executed">Approved</option>
          <option value="rejected">Rejected</option>
          <option value="expired">Expired</option>
          <option value="">All</option>
        </select>
        <button class="btn btn-secondary btn-sm" id="aq-refresh">
          <span style="display:inline-flex;width:12px;height:12px">${SVG.check}</span>
          Refresh
        </button>
      </div>
    </div>
    <div id="aq-out">${skeletonCards(2)}</div>`;

  $('#aq-filter').onchange = loadActions;
  $('#aq-refresh').onclick = loadActions;
  await loadActions();
  // Refresh the badge on entry
  refreshPendingCount();

  async function loadActions() {
    const status = $('#aq-filter').value;
    try {
      const r = await api(`/api/pending-actions?status=${status}&limit=100`);
      const actions = r.actions || [];
      // Always update the count badge from the pending-filter result
      if (status === 'pending' || status === '') {
        const pendingN = status === 'pending'
          ? r.count
          : (actions || []).filter(a => a.status === 'pending').length;
        const badge = $('#aq-count-badge');
        if (badge) {
          badge.textContent = String(pendingN);
          badge.style.display = pendingN > 0 ? '' : 'none';
        }
      }
      if (actions.length === 0) {
        $('#aq-out').innerHTML = emptyState(
          status === 'pending' ? 'No pending actions' : 'No actions found',
          status === 'pending'
            ? 'When the AI drafts an action (purchase order, price change, expense, etc.), it will appear here for your approval. Try the "Prepare for season" button on the AI Usage page to generate sample batched actions.'
            : 'Try changing the filter above.',
          '', ''
        );
        return;
      }
      // Group by batch_id if present
      const batches = {};
      const standalone = [];
      for (const a of actions) {
        if (a.batch_id) {
          if (!batches[a.batch_id]) batches[a.batch_id] = [];
          batches[a.batch_id].push(a);
        } else {
          standalone.push(a);
        }
      }
      let html = '';
      // Render batches first
      for (const [bid, batchActions] of Object.entries(batches)) {
        html += renderBatch(bid, batchActions);
      }
      // Then standalone
      for (const a of standalone) {
        html += renderCard(a);
      }
      $('#aq-out').innerHTML = html;
      // Wire buttons
      wireActionButtons();
    } catch (e) {
      $('#aq-out').innerHTML = errorBox(e.message);
    }
  }

  function renderBatch(batchId, actions) {
    const firstAction = actions[0];
    const allPending = actions.every(a => a.status === 'pending');
    const pendingN = actions.filter(a => a.status === 'pending').length;
    const executedN = actions.filter(a => a.status === 'executed').length;
    const rejectedN = actions.filter(a => a.status === 'rejected').length;
    // Determine batch source label
    const source = firstAction.source || 'AI';
    const sourceLabel = source.startsWith('ai_') ? 'AI ' + source.replace('ai_', '').replace(/_/g, ' ') : source;
    // Determine if any action in batch needs PIN
    const needsPin = actions.some(a => ACTION_META[a.action_type]?.pin);
    return `<div class="card mb-3" style="border-left:3px solid var(--accent,#2563EB);padding:0;overflow:hidden">
      <div style="padding:12px 16px;background:var(--bg-2,#F1F5F9);display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap">
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          <span style="display:inline-flex;width:18px;height:18px;color:var(--accent-text,#2563EB)">${SVG.layers}</span>
          <strong>Batch ${esc(batchId)}</strong>
          <span class="chip chip-info chip-sm">${esc(sourceLabel)}</span>
          <span class="text-dim text-sm">${actions.length} action${actions.length === 1 ? '' : 's'}</span>
          ${pendingN > 0 ? `<span class="chip chip-warning chip-sm">${pendingN} pending</span>` : ''}
          ${executedN > 0 ? `<span class="chip chip-success chip-sm">${executedN} approved</span>` : ''}
          ${rejectedN > 0 ? `<span class="chip chip-danger chip-sm">${rejectedN} rejected</span>` : ''}
          ${needsPin && allPending ? `<span style="display:inline-flex;width:14px;height:14px;color:var(--warning-text,#D97706)" title="Some actions require manager PIN">${SVG.lock}</span>` : ''}
        </div>
        ${allPending ? `<div style="display:flex;gap:4px">
          <button class="btn btn-success btn-sm" data-batch-approve="${esc(batchId)}">
            <span style="display:inline-flex;width:12px;height:12px">${SVG.check}</span>
            Approve All
          </button>
          <button class="btn btn-danger btn-sm" data-batch-reject="${esc(batchId)}">
            <span style="display:inline-flex;width:12px;height:12px">${SVG.x}</span>
            Reject All
          </button>
        </div>` : ''}
      </div>
      <div style="padding:8px 12px">
        ${actions.map(a => renderCardInner(a, true)).join('')}
      </div>
    </div>`;
  }

  function renderCard(a, isNested = false) {
    return `<div class="${isNested ? '' : 'card mb-3'}" ${isNested ? 'style="padding:8px 12px;margin-bottom:4px;background:var(--bg-2,#F8FAFC);border-radius:6px"' : ''}>
      ${renderCardInner(a, false)}
    </div>`;
  }

  function renderCardInner(a, isNested) {
    const meta = ACTION_META[a.action_type] || { label: a.action_type.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()), icon: SVG.robot, pin: false, color: 'var(--text,#0F172A)' };
    const statusChip = a.status === 'pending'
      ? '<span class="chip chip-warning chip-sm">Pending</span>'
      : a.status === 'executed'
        ? '<span class="chip chip-success chip-sm">Approved</span>'
        : a.status === 'expired'
          ? '<span class="chip chip-danger chip-sm">Expired</span>'
          : '<span class="chip chip-danger chip-sm">Rejected</span>';
    // Expiry countdown for pending actions
    let expiryHtml = '';
    if (a.status === 'pending' && a.expires_at) {
      const expires = new Date(a.expires_at + 'Z');
      const now = new Date();
      const daysLeft = Math.ceil((expires - now) / (1000 * 60 * 60 * 24));
      if (daysLeft <= 0) {
        expiryHtml = `<div style="font-size:11px;color:var(--danger-text,#DC2626);margin-top:2px;display:flex;align-items:center;gap:4px">
          <span style="display:inline-flex;width:10px;height:10px">${SVG.clock}</span>
          <strong>Expired</strong> — pending auto-expiry
        </div>`;
      } else if (daysLeft <= 2) {
        expiryHtml = `<div style="font-size:11px;color:var(--warning-text,#D97706);margin-top:2px;display:flex;align-items:center;gap:4px">
          <span style="display:inline-flex;width:10px;height:10px">${SVG.clock}</span>
          Expires in <strong>${daysLeft} day${daysLeft === 1 ? '' : 's'}</strong>
        </div>`;
      } else {
        expiryHtml = `<div style="font-size:11px;color:var(--text-dim,#64748B);margin-top:2px;display:flex;align-items:center;gap:4px">
          <span style="display:inline-flex;width:10px;height:10px">${SVG.clock}</span>
          Expires in ${daysLeft} days
        </div>`;
      }
    }
    const payload = a.payload || {};
    // Build a human-readable "What" line from payload + action_type
    const whatLine = buildWhatLine(a.action_type, payload);
    return `<div style="${isNested ? '' : 'padding:16px'}">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;margin-bottom:8px">
        <div style="display:flex;gap:10px;align-items:flex-start;flex:1;min-width:0">
          <div style="width:32px;height:32px;background:var(--bg-2,#F1F5F9);color:${meta.color};border-radius:8px;display:flex;align-items:center;justify-content:center;flex-shrink:0">
            <span style="display:inline-flex;width:16px;height:16px">${meta.icon}</span>
          </div>
          <div style="flex:1;min-width:0">
            <div style="display:flex;gap:6px;align-items:center;margin-bottom:4px;flex-wrap:wrap">
              ${statusChip}
              <strong>${esc(meta.label)}</strong>
              ${meta.pin ? `<span style="display:inline-flex;width:14px;height:14px;color:var(--warning-text,#D97706);align-items:center" title="Requires manager PIN">${SVG.lock}</span>` : ''}
            </div>
            ${whatLine ? `<div style="font-size:13px;font-weight:600;margin-bottom:2px">${esc(whatLine)}</div>` : ''}
            <div class="text-dim text-sm">${esc(a.reason || 'No reason provided')}</div>
            ${expiryHtml}
          </div>
        </div>
        <div class="text-dim text-sm" style="text-align:right;flex-shrink:0">
          ${esc(fmtDate(a.created_at))}<br>
          <span style="font-size:11px">by ${esc(a.source || a.created_by || 'ai')}</span>
        </div>
      </div>
      ${a.impact_summary ? `<div style="padding:8px 12px;background:var(--bg-2,#F8FAFC);border-radius:6px;margin-bottom:8px;font-size:13px;border-left:3px solid var(--accent,#2563EB)">
        <strong style="color:var(--accent-text,#2563EB)">Impact:</strong> ${esc(a.impact_summary)}
      </div>` : ''}
      ${Object.keys(payload).length > 0 ? `<details style="margin-bottom:8px;font-size:12px;color:var(--text-dim,#64748B)">
        <summary style="cursor:pointer;padding:4px 0">Details (${Object.keys(payload).length} field${Object.keys(payload).length === 1 ? '' : 's'})</summary>
        <pre style="background:var(--bg-2,#F1F5F9);padding:8px 12px;border-radius:6px;margin-top:4px;overflow-x:auto;font-size:11px;line-height:1.4">${esc(JSON.stringify(payload, null, 2))}</pre>
      </details>` : ''}
      ${a.status === 'pending' ? `<div style="display:flex;gap:6px;flex-wrap:wrap">
        <button class="btn btn-success btn-sm" data-approve="${a.id}">
          <span style="display:inline-flex;width:12px;height:12px">${SVG.check}</span>
          Approve
        </button>
        <button class="btn btn-secondary btn-sm" data-edit="${a.id}">
          <span style="display:inline-flex;width:12px;height:12px">${SVG.edit}</span>
          Edit
        </button>
        <button class="btn btn-danger btn-sm" data-reject="${a.id}">
          <span style="display:inline-flex;width:12px;height:12px">${SVG.x}</span>
          Reject
        </button>
      </div>` : ''}
      ${a.status === 'executed' && a.approved_by ? `<div class="text-dim text-sm" style="margin-top:4px">
        Approved by ${esc(a.approved_by)} on ${esc(fmtDate(a.executed_at || ''))}${a.pin_verified ? ' (PIN verified)' : ''}
      </div>` : ''}
      ${a.status === 'expired' ? `<div class="text-dim text-sm" style="margin-top:4px;color:var(--danger-text,#DC2626)">
        Expired on ${esc(fmtDate(a.expires_at || ''))} — no action taken
      </div>` : ''}
    </div>`;
  }

  function buildWhatLine(actionType, payload) {
    // Build a concise one-line summary of WHAT the action will do
    try {
      if (actionType === 'apply_price_suggestion' && payload.category_id != null && payload.new_price != null) {
        return `Set price for category #${payload.category_id} to Rs ${Number(payload.new_price).toLocaleString()}`;
      }
      if (actionType === 'draft_purchase_order' && payload.category_id != null) {
        return `Purchase ${payload.qty || '?'} units of category #${payload.category_id}`;
      }
      if (actionType === 'confirm_bill' && payload.bill_id != null) {
        return `Confirm bill #${payload.bill_id}`;
      }
      if (actionType === 'draft_expense') {
        const amt = payload.amount != null ? `Rs ${Number(payload.amount).toLocaleString()}` : '';
        const cat = payload.category ? ` for ${payload.category}` : '';
        return `Record expense ${amt}${cat}`;
      }
      if (actionType === 'happy_hour_rule' && payload.pct != null) {
        return `${payload.pct}% off, ${payload.start_hhmm || '?'}–${payload.end_hhmm || '?'}`;
      }
      if (actionType === 'customer_broadcast' && payload.group) {
        return `Broadcast to "${payload.group}" group`;
      }
      if (actionType === 'draft_dead_stock_promo' && payload.category_id != null) {
        return `Promo on dead-stock category #${payload.category_id}`;
      }
      if (actionType === 'queue_whatsapp_reminder' && payload.customer_id != null) {
        return `WhatsApp reminder to customer #${payload.customer_id}`;
      }
    } catch {}
    return '';
  }

  function wireActionButtons() {
    // Individual approve
    document.querySelectorAll('[data-approve]').forEach(btn => {
      btn.onclick = async () => {
        const id = parseInt(btn.getAttribute('data-approve'));
        // Look up the action_type to decide if PIN is needed
        const action = await getAction(id);
        if (!action) return;
        const needsPin = ACTION_META[action.action_type]?.pin || action.action_type === 'apply_price_suggestion';
        if (needsPin) {
          openPinModal(id, action);
        } else {
          await approveAction(id, null);
        }
      };
    });
    // Edit (only pending)
    document.querySelectorAll('[data-edit]').forEach(btn => {
      btn.onclick = async () => {
        const id = parseInt(btn.getAttribute('data-edit'));
        const action = await getAction(id);
        if (!action) return;
        openEditModal(action);
      };
    });
    // Individual reject
    document.querySelectorAll('[data-reject]').forEach(btn => {
      btn.onclick = async () => {
        const id = parseInt(btn.getAttribute('data-reject'));
        if (!confirm('Reject this action? It will be discarded.')) return;
        try {
          await apiPost(`/api/pending-actions/${id}/reject`, {});
          toast('Action rejected', 'info');
          await loadActions();
          refreshPendingCount();
        } catch (e) { toast('Reject failed: ' + e.message, 'error'); }
      };
    });
    // Batch approve all
    document.querySelectorAll('[data-batch-approve]').forEach(btn => {
      btn.onclick = async () => {
        const batchId = btn.getAttribute('data-batch-approve');
        if (!confirm(`Approve ALL pending actions in batch ${batchId}?\n\nIf any action requires a manager PIN, you will be prompted.`)) return;
        // Get all pending actions in this batch
        const r = await api(`/api/pending-actions?status=pending&limit=100`);
        const batchActions = (r.actions || []).filter(a => a.batch_id === batchId);
        let approved = 0, failed = 0;
        for (const a of batchActions) {
          try {
            const needsPin = ACTION_META[a.action_type]?.pin || a.action_type === 'apply_price_suggestion';
            const pin = needsPin ? prompt(`Enter manager PIN for price change on action #${a.id}:`) : null;
            if (needsPin && !pin) { failed++; continue; }
            await apiPost(`/api/pending-actions/${a.id}/approve`, {
              approved_by: 'manager', manager_pin: pin,
            });
            approved++;
          } catch (e) { failed++; }
        }
        toast(`Approved ${approved}, failed ${failed}`, approved > 0 ? 'success' : 'error');
        await loadActions();
        refreshPendingCount();
      };
    });
    // Batch reject all
    document.querySelectorAll('[data-batch-reject]').forEach(btn => {
      btn.onclick = async () => {
        const batchId = btn.getAttribute('data-batch-reject');
        if (!confirm(`Reject ALL pending actions in batch ${batchId}?`)) return;
        const r = await api(`/api/pending-actions?status=pending&limit=100`);
        const batchActions = (r.actions || []).filter(a => a.batch_id === batchId);
        for (const a of batchActions) {
          try { await apiPost(`/api/pending-actions/${a.id}/reject`, {}); } catch {}
        }
        toast('Batch rejected', 'info');
        await loadActions();
        refreshPendingCount();
      };
    });
  }

  async function getAction(id) {
    try {
      const r = await api(`/api/pending-actions?status=&limit=100`);
      return (r.actions || []).find(a => a.id === id);
    } catch { return null; }
  }

  function openPinModal(actionId, action) {
    const meta = ACTION_META[action.action_type] || { label: 'this action' };
    openModal('Manager PIN Required', `
      <div style="display:flex;gap:12px;align-items:center;margin-bottom:16px">
        <div style="width:36px;height:36px;background:var(--bg-warning-soft,#FEF3C7);color:var(--warning-text,#D97706);border-radius:10px;display:flex;align-items:center;justify-content:center">
          <span style="display:inline-flex;width:18px;height:18px">${SVG.lock}</span>
        </div>
        <div>
          <strong>${esc(meta.label)} — PIN Required</strong>
          <div class="text-dim text-sm">Enter manager PIN to approve this action</div>
        </div>
      </div>
      <div class="form-group">
        <input class="input" id="aq-pin-input" type="password" inputmode="numeric" maxlength="8" placeholder="Manager PIN" autofocus>
      </div>`,
      `<button class="btn" data-close>Cancel</button>
       <button class="btn btn-primary" id="aq-pin-confirm">Confirm & Approve</button>`);
    const input = $('#aq-pin-input');
    input.focus();
    input.onkeydown = (e) => { if (e.key === 'Enter') $('#aq-pin-confirm').click(); };
    $('#aq-pin-confirm').onclick = async () => {
      const pin = input.value.trim();
      if (!pin) { toast('Enter a PIN', 'error'); return; }
      closeModal();
      await approveAction(actionId, pin);
    };
  }

  function openEditModal(action) {
    const payload = action.payload || {};
    const payloadStr = JSON.stringify(payload, null, 2);
    openModal(`Edit Action #${action.id}`, `
      <div class="text-dim text-sm" style="margin-bottom:12px">
        Adjust the reason, impact, or payload before approving. The action will not execute until you approve it.
      </div>
      <div class="form-group">
        <label class="text-sm"><strong>Reason</strong></label>
        <textarea class="input" id="aq-edit-reason" rows="2" style="margin-top:4px">${esc(action.reason || '')}</textarea>
      </div>
      <div class="form-group">
        <label class="text-sm"><strong>Impact Summary</strong></label>
        <textarea class="input" id="aq-edit-impact" rows="2" style="margin-top:4px">${esc(action.impact_summary || '')}</textarea>
      </div>
      <div class="form-group">
        <label class="text-sm"><strong>Payload (JSON)</strong></label>
        <textarea class="input" id="aq-edit-payload" rows="6" style="margin-top:4px;font-family:monospace;font-size:12px">${esc(payloadStr)}</textarea>
      </div>`,
      `<button class="btn" data-close>Cancel</button>
       <button class="btn btn-primary" id="aq-edit-save">Save Changes</button>`);
    $('#aq-edit-save').onclick = async () => {
      const reason = $('#aq-edit-reason').value.trim();
      const impact = $('#aq-edit-impact').value.trim();
      const payloadRaw = $('#aq-edit-payload').value.trim();
      let payloadObj;
      try {
        payloadObj = payloadRaw ? JSON.parse(payloadRaw) : {};
      } catch (e) {
        toast('Invalid JSON payload: ' + e.message, 'error');
        return;
      }
      try {
        await apiPut(`/api/pending-actions/${action.id}`, {
          payload: payloadObj, reason, impact_summary: impact,
        });
        toast('Action updated', 'success');
        closeModal();
        await loadActions();
        refreshPendingCount();
      } catch (e) {
        toast('Save failed: ' + e.message, 'error');
      }
    };
  }

  async function approveAction(id, pin) {
    try {
      const r = await apiPost(`/api/pending-actions/${id}/approve`, {
        approved_by: 'manager',
        manager_pin: pin,
      });
      toast('Action approved and executed', 'success');
      await loadActions();
      refreshPendingCount();
    } catch (e) {
      toast('Approve failed: ' + e.message, 'error');
    }
  }
});

// Wire to global badge refresh interval (if shell loads it)
if (typeof window !== 'undefined') {
  // Refresh badge on page show (handles browser back)
  window.addEventListener('pageshow', () => { refreshPendingCount(); });
}
