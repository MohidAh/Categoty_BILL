// v7.2 Phase 5 — AI Automations settings page
// Dedicated page for AI automation toggles + season-prep trigger.
// All toggles OFF by default (hard constraint: no surprise automation).

import { route, navigate } from '../router.js';
import { api, apiPost } from '../api.js';
import { $, esc, toast, openModal, closeModal, skeletonCards, errorBox } from '../utils.js';

const SVG = {
  brain: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96.44 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24 2.5 2.5 0 0 1 1.98-3A2.5 2.5 0 0 1 9.5 2Z"/><path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96.44 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-1.98-3A2.5 2.5 0 0 0 14.5 2Z"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>',
  power: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18.36 6.64a9 9 0 1 1-12.73 0"/><line x1="12" y1="2" x2="12" y2="12"/></svg>',
  sparkles: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1"/></svg>',
  alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>',
  cart: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="9" cy="21" r="1"/><circle cx="20" cy="21" r="1"/><path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6"/></svg>',
  bell: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>',
  receipt: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21V5a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v16l4-3 4 3 4-3 4 3z"/></svg>',
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>',
  calendar: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>',
  tag: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg>',
  shield: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>',
};

// Friendly metadata for each automation key — drives the UI labels + descriptions
const AUTOMATIONS = [
  {
    key: 'auto_confirm_bills',
    label: 'Auto-confirm low-risk bills',
    description: 'Bills with no flags and matching totals get auto-confirmed. Level 3 (bounded).',
    icon: SVG.receipt,
    level: 3,
  },
  {
    key: 'auto_draft_po',
    label: 'Auto-draft purchase orders',
    description: 'When stock drops below reorder, draft a PO into the Approval Queue (never sends automatically).',
    icon: SVG.cart,
    level: 2,
  },
  {
    key: 'urdhaar_reminders',
    label: 'Urdhaar (credit) reminders',
    description: 'Queue WhatsApp reminders for customers with outstanding credit older than 7 days.',
    icon: SVG.bell,
    level: 1,
  },
  {
    key: 'recurring_detection',
    label: 'Recurring expense detection',
    description: 'Detect expenses that appear 2+ months with same description+amount — surface in Insights.',
    icon: SVG.calendar,
    level: 1,
  },
  {
    key: 'expense_categorization',
    label: 'Auto-categorize expenses',
    description: 'AI suggests category for uncategorized expenses (drafts into Approval Queue).',
    icon: SVG.receipt,
    level: 2,
  },
  {
    key: 'anomaly_diagnosis',
    label: 'Anomaly diagnosis',
    description: 'When a sale or expense looks unusual (z-score > 2), queue an AI explanation.',
    icon: SVG.search,
    level: 1,
  },
  {
    key: 'variance_investigation',
    label: 'Shift variance investigation',
    description: 'When a shift ends with cash variance > Rs 500, queue an AI investigation summary.',
    icon: SVG.alert,
    level: 1,
  },
  {
    key: 'scheduled_reports',
    label: 'Scheduled reports',
    description: 'Email/WhatsApp daily summary at 9 PM and weekly P&L every Monday (queued, not auto-sent).',
    icon: SVG.calendar,
    level: 1,
  },
  {
    key: 'dead_stock_liquidation',
    label: 'Dead-stock liquidation',
    description: 'Identify dead stock (>90 days no sale) and queue promotional pricing drafts.',
    icon: SVG.tag,
    level: 2,
  },
];

const SEASONS = [
  'Eid ul Fitr', 'Eid ul Adha', 'Ramazan', 'Basant', 'Wedding Season',
  'Back to School', 'Winter Sale', 'Summer Sale', 'Black Friday', 'New Year',
];

route('/settings/ai-automations', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-pink">${SVG.brain}</div>
      <div>
        <h2 class="pos-page-header-title">AI Automations</h2>
        <p class="pos-page-header-sub">All automations are OFF by default. Toggle them on individually — every action still goes through the Approval Queue.</p>
      </div>
    </div>
    <div id="aiu-out">${skeletonCards(3)}</div>`;

  await loadData();

  async function loadData() {
    try {
      const [config, killStatus] = await Promise.all([
        api('/api/automation-config'),
        api('/api/ai/kill-switch'),
      ]);
      renderPage(config, killStatus);
    } catch (e) {
      $('#aiu-out').innerHTML = errorBox(e.message);
    }
  }

  function renderPage(config, killStatus) {
    const configMap = {};
    for (const c of (config.config || [])) {
      configMap[c.key] = c;
    }
    const isKilled = !!killStatus.disabled;

    let togglesHtml = '';
    let anyL3Enabled = false;
    for (const auto of AUTOMATIONS) {
      const c = configMap[auto.key] || { enabled: 0, level: auto.level };
      const isEnabled = c.enabled === 1;
      if (isEnabled && auto.level === 3) anyL3Enabled = true;
      const isLocked = isKilled; // Kill switch disables all toggles visually
      // Level-3 specific warning (only shown when L3 + enabled)
      const l3Warning = (auto.level === 3 && isEnabled) ? `
        <div style="margin-top:6px;padding:6px 10px;background:var(--danger-soft,#FEE2E2);border:1px solid var(--danger,#DC2626);border-radius:6px;font-size:12px;color:var(--danger-text,#DC2626);display:flex;gap:6px;align-items:center">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.alert}</span>
          <strong>Level 3 — bounded auto-execute.</strong> This automation will execute actions without your approval. Disable if unsure.
        </div>` : '';
      // Level-3 chip color differs from L1/L2 to flag the higher risk
      const levelChipClass = auto.level === 3 ? 'chip-danger' : (auto.level === 2 ? 'chip-warning' : 'chip-info');
      togglesHtml += `<div class="card" style="padding:16px;margin-bottom:8px;${isLocked ? 'opacity:0.6' : ''}${auto.level === 3 && isEnabled ? 'border:1px solid var(--danger,#DC2626)' : ''}">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px">
          <div style="display:flex;gap:12px;align-items:flex-start;flex:1;min-width:0">
            <div style="width:36px;height:36px;background:var(--bg-2,#F1F5F9);color:${auto.level === 3 ? 'var(--danger-text,#DC2626)' : 'var(--accent-text,#2563EB)'};border-radius:8px;display:flex;align-items:center;justify-content:center;flex-shrink:0">
              <span style="display:inline-flex;width:18px;height:18px">${auto.icon}</span>
            </div>
            <div style="flex:1;min-width:0">
              <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
                <strong>${esc(auto.label)}</strong>
                <span class="chip ${levelChipClass} chip-sm" title="${auto.level === 3 ? 'Bounded auto-execute' : auto.level === 2 ? 'Drafts into queue' : 'Read-only insights'}">Level ${auto.level}</span>
                ${isEnabled ? '<span class="chip chip-success chip-sm">ON</span>' : '<span class="chip chip-warning chip-sm">OFF</span>'}
              </div>
              <div class="text-dim text-sm" style="margin-top:4px">${esc(auto.description)}</div>
              <div class="text-dim text-sm" style="margin-top:4px;font-size:11px"><code>${esc(auto.key)}</code></div>
              ${l3Warning}
            </div>
          </div>
          <label style="display:flex;align-items:center;cursor:${isLocked ? 'not-allowed' : 'pointer'};flex-shrink:0">
            <input type="checkbox" data-config-key="${esc(auto.key)}" data-level="${auto.level}" ${isEnabled ? 'checked' : ''} ${isLocked ? 'disabled' : ''} style="width:20px;height:20px;cursor:${isLocked ? 'not-allowed' : 'pointer'}">
          </label>
        </div>
      </div>`;
    }

    $('#aiu-out').innerHTML = `
      ${isKilled ? `<div class="card" style="padding:12px;background:var(--danger-soft,#FEE2E2);border:1px solid var(--danger,#DC2626);margin-bottom:16px;display:flex;gap:12px;align-items:center">
        <span style="display:inline-flex;width:20px;height:20px;color:var(--danger-text,#DC2626)">${SVG.power}</span>
        <div style="flex:1">
          <strong style="color:var(--danger-text,#DC2626)">AI Kill Switch is ON</strong> — all automations are paused.
          <span class="text-sm" style="color:var(--danger-text,#DC2626)">Toggle the kill switch off on the <a href="#/insights/ai-usage" style="color:inherit;text-decoration:underline">AI Usage page</a> to re-enable.</span>
        </div>
      </div>` : ''}
      ${anyL3Enabled && !isKilled ? `<div class="card" style="padding:12px;background:var(--bg-warning-soft,#FEF3C7);border:1px solid var(--warning,#D97706);margin-bottom:16px;display:flex;gap:12px;align-items:center">
        <span style="display:inline-flex;width:20px;height:20px;color:var(--warning-text,#D97706)">${SVG.alert}</span>
        <div style="flex:1">
          <strong style="color:var(--warning-text,#D97706)">Level 3 automation active</strong> — one or more automations will execute actions without your approval.
          <span class="text-sm" style="color:var(--warning-text,#D97706)">Disable Level 3 automations if you want every action to go through the Approval Queue.</span>
        </div>
      </div>` : ''}

      <!-- Season prep trigger -->
      <div class="card" style="padding:16px;margin-bottom:16px;border-left:3px solid var(--accent,#2563EB)">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap">
          <div style="display:flex;gap:12px;align-items:center">
            <div style="width:36px;height:36px;background:var(--accent-soft,#DBEAFE);color:var(--accent-text,#2563EB);border-radius:8px;display:flex;align-items:center;justify-content:center">
              <span style="display:inline-flex;width:18px;height:18px">${SVG.sparkles}</span>
            </div>
            <div>
              <strong>Prepare for a season</strong>
              <div class="text-dim text-sm">Multi-step agent: drafts POs for low-stock items, a happy-hour rule, and a customer broadcast — all grouped in the Approval Queue.</div>
            </div>
          </div>
          <button class="btn btn-primary" id="season-prep-btn" ${isKilled ? 'disabled' : ''}>
            <span style="display:inline-flex;width:14px;height:14px">${SVG.sparkles}</span>
            Prepare for Season
          </button>
        </div>
      </div>

      <h3 style="margin:16px 0 8px">Automation Toggles</h3>
      ${togglesHtml}

      <div class="card" style="padding:12px;background:var(--bg-2,#F1F5F9);margin-top:16px;display:flex;gap:8px;align-items:flex-start">
        <span style="display:inline-flex;width:16px;height:16px;color:var(--text-dim,#64748B);flex-shrink:0;margin-top:2px">${SVG.shield}</span>
        <div class="text-sm text-dim">
          <strong>How automations work:</strong> When enabled, the AI runs in the background and drafts actions
          into the <a href="#/insights/approval-queue" style="color:inherit;text-decoration:underline">Approval Queue</a>.
          Nothing executes without your explicit approval. Price changes additionally require a manager PIN.
          Level 1 = read-only insights, Level 2 = drafts into queue, Level 3 = bounded auto-execute.
        </div>
      </div>`;

    // Wire toggles
    document.querySelectorAll('[data-config-key]').forEach(cb => {
      cb.onchange = async () => {
        const key = cb.getAttribute('data-config-key');
        const level = parseInt(cb.getAttribute('data-level') || '1', 10);
        // Level-3 confirmation: requires explicit OK
        if (cb.checked && level === 3) {
          const ok = confirm(
            `WARNING: You are enabling a Level 3 automation.\n\n` +
            `Level 3 = bounded auto-execute. This automation will perform actions ` +
            `WITHOUT your explicit approval. Only enable if you understand the risk.\n\n` +
            `Click OK to enable, or Cancel to keep it off.`
          );
          if (!ok) {
            cb.checked = false;
            return;
          }
        }
        try {
          await apiPost(`/api/automation-config/${key}`, {
            enabled: cb.checked ? 1 : 0, level: cb.checked ? level : 1,
          });
          toast(`${key} ${cb.checked ? 'enabled' : 'disabled'}`, 'success');
          // Re-render to show/hide L3 warning
          await loadData();
        } catch (e) {
          toast('Toggle failed: ' + e.message, 'error');
          cb.checked = !cb.checked;
        }
      };
    });

    // Wire season prep button
    const btn = $('#season-prep-btn');
    if (btn) {
      btn.onclick = () => openSeasonPrepModal();
    }
  }

  function openSeasonPrepModal() {
    const seasonOptions = SEASONS.map(s => `<option value="${esc(s)}">${esc(s)}</option>`).join('');
    openModal('Prepare for a Season', `
      <div class="text-dim text-sm" style="margin-bottom:12px">
        The agent will draft multiple actions (POs for low stock, a happy-hour rule, a customer broadcast)
        into a single batch in your Approval Queue. You can review, edit, and approve each one individually.
      </div>
      <div class="form-group">
        <label class="text-sm"><strong>Season</strong></label>
        <select class="input" id="season-prep-select" style="margin-top:4px">
          ${seasonOptions}
        </select>
      </div>
      <div class="form-group">
        <label class="text-sm"><strong>Or enter a custom season name</strong></label>
        <input class="input" id="season-prep-custom" placeholder="e.g. Eid ul Fitr 2026" style="margin-top:4px">
      </div>`,
      `<button class="btn" data-close>Cancel</button>
       <button class="btn btn-primary" id="season-prep-confirm">
         <span style="display:inline-flex;width:14px;height:14px">${SVG.sparkles}</span>
         Prepare
       </button>`);
    $('#season-prep-confirm').onclick = async () => {
      const select = $('#season-prep-select').value;
      const custom = $('#season-prep-custom').value.trim();
      const season = custom || select;
      if (!season) { toast('Pick or enter a season', 'error'); return; }
      try {
        const r = await apiPost('/api/agent/prepare-season', { season });
        toast(`Prepared ${r.pending_count} actions for ${season}`, 'success');
        closeModal();
        // Navigate to the Approval Queue so the user can see the new batch
        navigate('/insights/approval-queue');
      } catch (e) {
        toast('Season prep failed: ' + e.message, 'error');
      }
    };
  }
});
