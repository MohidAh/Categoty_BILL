// v8.0 Phase 1 — Settings → Branch page
// Single-shop must stay identical: with role='branch' + empty hub_url (the defaults),
// the app behaves EXACTLY as v7.2. This page lets the owner configure branch identity
// for multi-store sync (Phase 2+), but it is purely opt-in.
import { route } from '../router.js';
import { api, apiPut } from '../api.js';
import { $, esc, toast, openModal, closeModal, skeletonCards, errorBox } from '../utils.js';

const SVG = {
  branch: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 3v12"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/></svg>',
  store: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 9l1-5h16l1 5"/><path d="M4 9v11a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1V9"/><line x1="9" y1="13" x2="15" y2="13"/></svg>',
  hq: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 21h18"/><path d="M5 21V7l8-4v18"/><path d="M19 21V11l-6-4"/><line x1="9" y1="9" x2="9" y2="9.01"/><line x1="9" y1="12" x2="9" y2="12.01"/><line x1="9" y1="15" x2="9" y2="15.01"/><line x1="9" y1="18" x2="9" y2="18.01"/></svg>',
  link: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>',
  shield: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>',
  info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>',
  save: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>',
  refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>',
};

route('/settings/branch', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-info">${SVG.branch}</div>
      <div>
        <h2 class="pos-page-header-title">Branch Settings</h2>
        <p class="pos-page-header-sub">Configure this instance for multi-store sync. Single-shop mode requires no changes here.</p>
      </div>
    </div>
    <div id="branch-out">${skeletonCards(2)}</div>`;

  await loadConfig();

  async function loadConfig() {
    try {
      const cfg = await api('/api/branch-config');
      renderConfig(cfg);
    } catch (e) {
      $('#branch-out').innerHTML = errorBox(e.message);
    }
  }

  function renderConfig(cfg) {
    const isHQ = cfg.role === 'hq';
    const isSingleShop = !cfg.hub_url && !isHQ;
    $('#branch-out').innerHTML = `
      ${isSingleShop ? `<div class="card" style="padding:12px;background:var(--bg-2,#F1F5F9);margin-bottom:16px;display:flex;gap:12px;align-items:center">
        <span style="display:inline-flex;width:20px;height:20px;color:var(--text-dim,#64748B)">${SVG.info}</span>
        <div style="flex:1;font-size:13px">
          <strong>Single-shop mode.</strong> This instance behaves exactly as BillBook v7.2 — no sync attempts, no hub dependency.
          Fill in the form below only when you are ready to enable multi-store sync (v8.0+).
        </div>
      </div>` : ''}

      ${isHQ ? `<div class="card" style="padding:12px;background:var(--accent-soft,#DBEAFE);border:1px solid var(--accent,#2563EB);margin-bottom:16px;display:flex;gap:12px;align-items:center">
        <span style="display:inline-flex;width:20px;height:20px;color:var(--accent-text,#2563EB)">${SVG.hq}</span>
        <div style="flex:1">
          <strong style="color:var(--accent-text,#2563EB)">Headquarters role active.</strong>
          <span class="text-sm" style="color:var(--accent-text,#2563EB)">This instance aggregates summaries from all branches and hosts the Owner Hub dashboard.</span>
        </div>
      </div>` : ''}

      <div class="card" style="padding:16px;margin-bottom:16px">
        <h3 style="margin:0 0 12px">Branch Identity</h3>
        <div class="grid grid-2" style="gap:12px;margin-bottom:12px">
          <div class="form-group">
            <label class="text-sm"><strong>Branch Name</strong></label>
            <input class="input" id="branch-name" value="${esc(cfg.branch_name || 'Main Shop')}" style="margin-top:4px">
          </div>
          <div class="form-group">
            <label class="text-sm"><strong>Region</strong></label>
            <input class="input" id="branch-region" value="${esc(cfg.region || '')}" placeholder="e.g. Lahore, Karachi" style="margin-top:4px">
          </div>
        </div>
        <div class="grid grid-2" style="gap:12px;margin-bottom:12px">
          <div class="form-group">
            <label class="text-sm"><strong>Role</strong></label>
            <select class="input" id="branch-role" style="margin-top:4px">
              <option value="branch" ${cfg.role === 'branch' ? 'selected' : ''}>Branch (independent shop)</option>
              <option value="hq" ${cfg.role === 'hq' ? 'selected' : ''}>Headquarters (aggregation hub)</option>
            </select>
          </div>
          <div class="form-group">
            <label class="text-sm"><strong>Branch ID</strong> <span class="text-dim text-sm">(auto-generated)</span></label>
            <input class="input" id="branch-id" value="${esc(cfg.branch_id || '')}" readonly style="margin-top:4px;background:var(--bg-2,#F1F5F9)">
          </div>
        </div>
      </div>

      <div class="card" style="padding:16px;margin-bottom:16px">
        <h3 style="margin:0 0 12px">Hub Connection <span class="text-dim text-sm" style="font-weight:normal">(branch role only)</span></h3>
        <div class="form-group" style="margin-bottom:12px">
          <label class="text-sm"><strong>Hub URL</strong> <span class="text-dim text-sm">(Cloudflare Tunnel URL of your HQ instance)</span></label>
          <input class="input" id="branch-hub-url" value="${esc(cfg.hub_url || '')}" placeholder="https://billbook-hq.yourdomain.com" style="margin-top:4px">
        </div>
        <div class="form-group" style="margin-bottom:12px">
          <label class="text-sm"><strong>Sync Token</strong> <span class="text-dim text-sm">(issued by HQ during registration)</span></label>
          <input class="input" id="branch-sync-token" type="password" placeholder="${cfg.has_sync_token ? '•••••••• (set — leave blank to keep)' : 'Not set yet'}" style="margin-top:4px">
        </div>
        <div class="card" style="padding:12px;background:var(--bg-2,#F1F5F9);border:1px dashed var(--border,#E2E8F0)">
          <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap">
            <div>
              <strong style="font-size:13px">Register with HQ</strong>
              <div class="text-dim text-sm">If your HQ gave you a 6-digit registration code, enter it here to auto-fetch your sync token.</div>
            </div>
            <button class="btn btn-secondary btn-sm" id="branch-register-btn">
              <span style="display:inline-flex;width:14px;height:14px">${SVG.link}</span>
              Register with Code
            </button>
          </div>
        </div>
      </div>

      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <button class="btn btn-primary" id="branch-save">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.save}</span>
          Save Branch Settings
        </button>
        <button class="btn btn-secondary" id="branch-refresh">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.refresh}</span>
          Reload
        </button>
      </div>

      <div class="card" style="padding:12px;background:var(--bg-2,#F1F5F9);margin-top:16px;display:flex;gap:8px;align-items:flex-start">
        <span style="display:inline-flex;width:16px;height:16px;color:var(--text-dim,#64748B);flex-shrink:0;margin-top:2px">${SVG.shield}</span>
        <div class="text-sm text-dim">
          <strong>How branch identity works:</strong>
          <ul style="margin:4px 0 0 16px;padding:0">
            <li><strong>Single-shop (default):</strong> role='branch' + empty Hub URL = no sync, no friction, identical to v7.2.</li>
            <li><strong>Branch role:</strong> sells locally, pushes daily summaries to HQ, sends/receives transfer challans.</li>
            <li><strong>HQ role:</strong> maintains the branch registry, hosts the Owner Hub dashboard, routes price pushes and central purchases.</li>
            <li>The sync token is stored as a SHA-256 hash — never plaintext. Re-issue from HQ if lost.</li>
          </ul>
        </div>
      </div>`;

    $('#branch-save').onclick = async () => {
      const payload = {
        role: $('#branch-role').value,
        branch_name: $('#branch-name').value.trim() || 'Main Shop',
        region: $('#branch-region').value.trim(),
        hub_url: $('#branch-hub-url').value.trim(),
        sync_token: $('#branch-sync-token').value,
        branch_id: cfg.branch_id,
      };
      try {
        const r = await apiPut('/api/branch-config', payload);
        toast(`Branch settings saved (branch_id: ${r.branch_id})`, 'success');
        await loadConfig();
      } catch (e) {
        toast('Save failed: ' + e.message, 'error');
      }
    };
    $('#branch-refresh').onclick = loadConfig;
    $('#branch-register-btn').onclick = () => openRegisterModal(cfg);

    function openRegisterModal(currentCfg) {
      // Ensure we have a branch_id before registering — save first if missing
      if (!currentCfg.branch_id) {
        toast('Save your branch settings first to generate a branch_id', 'error');
        return;
      }
      openModal('Register with HQ', `
        <div class="text-dim text-sm" style="margin-bottom:12px">
          Enter the 6-digit code your HQ gave you. We'll fetch a sync token and store it
          (hashed) in your branch config. Your branch_id is
          <code style="background:var(--bg-2,#F1F5F9);padding:1px 4px;border-radius:3px">${esc(currentCfg.branch_id)}</code>.
        </div>
        <div class="form-group">
          <label class="text-sm"><strong>Hub URL</strong> <span class="text-dim text-sm">(must be set first)</span></label>
          <input class="input" id="reg-hub-url" value="${esc(currentCfg.hub_url || '')}" placeholder="https://billbook-hq.yourdomain.com" style="margin-top:4px">
        </div>
        <div class="form-group">
          <label class="text-sm"><strong>Registration Code (6 digits)</strong></label>
          <input class="input" id="reg-code" inputmode="numeric" maxlength="6" placeholder="123456" style="margin-top:4px;letter-spacing:4px;font-family:monospace;text-align:center">
        </div>
        <div class="form-group" style="margin-bottom:0">
          <label class="text-sm"><strong>Your Branch's Tunnel URL</strong> <span class="text-dim text-sm">(so HQ can push to you)</span></label>
          <input class="input" id="reg-tunnel-url" placeholder="https://billbook-branch-a.trycloudflare.com" style="margin-top:4px">
        </div>`,
        `<button class="btn" data-close>Cancel</button>
         <button class="btn btn-primary" id="reg-confirm">Register</button>`);
      $('#reg-confirm').onclick = async () => {
        const hubUrl = $('#reg-hub-url').value.trim().replace(/\/+$/, '');
        const code = $('#reg-code').value.trim();
        const tunnelUrl = $('#reg-tunnel-url').value.trim();
        if (!hubUrl) { toast('Enter Hub URL', 'error'); return; }
        if (!code || code.length !== 6) { toast('Code must be 6 digits', 'error'); return; }
        try {
          // 1. Save the hub_url locally first
          await apiPut('/api/branch-config', {
            role: currentCfg.role, branch_name: currentCfg.branch_name,
            region: currentCfg.region, hub_url: hubUrl,
            branch_id: currentCfg.branch_id,
          });
          // 2. Call HQ's registration endpoint
          const r = await fetch(`${hubUrl}/api/hq/branches/register`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              code, branch_name: currentCfg.branch_name, region: currentCfg.region,
              branch_id: currentCfg.branch_id, tunnel_url: tunnelUrl,
            }),
          });
          if (!r.ok) {
            const err = await r.json().catch(() => ({}));
            throw new Error(err.error || err.detail || `HTTP ${r.status}`);
          }
          const result = await r.json();
          // 3. Store the issued token locally
          await apiPut('/api/branch-config', {
            role: currentCfg.role, branch_name: currentCfg.branch_name,
            region: currentCfg.region, hub_url: hubUrl,
            sync_token: result.token, branch_id: currentCfg.branch_id,
          });
          toast('Registered with HQ — sync token stored', 'success');
          closeModal();
          await loadConfig();
        } catch (e) {
          toast('Registration failed: ' + e.message, 'error');
        }
      };
    }
  }
});
