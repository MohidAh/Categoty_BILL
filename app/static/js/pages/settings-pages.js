// Settings app — General, Tax & SMS, Backups, Appearance
// Part 1 of Settings app (split for file size). Part 2: settings-staff.js (Employees + Security).
import { route, navigate, reload } from '../router.js';
import { api, apiPost, apiPut, apiDelete } from '../api.js';
import { $, $$, esc, fmt, fmtRs, toast, showLoading, hideLoading,
         openModal, closeModal, skeletonCards, errorBox, emptyState, icon, iconHtml,
         applyAppearance, cacheAppearance, APPEARANCE_DEFAULTS, APPEARANCE_ACCENT_PRESETS } from '../utils.js';

const SVG = {
  settings: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/></svg>',
  store: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 9l1-5h16l1 5"/><path d="M4 9v11a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1V9"/><line x1="9" y1="13" x2="15" y2="13"/></svg>',
  brain: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96.44 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24 2.5 2.5 0 0 1 1.98-3A2.5 2.5 0 0 1 9.5 2Z"/><path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96.44 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-1.98-3A2.5 2.5 0 0 0 14.5 2Z"/></svg>',
  backup: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/></svg>',
  plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>',
  save: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>',
  edit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>',
  alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
  sun: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>',
  moon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>',
  download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>',
  upload: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>',
  file: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>',
};

// ═══════════════════════════════════════════════════
// GENERAL — categories + AI providers + extraction accuracy
// ═══════════════════════════════════════════════════
route('/settings', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon">${SVG.settings}</div>
      <div>
        <h2 class="pos-page-header-title">General Settings</h2>
        <p class="pos-page-header-sub">Price categories, AI providers, and extraction accuracy.</p>
      </div>
    </div>
    <div id="set-out">${skeletonCards(3)}</div>`;

  let cats, providers, accuracy;
  try {
    [cats, providers, accuracy] = await Promise.all([
      api('/api/categories'),
      api('/api/providers'),
      api('/api/accuracy'),
    ]);
  } catch (e) {
    $('#set-out').innerHTML = errorBox(e.message);
    return;
  }

  $('#set-out').innerHTML = `
    <div class="card mb-4">
      <div class="card-title">
        <h3>
          <span style="display:inline-flex;width:18px;height:18px;vertical-align:-3px;margin-right:6px">${SVG.store}</span>
          Price Categories
        </h3>
        <button class="btn btn-secondary btn-sm" id="cat-add-btn">
          <span style="display:inline-flex;width:12px;height:12px">${SVG.plus}</span>
          Add
        </button>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Code</th><th>Name</th><th class="table-num">Sell Price</th><th>Color</th><th class="table-num">Items</th><th>Actions</th></tr></thead>
          <tbody>${cats.map(c => `<tr>
            <td><span class="badge badge-accent font-mono">${esc(c.code || '—')}</span></td>
            <td class="font-semibold">${esc(c.name)}</td>
            <td class="table-num">Rs ${fmt(c.sell_price)}</td>
            <td><span class="tag-chip" style="background:${esc(c.color)}">${esc(c.color)}</span></td>
            <td class="table-num">${c.item_count}</td>
            <td>
              <button class="btn btn-ghost btn-sm btn-icon" data-cat-edit="${c.id}" title="Edit">${SVG.edit}</button>
              <button class="btn btn-ghost btn-sm btn-icon" data-cat-delete="${c.id}" title="Delete">${SVG.trash}</button>
            </td>
          </tr>`).join('')}</tbody>
        </table>
      </div>
    </div>

    <div class="card mb-4">
      <div class="card-title">
        <h3>
          <span style="display:inline-flex;width:18px;height:18px;vertical-align:-3px;margin-right:6px">${SVG.brain}</span>
          AI Providers
        </h3>
        <button class="btn btn-secondary btn-sm" id="prov-add-btn">
          <span style="display:inline-flex;width:12px;height:12px">${SVG.plus}</span>
          Add Provider
        </button>
      </div>
      <p class="text-sm text-dim mb-4">Providers are tried in priority order. Add a free Gemini key from <a href="https://aistudio.google.com/apikey" target="_blank">Google AI Studio</a> for bill extraction.</p>
      ${providers.length ? `
        <div class="table-wrap">
          <table>
            <thead><tr><th>Name</th><th>Type</th><th>Model</th><th>Key</th><th class="table-num">Priority</th><th>Actions</th></tr></thead>
            <tbody>${providers.map(p => `<tr>
              <td class="font-semibold">${esc(p.name)}</td>
              <td><span class="badge badge-accent">${esc(p.provider_type)}</span></td>
              <td class="text-sm">${esc(p.model || '—')}</td>
              <td><code>${esc(p.key_preview)}</code></td>
              <td class="table-num">${p.priority}</td>
              <td>
                <div class="flex gap-2">
                  <button class="btn btn-secondary btn-sm" data-prov-test="${p.id}">${SVG.check} Test</button>
                  <button class="btn btn-ghost btn-sm btn-icon" data-prov-edit="${p.id}" title="Edit">${SVG.edit}</button>
                  <button class="btn btn-ghost btn-sm btn-icon" data-prov-delete="${p.id}" title="Delete">${SVG.trash}</button>
                </div>
              </td>
            </tr>`).join('')}</tbody>
          </table>
        </div>` : '<p class="text-dim">No providers configured. Add one to enable AI extraction.</p>'}
    </div>

    <div class="card">
      <h3>Extraction Accuracy</h3>
      <p class="text-sm text-dim mt-2">How accurate AI extraction is, based on your manual corrections.</p>
      <div class="grid grid-3 mt-3">
        <div class="stat-card">
          <div class="stat-card-icon chip-success">${SVG.check}</div>
          <div class="stat-card-label">Accuracy</div>
          <div class="stat-card-value ${accuracy.accuracy === null ? 'text-dim' : 'text-success'}">${accuracy.accuracy !== null ? (accuracy.accuracy * 100).toFixed(1) + '%' : 'N/A'}</div>
        </div>
        <div class="stat-card">
          <div class="stat-card-icon chip-primary">${SVG.edit}</div>
          <div class="stat-card-label">Corrections</div>
          <div class="stat-card-value">${accuracy.corrected || 0}</div>
        </div>
        <div class="stat-card">
          <div class="stat-card-icon chip-info">${SVG.file}</div>
          <div class="stat-card-label">Fields Extracted</div>
          <div class="stat-card-value">${accuracy.fields || 0}</div>
        </div>
      </div>
    </div>`;

  // Wire up category buttons
  $('#cat-add-btn').onclick = () => openCatModal(null, cats);
  $$('[data-cat-edit]').forEach(b => b.onclick = () => {
    const id = parseInt(b.dataset.catEdit);
    const c = cats.find(x => x.id === id);
    openCatModal(id, cats, c);
  });
  $$('[data-cat-delete]').forEach(b => b.onclick = async () => {
    if (!confirm('Delete this category? Items using it will be uncategorized.')) return;
    try { await apiDelete(`/api/categories/${b.dataset.catDelete}`); toast('Deleted', 'success'); reload(); }
    catch (e) { toast('Error: ' + e.message, 'error'); }
  });

  // Wire up provider buttons
  $('#prov-add-btn').onclick = () => openProvModal(null, providers);
  $$('[data-prov-edit]').forEach(b => b.onclick = () => {
    const id = parseInt(b.dataset.provEdit);
    const p = providers.find(x => x.id === id);
    openProvModal(id, providers, p);
  });
  $$('[data-prov-test]').forEach(b => b.onclick = async () => {
    b.disabled = true;
    const orig = b.innerHTML;
    b.innerHTML = 'Testing...';
    try {
      const r = await apiPost(`/api/providers/${b.dataset.provTest}/test`, {});
      if (r.ok) toast(`${r.model || 'Provider'} is working`, 'success');
      else toast(r.error || 'Test failed', 'error', { duration: 6000 });
    } catch (e) { toast('Test failed: ' + e.message, 'error'); }
    b.disabled = false;
    b.innerHTML = orig;
  });
  $$('[data-prov-delete]').forEach(b => b.onclick = async () => {
    if (!confirm('Delete this provider?')) return;
    try { await apiDelete(`/api/providers/${b.dataset.provDelete}`); toast('Deleted', 'success'); reload(); }
    catch (e) { toast('Error: ' + e.message, 'error'); }
  });

  function openCatModal(id, catList, c = null) {
    openModal(id ? 'Edit Category' : 'Add Category', `
      <div class="grid grid-2">
        <div><label>Name</label><input class="input" id="c-name" placeholder="e.g. Budget" value="${c ? esc(c.name) : ''}"></div>
        <div><label>Code (1-3 chars)</label><input class="input" id="c-code" placeholder="e.g. A, B, 250, XL" maxlength="4" value="${c ? esc(c.code || '') : ''}"></div>
      </div>
      <div class="mt-3 grid grid-2">
        <div><label>Sell Price (Rs)</label><input class="input" id="c-price" type="number" value="${c ? c.sell_price : 500}"></div>
        <div><label>Color</label><input class="input" id="c-color" type="color" value="${c ? c.color : '#10b981'}" style="height:40px"></div>
      </div>`,
      `<button class="btn btn-secondary" data-modal-close>Cancel</button>
       <button class="btn" id="c-save-btn">${SVG.save} Save</button>`);
    $('#c-save-btn').onclick = async () => {
      const payload = {
        name: $('#c-name').value,
        code: $('#c-code').value.trim().toUpperCase(),
        sell_price: parseFloat($('#c-price').value),
        color: $('#c-color').value, sort_order: 0,
      };
      try {
        if (id) await apiPut(`/api/categories/${id}`, payload);
        else await apiPost('/api/categories', payload);
        closeModal(); toast('Saved', 'success'); reload();
      } catch (e) { toast('Error: ' + e.message, 'error'); }
    };
  }

  function openProvModal(id, provList, p = null) {
    openModal(id ? 'Edit AI Provider' : 'Add AI Provider', `
      <div><label>Name</label><input class="input" id="p-name" placeholder="e.g. My Gemini" value="${p ? esc(p.name) : ''}"></div>
      <div class="mt-3"><label>Type</label><select class="select" id="p-type">
        <option value="gemini" ${p?.provider_type === 'gemini' ? 'selected' : ''}>Gemini</option>
        <option value="groq" ${p?.provider_type === 'groq' ? 'selected' : ''}>Groq</option>
        <option value="openrouter" ${p?.provider_type === 'openrouter' ? 'selected' : ''}>OpenRouter</option>
      </select></div>
      <div class="mt-3"><label>API Key ${p ? '(re-enter)' : ''}</label><input class="input" id="p-key" type="password" placeholder="Paste your key"></div>
      <div class="mt-3"><label>Model (optional)</label><input class="input" id="p-model" placeholder="gemini-2.5-flash" value="${p ? esc(p.model || '') : ''}"></div>
      <div class="mt-3"><label>Priority (lower = tried first)</label><input class="input" id="p-priority" type="number" value="${p ? p.priority : 0}"></div>
      <div id="p-test-result" class="mt-3"></div>`,
      `<button class="btn btn-secondary" data-modal-close>Cancel</button>
       <button class="btn btn-secondary" id="p-test-btn">${SVG.check} Test Connection</button>
       <button class="btn" id="p-save-btn">${SVG.save} Save</button>`);
    $('#p-test-btn').onclick = async () => {
      const key = $('#p-key').value;
      const type = $('#p-type').value;
      const model = $('#p-model').value;
      if (!key) { toast('Enter an API key first', 'error'); return; }
      $('#p-test-result').innerHTML = '<div class="alert alert-info">Testing connection...</div>';
      try {
        const r = await apiPost('/api/providers/test', { provider_type: type, api_key: key, model });
        $('#p-test-result').innerHTML = r.ok
          ? `<div class="alert alert-success">${SVG.check} <strong>Connection successful!</strong> Model: ${esc(r.model)}</div>`
          : `<div class="alert alert-danger">${SVG.alert} <strong>Failed:</strong> ${esc(r.error)}</div>`;
      } catch (e) {
        $('#p-test-result').innerHTML = `<div class="alert alert-danger">${SVG.alert} ${esc(e.message)}</div>`;
      }
    };
    $('#p-save-btn').onclick = async () => {
      const key = $('#p-key').value;
      if (!key) { toast('API key required', 'error'); return; }
      const payload = {
        name: $('#p-name').value, provider_type: $('#p-type').value, api_key: key,
        model: $('#p-model').value, priority: parseInt($('#p-priority').value) || 0, enabled: true,
      };
      try {
        if (id) await apiPut(`/api/providers/${id}`, payload);
        else await apiPost('/api/providers', payload);
        closeModal(); toast('Saved', 'success'); reload();
      } catch (e) { toast('Error: ' + e.message, 'error'); }
    };
  }
});

// ═══════════════════════════════════════════════════
// TAX & SMS — combined config page
// ═══════════════════════════════════════════════════
route('/settings/tax-sms', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon">${SVG.brain}</div>
      <div>
        <h2 class="pos-page-header-title">Tax & SMS Configuration</h2>
        <p class="pos-page-header-sub">Sales tax (GST) for POS checkout, and SMS notifications for receipts.</p>
      </div>
    </div>
    <div id="ts-out">${skeletonCards(2)}</div>`;

  let taxCfg = { rate: 0, inclusive: false };
  let smsCfg = { enabled: false, provider: 'twilio', account_sid: '', auth_token_masked: '', from_number: '' };
  try {
    [taxCfg, smsCfg] = await Promise.all([
      api('/api/tax/config'),
      api('/api/sms/config'),
    ]);
  } catch (e) {}

  $('#ts-out').innerHTML = `
    <div class="card mb-4">
      <h3>Tax / GST Configuration</h3>
      <p class="text-sm text-dim mt-2">Apply sales tax automatically at POS checkout. Set rate to 0 to disable.</p>
      <div class="grid grid-2 mt-3">
        <div>
          <label class="text-xs text-dim">Tax Rate (e.g., 0.17 for 17% GST)</label>
          <input class="input" id="tax-rate" type="number" step="0.001" min="0" max="1" value="${taxCfg.rate}">
        </div>
        <div>
          <label class="text-xs text-dim">Pricing Mode</label>
          <select class="select" id="tax-inclusive">
            <option value="false" ${!taxCfg.inclusive ? 'selected' : ''}>Tax-Exclusive (added at checkout)</option>
            <option value="true" ${taxCfg.inclusive ? 'selected' : ''}>Tax-Inclusive (already in price)</option>
          </select>
        </div>
      </div>
      <button class="btn mt-3" id="tax-save-btn">${SVG.save} Save Tax Settings</button>
    </div>

    <div class="card">
      <h3>SMS Notifications (Twilio)</h3>
      <p class="text-sm text-dim mt-2">Send receipt summaries to customers via SMS. Get credentials at twilio.com.</p>
      <div class="mt-3">
        <label class="text-xs text-dim">Enabled</label>
        <select class="select" id="sms-enabled">
          <option value="false" ${!smsCfg.enabled ? 'selected' : ''}>Disabled</option>
          <option value="true" ${smsCfg.enabled ? 'selected' : ''}>Enabled</option>
        </select>
      </div>
      <div class="grid grid-2 mt-2">
        <div>
          <label class="text-xs text-dim">Account SID</label>
          <input class="input" id="sms-sid" value="${esc(smsCfg.account_sid || '')}" placeholder="ACxxxxxxxxx">
        </div>
        <div>
          <label class="text-xs text-dim">Auth Token</label>
          <input class="input" id="sms-token" type="password" value="${esc(smsCfg.auth_token_masked || '')}" placeholder="Leave as-is to keep">
        </div>
      </div>
      <div class="mt-2">
        <label class="text-xs text-dim">From Number (Twilio)</label>
        <input class="input" id="sms-from" value="${esc(smsCfg.from_number || '')}" placeholder="+1234567890">
      </div>
      <button class="btn mt-3" id="sms-save-btn">${SVG.save} Save SMS Settings</button>
    </div>`;

  $('#tax-save-btn').onclick = async () => {
    try {
      await apiPost('/api/tax/config', {
        rate: parseFloat($('#tax-rate').value) || 0,
        inclusive: $('#tax-inclusive').value === 'true',
      });
      toast('Tax settings saved', 'success');
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  };
  $('#sms-save-btn').onclick = async () => {
    const cfg = {
      enabled: $('#sms-enabled').value === 'true',
      provider: 'twilio',
      account_sid: $('#sms-sid').value,
      from_number: $('#sms-from').value,
    };
    const token = $('#sms-token').value;
    if (token && !token.startsWith('•')) cfg.auth_token = token;
    try {
      await apiPost('/api/sms/config', cfg);
      toast('SMS settings saved', 'success');
      reload();
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  };
});

// ═══════════════════════════════════════════════════
// BACKUPS — list + create + download + restore + upload
// ═══════════════════════════════════════════════════
route('/settings/backups', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon">${SVG.backup}</div>
      <div>
        <h2 class="pos-page-header-title">Backups</h2>
        <p class="pos-page-header-sub">Database snapshots stored in <code>data/backups/</code>. Last 10 are retained. v8.16.6: now supports upload + restore.</p>
      </div>
      <div class="pos-page-header-actions">
        <input type="file" id="bk-upload-input" accept=".db,.zip" style="display:none">
        <button class="btn btn-secondary btn-sm" id="bk-upload-btn" title="Upload a backup .db or .zip file from another machine">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.upload || SVG.download}</span>
          Upload Backup
        </button>
                <button class="btn btn-primary btn-sm" id="bk-now-btn">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.backup}</span>
          Backup Now
        </button>
        <!-- v8.18.4: Google Drive cloud-backup button removed with the feature -->
      </div>
    </div>
    <div id="bk-out">${skeletonCards(2)}</div>`;

  $('#bk-now-btn').onclick = doBackup;
  $('#bk-upload-btn').onclick = () => $('#bk-upload-input').click();
  $('#bk-upload-input').onchange = doUpload;
  await loadBackups();

  async function loadBackups() {
    try {
      const r = await api('/api/backups');
      const list = r.backups || [];
      if (!list.length) {
        $('#bk-out').innerHTML = emptyState('No backups yet', 'Click "Backup Now" to create your first backup, or "Upload Backup" to import one from another machine.', '', '');
        return;
      }
      const totalSize = list.reduce((s, b) => s + (b.size_mb || 0), 0);
      $('#bk-out').innerHTML = `
        <div class="grid grid-3 mb-4">
          <div class="stat-card">
            <div class="stat-card-icon chip-primary">${SVG.backup}</div>
            <div class="stat-card-label">Total Backups</div>
            <div class="stat-card-value">${list.length}</div>
          </div>
          <div class="stat-card">
            <div class="stat-card-icon chip-success">${SVG.file}</div>
            <div class="stat-card-label">Latest Backup</div>
            <div class="stat-card-value" style="font-size:14px">${esc(list[0].name)}</div>
          </div>
          <div class="stat-card">
            <div class="stat-card-icon chip-info">${SVG.download}</div>
            <div class="stat-card-label">Total Size</div>
            <div class="stat-card-value">${totalSize.toFixed(1)} MB</div>
          </div>
        </div>
        <div class="card">
          <div class="card-title" style="display:flex;justify-content:space-between;align-items:center">
            <h3>Backup History</h3>
            <span class="text-dim text-sm">Click Restore to roll back to a previous state. Manager PIN required.</span>
          </div>
          <div class="table-wrap">
            <table>
              <thead><tr>
                <th>Name</th>
                <th class="table-num">Size</th>
                <th style="text-align:right">Actions</th>
              </tr></thead>
              <tbody>${list.map(b => `<tr>
                <td class="font-mono">
                  ${esc(b.name)}
                  ${b.name.startsWith('upload_') ? '<span class="chip chip-info chip-sm" style="margin-left:6px">UPLOADED</span>' : ''}
                  ${b.name.startsWith('pre_restore_safety_') ? '<span class="chip chip-warning chip-sm" style="margin-left:6px">SAFETY</span>' : ''}
                </td>
                <td class="table-num">${(b.size_mb || 0).toFixed(2)} MB</td>
                <td style="text-align:right;white-space:nowrap">
                  <button class="btn btn-secondary btn-sm" data-download="${esc(b.name)}" title="Download as ZIP">
                    <span style="display:inline-flex;width:12px;height:12px">${SVG.download}</span>
                    Download
                  </button>
                  <button class="btn btn-secondary btn-sm" data-restore="${esc(b.name)}" title="Restore this backup (replaces current DB)">
                    <span style="display:inline-flex;width:12px;height:12px">${SVG.backup}</span>
                    Restore
                  </button>
                </td>
              </tr>`).join('')}</tbody>
            </table>
          </div>
        </div>`;
      // Wire up the action buttons
      $$('[data-restore]').forEach(btn => {
        btn.onclick = () => doRestore(btn.dataset.restore);
      });
      $$('[data-download]').forEach(btn => {
        btn.onclick = () => doDownload(btn.dataset.download);
      });
    } catch (e) {
      $('#bk-out').innerHTML = errorBox(e.message);
    }
  }

  async function doBackup() {
    const btn = $('#bk-now-btn');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-sm"></span> Creating...';
    showLoading('Creating backup...');
    try {
      const r = await apiPost('/api/backup', {});
      hideLoading();
      toast(`Backup created (${r.size_mb} MB)`, 'success');
      loadBackups();
    } catch (e) {
      hideLoading();
      toast('Error: ' + e.message, 'error');
    }
    btn.disabled = false;
    btn.innerHTML = `<span style="display:inline-flex;width:14px;height:14px">${SVG.backup}</span> Backup Now`;
  }

  async function doUpload() {
    const input = $('#bk-upload-input');
    if (!input.files || !input.files[0]) return;
    const file = input.files[0];
    if (file.size > 200 * 1024 * 1024) {
      toast('File too large (max 200 MB)', 'error');
      return;
    }
    const fd = new FormData();
    fd.append('file', file);
    showLoading(`Uploading ${file.name}...`);
    try {
      const r = await fetch('/api/backup/upload', {
        method: 'POST',
        body: fd,
        credentials: 'same-origin',
      });
      const data = await r.json();
      hideLoading();
      if (!r.ok) throw new Error(data.detail || data.error || 'Upload failed');
      toast(`Upload successful (${data.size_mb} MB). Click Restore to apply.`, 'success');
      loadBackups();
    } catch (e) {
      hideLoading();
      toast('Upload error: ' + e.message, 'error');
    }
    input.value = '';  // reset so user can re-upload same file
  }

  function doDownload(name) {
    // Use a hidden iframe so the browser downloads instead of navigating
    const url = `/api/backup/download?name=${encodeURIComponent(name)}`;
    const a = document.createElement('a');
    a.href = url;
    a.download = `billbook_backup_${name}.zip`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    toast('Preparing download...', 'info');
  }

  async function doRestore(name) {
    // Confirm with the user
    const confirmed = confirm(
      `Restore backup "${name}"?\n\n` +
      `This will REPLACE your current database with this backup.\n` +
      `A safety backup of your current state will be created automatically.\n\n` +
      `Click Cancel to abort.`
    );
    if (!confirmed) return;

    // Ask for manager PIN
    const pin = prompt(`Enter Manager PIN to restore backup "${name}":`);
    if (!pin) return;

    showLoading('Creating safety backup, then restoring...');
    try {
      const r = await apiPost('/api/backup/restore', { name, manager_pin: pin });
      hideLoading();
      toast(`Restore successful! Safety backup: ${r.safety_backup}. Restart the server to apply.`, 'success', 8000);
      loadBackups();
      // Also offer a hard reload
      setTimeout(() => {
        if (confirm('Restore complete. Reload the page now?')) {
          location.reload();
        }
      }, 1500);
    } catch (e) {
      hideLoading();
      toast('Restore error: ' + e.message, 'error', 8000);
    }
  }
});

// ═══════════════════════════════════════════════════
// APPEARANCE & BRANDING — v8.15.0, full design.md system
// Every design.md option: theme (cream/dark), brand accent (+ presets),
// serif display headings, radius scale, density, font scale, live preview,
// plus Shop Branding (identity + receipt template).
// Before v8.14.x these controls saved values but NOTHING applied them.
// ═══════════════════════════════════════════════════
route('/settings/appearance', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon">${SVG.sun}</div>
      <div>
        <h2 class="pos-page-header-title">Appearance & Branding</h2>
        <p class="pos-page-header-sub">Theme, brand accent, typography, shapes, and shop identity. Changes preview live.</p>
      </div>
    </div>
    <div id="ap-out">${skeletonCards(2)}</div>`;

  let cfg = {};   try { cfg = await api('/api/appearance'); } catch (e) {}
  let shop = {};  try { shop = await api('/api/shop-profile'); } catch (e) {}
  let rcpt = {};  try { rcpt = await api('/api/receipt-template'); } catch (e) {}

  // Local state — normalized against design.md defaults
  const st = {
    theme: cfg.theme === 'dark' ? 'dark' : 'light',
    accent_color: /^#[0-9a-fA-F]{6}$/.test(cfg.accent_color || '') ? cfg.accent_color : APPEARANCE_DEFAULTS.accent_color,
    serif_headings: !(cfg.serif_headings === false || cfg.serif_headings === '0'),
    radius: ['compact', 'standard', 'roomy'].includes(cfg.radius) ? cfg.radius : 'standard',
    density: cfg.density === 'compact' ? 'compact' : 'comfortable',
    font_scale: String(Math.min(120, Math.max(90, parseInt(cfg.font_scale || '100', 10) || 100))),
  };
  const dirty = { appearance: false, shop: false };

  const radiusMeta = {
    compact:  { label: 'Compact',  desc: '4–10px — tight, utilitarian' },
    standard: { label: 'Standard', desc: '6–16px — design default' },
    roomy:    { label: 'Roomy',    desc: '8–20px — soft, friendly' },
  };

  $('#ap-out').innerHTML = `
    <div class="card mb-4" id="ap-preview-card">
      <div class="card-title">
        <h3>Live Preview</h3>
        <span class="badge badge-accent">updates as you choose</span>
      </div>
      <p class="text-sm text-dim mt-2">The whole app restyles instantly — this card just demonstrates the pieces.</p>
      <div class="ap-preview mt-3">
        <div class="ap-preview-head">
          <div class="ap-preview-mark"></div>
          <div class="ap-preview-title font-serif-display">Daily Sales</div>
          <span class="ap-preview-badge">NEW</span>
        </div>
        <div class="ap-preview-body">
          <div class="ap-preview-kpi">
            <div class="ap-preview-kpi-label">Today</div>
            <div class="ap-preview-kpi-value">Rs 128,450</div>
          </div>
          <div class="ap-preview-actions">
            <button class="btn ap-preview-btn" type="button">New Sale</button>
            <button class="btn btn-secondary ap-preview-btn2" type="button">Reports</button>
          </div>
        </div>
      </div>
    </div>

    <div class="card mb-4">
      <h3>Theme</h3>
      <p class="text-sm text-dim mt-2">Design default is the warm cream canvas. Dark is the warm-navy variant for night counters.</p>
      <div class="grid grid-2 mt-3">
        <label class="appearance-theme-card ${st.theme === 'light' ? 'selected' : ''}" data-theme="light">
          <input type="radio" name="theme" value="light" ${st.theme === 'light' ? 'checked' : ''} hidden>
          <div class="appearance-theme-preview appearance-theme-preview-light">
            <div class="appearance-theme-bar"></div>
            <div class="appearance-theme-content"></div>
          </div>
          <div class="appearance-theme-label">
            <span style="display:inline-flex;width:16px;height:16px;margin-right:6px">${SVG.sun}</span>
            Cream Light <span class="text-dim text-sm">(default)</span>
          </div>
        </label>
        <label class="appearance-theme-card ${st.theme === 'dark' ? 'selected' : ''}" data-theme="dark">
          <input type="radio" name="theme" value="dark" ${st.theme === 'dark' ? 'checked' : ''} hidden>
          <div class="appearance-theme-preview appearance-theme-preview-dark">
            <div class="appearance-theme-bar"></div>
            <div class="appearance-theme-content"></div>
          </div>
          <div class="appearance-theme-label">
            <span style="display:inline-flex;width:16px;height:16px;margin-right:6px">${SVG.moon}</span>
            Warm Dark
          </div>
        </label>
      </div>
    </div>

    <div class="card mb-4">
      <h3>Brand Accent</h3>
      <p class="text-sm text-dim mt-2">Used for primary buttons, links, and highlights. Coral is the design signature.</p>
      <div class="flex gap-3 mt-3 items-center flex-wrap" id="ap-accent-presets">
        ${APPEARANCE_ACCENT_PRESETS.map(p => `
          <button type="button" class="ap-swatch ${st.accent_color.toLowerCase() === p.value ? 'selected' : ''}"
                  data-accent="${p.value}" title="${p.name} ${p.value}">
            <span class="ap-swatch-dot" style="background:${p.value}"></span>${p.name}
          </button>`).join('')}
        <span class="flex gap-2 items-center">
          <input class="input" id="ap-accent" type="color" value="${esc(st.accent_color)}" style="width:56px;height:38px;padding:2px">
          <input class="input font-mono" id="ap-accent-text" value="${esc(st.accent_color)}" style="max-width:120px" spellcheck="false">
        </span>
      </div>
    </div>

    <div class="card mb-4">
      <h3>Display Headings</h3>
      <p class="text-sm text-dim mt-2">Serif (Cormorant Garamond) is the design voice for page titles. Switch off for all-sans.</p>
      <div class="flex gap-3 mt-3 items-center">
        <div class="ap-seg" id="ap-serif-seg">
          <button type="button" class="ap-seg-btn ${st.serif_headings ? 'selected' : ''}" data-serif="on">Serif display</button>
          <button type="button" class="ap-seg-btn ${!st.serif_headings ? 'selected' : ''}" data-serif="off">All sans</button>
        </div>
        <span class="font-serif-display ap-demo-serif" style="font-size:22px">Aa — Daily Sales</span>
      </div>
    </div>

    <div class="card mb-4">
      <h3>Corner Radius</h3>
      <p class="text-sm text-dim mt-2">Shape scale applied to buttons, inputs, cards, and modals.</p>
      <div class="grid grid-3 mt-3" id="ap-radius-cards">
        ${Object.entries(radiusMeta).map(([k, m]) => `
          <button type="button" class="ap-radius-card ${st.radius === k ? 'selected' : ''}" data-radius="${k}">
            <span class="ap-radius-shape" data-r="${k}"></span>
            <div>
              <div class="font-semibold">${m.label}${k === 'standard' ? ' <span class="text-dim text-sm">(default)</span>' : ''}</div>
              <div class="text-sm text-dim">${m.desc}</div>
            </div>
          </button>`).join('')}
      </div>
    </div>

    <div class="card mb-4">
      <h3>Layout Density</h3>
      <p class="text-sm text-dim mt-2">Comfortable gives more breathing room; Compact fits more rows per screen.</p>
      <div class="mt-3" id="ap-density-seg">
        <div class="ap-seg">
          <button type="button" class="ap-seg-btn ${st.density === 'comfortable' ? 'selected' : ''}" data-density="comfortable">Comfortable (default)</button>
          <button type="button" class="ap-seg-btn ${st.density === 'compact' ? 'selected' : ''}" data-density="compact">Compact</button>
        </div>
      </div>
    </div>

    <div class="card mb-4">
      <h3>Font & UI Scale</h3>
      <p class="text-sm text-dim mt-2">Scales the whole interface — handy on small counter screens.</p>
      <div class="flex gap-3 mt-3 items-center">
        <input class="input" id="ap-font-scale" type="range" min="90" max="120" step="5" value="${esc(st.font_scale)}" style="max-width:240px">
        <span id="ap-font-scale-label" class="font-semibold">${esc(st.font_scale)}%</span>
      </div>
    </div>

    <div class="flex gap-2 mb-4 items-center">
      <button class="btn" id="ap-save-btn">${SVG.save} Save Appearance</button>
      <button class="btn btn-secondary" id="ap-reset-btn">Reset to design defaults</button>
      <span class="text-sm text-dim" id="ap-dirty-note"></span>
    </div>

    <div class="card mb-4" id="ap-shop-card">
      <div class="card-title">
        <h3><span style="display:inline-flex;width:18px;height:18px;vertical-align:-3px;margin-right:6px">${SVG.store}</span>Shop Branding</h3>
      </div>
      <p class="text-sm text-dim mt-2">Your shop identity — printed on receipts and invoices.</p>
      <div class="grid grid-2 mt-3">
        <label class="text-sm">Shop name
          <input class="input mt-1" id="sb-name" value="${esc(shop.shop_name || '')}" placeholder="e.g. Al-Madina Kiryana Store">
        </label>
        <label class="text-sm">Phone
          <input class="input mt-1" id="sb-phone" value="${esc(shop.phone || '')}" placeholder="0300-1234567">
        </label>
        <label class="text-sm" style="grid-column:1/-1">Address
          <input class="input mt-1" id="sb-address" value="${esc(shop.address || '')}" placeholder="Shop 12, Main Bazaar, Lahore">
        </label>
        <label class="text-sm">NTN <span class="text-dim">(tax)</span>
          <input class="input mt-1" id="sb-ntn" value="${esc(shop.ntn || '')}" placeholder="Optional">
        </label>
        <label class="text-sm">STRN <span class="text-dim">(sales tax)</span>
          <input class="input mt-1" id="sb-strn" value="${esc(shop.strn || '')}" placeholder="Optional">
        </label>
        <label class="text-sm" style="grid-column:1/-1">Logo URL <span class="text-dim">(shown on receipts when enabled below)</span>
          <input class="input mt-1" id="sb-logo" value="${esc(shop.logo || '')}" placeholder="/static/icons/icon-192.png or https://...">
        </label>
      </div>
    </div>

    <div class="card mb-4">
      <h3>Receipt Template</h3>
      <p class="text-sm text-dim mt-2">Controls what your printed bills look like.</p>
      <div class="grid grid-2 mt-3">
        <label class="text-sm" style="grid-column:1/-1">Header line (above shop name)
          <input class="input mt-1" id="rt-header" value="${esc(rcpt.header_text || '')}" placeholder="e.g. Wholesale & Retail">
        </label>
        <label class="text-sm" style="grid-column:1/-1">Receipt footer message
          <input class="input mt-1" id="rt-footer" value="${esc(rcpt.footer_text || '')}" placeholder="Thank you for your business!">
        </label>
      </div>
      <div class="flex gap-4 mt-3 flex-wrap text-sm">
        <label class="flex gap-2 items-center"><input type="checkbox" id="rt-logo" ${rcpt.show_logo !== false ? 'checked' : ''}> Show logo on receipts</label>
        <label class="flex gap-2 items-center"><input type="checkbox" id="rt-ntn" ${rcpt.show_ntn !== false ? 'checked' : ''}> Print NTN</label>
        <label class="flex gap-2 items-center"><input type="checkbox" id="rt-strn" ${rcpt.show_strn !== false ? 'checked' : ''}> Print STRN</label>
        <label class="flex gap-2 items-center"><input type="checkbox" id="rt-qr" ${rcpt.show_qr === true ? 'checked' : ''}> QR code <span class="text-dim">(FBR)</span></label>
      </div>
      <button class="btn mt-3" id="sb-save-btn">${SVG.save} Save Shop Branding</button>
    </div>`;

  // ── Live-apply helper ─────────────────────────────────────────────
  const liveApply = () => applyAppearance(st);
  const markDirty = (which) => {
    dirty[which] = true;
    $('#ap-dirty-note').textContent = 'Unsaved changes — press Save to keep them.';
  };

  // ── Theme cards ───────────────────────────────────────────────────
  $$('.appearance-theme-card').forEach(card => {
    card.onclick = () => {
      $$('.appearance-theme-card').forEach(c => c.classList.remove('selected'));
      card.classList.add('selected');
      card.querySelector('input').checked = true;
      st.theme = card.dataset.theme;
      liveApply(); markDirty('appearance');
    };
  });

  // ── Accent presets + custom ───────────────────────────────────────
  const setAccent = (hex) => {
    st.accent_color = hex;
    $$('.ap-swatch').forEach(s => s.classList.toggle('selected', s.dataset.accent.toLowerCase() === hex.toLowerCase()));
    $('#ap-accent').value = hex;
    $('#ap-accent-text').value = hex;
    liveApply(); markDirty('appearance');
  };
  $$('.ap-swatch').forEach(s => { s.onclick = () => setAccent(s.dataset.accent); });
  $('#ap-accent').oninput = (e) => setAccent(e.target.value);
  $('#ap-accent-text').oninput = (e) => {
    if (/^#[0-9a-fA-F]{6}$/.test(e.target.value)) setAccent(e.target.value);
  };

  // ── Serif toggle ──────────────────────────────────────────────────
  $$('#ap-serif-seg .ap-seg-btn').forEach(b => {
    b.onclick = () => {
      $$('#ap-serif-seg .ap-seg-btn').forEach(x => x.classList.remove('selected'));
      b.classList.add('selected');
      st.serif_headings = b.dataset.serif === 'on';
      liveApply(); markDirty('appearance');
    };
  });

  // ── Radius cards ──────────────────────────────────────────────────
  $$('.ap-radius-card').forEach(card => {
    card.onclick = () => {
      $$('.ap-radius-card').forEach(c => c.classList.remove('selected'));
      card.classList.add('selected');
      st.radius = card.dataset.radius;
      liveApply(); markDirty('appearance');
    };
  });

  // ── Density segmented control ─────────────────────────────────────
  $$('#ap-density-seg .ap-seg-btn').forEach(b => {
    b.onclick = () => {
      $$('#ap-density-seg .ap-seg-btn').forEach(x => x.classList.remove('selected'));
      b.classList.add('selected');
      st.density = b.dataset.density;
      liveApply(); markDirty('appearance');
    };
  });

  // ── Font scale slider ─────────────────────────────────────────────
  $('#ap-font-scale').oninput = (e) => {
    st.font_scale = e.target.value;
    $('#ap-font-scale-label').textContent = e.target.value + '%';
    liveApply(); markDirty('appearance');
  };

  // ── Save appearance ───────────────────────────────────────────────
  $('#ap-save-btn').onclick = async () => {
    try {
      await apiPost('/api/appearance', {
        theme: st.theme,
        accent_color: st.accent_color,
        density: st.density,
        font_scale: st.font_scale,
        serif_headings: st.serif_headings,
        radius: st.radius,
      });
      cacheAppearance({ ...st, serif_headings: st.serif_headings ? '1' : '0' });
      dirty.appearance = false;
      $('#ap-dirty-note').textContent = '';
      toast('Appearance saved — applies to every device on this account', 'success');
    } catch (e) { toast('Error: ' + e.message, 'error', 8000); }
  };

  // ── Reset to design.md defaults ───────────────────────────────────
  $('#ap-reset-btn').onclick = async () => {
    Object.assign(st, {
      theme: 'light', accent_color: '#cc785c', serif_headings: true,
      radius: 'standard', density: 'comfortable', font_scale: '100',
    });
    // refresh the controls to match
    $$('.appearance-theme-card').forEach(c => c.classList.toggle('selected', c.dataset.theme === 'light'));
    $$('.ap-swatch').forEach(s => s.classList.toggle('selected', s.dataset.accent === '#cc785c'));
    $('#ap-accent').value = '#cc785c'; $('#ap-accent-text').value = '#cc785c';
    $$('#ap-serif-seg .ap-seg-btn').forEach(b => b.classList.toggle('selected', b.dataset.serif === 'on'));
    $$('.ap-radius-card').forEach(c => c.classList.toggle('selected', c.dataset.radius === 'standard'));
    $$('#ap-density-seg .ap-seg-btn').forEach(b => b.classList.toggle('selected', b.dataset.density === 'comfortable'));
    $('#ap-font-scale').value = '100'; $('#ap-font-scale-label').textContent = '100%';
    liveApply();
    try {
      await apiPost('/api/appearance', { theme: st.theme, accent_color: st.accent_color, density: st.density, font_scale: st.font_scale, serif_headings: true, radius: st.radius });
      cacheAppearance({ ...st, serif_headings: '1' });
      dirty.appearance = false; $('#ap-dirty-note').textContent = '';
      toast('Reset to design defaults', 'success');
    } catch (e) { markDirty('appearance'); toast('Preview reset — save failed: ' + e.message, 'error'); }
  };

  // ── Save shop branding + receipt template ─────────────────────────
  $('#sb-save-btn').onclick = async () => {
    try {
      await apiPost('/api/shop-profile', {
        shop_name: $('#sb-name').value.trim(),
        address: $('#sb-address').value.trim(),
        phone: $('#sb-phone').value.trim(),
        ntn: $('#sb-ntn').value.trim(),
        strn: $('#sb-strn').value.trim(),
        logo: $('#sb-logo').value.trim(),
      });
      await apiPost('/api/receipt-template', {
        header_text: $('#rt-header').value,
        footer_text: $('#rt-footer').value,
        show_logo: $('#rt-logo').checked,
        show_ntn: $('#rt-ntn').checked,
        show_strn: $('#rt-strn').checked,
        show_qr: $('#rt-qr').checked,
      });
      toast('Shop branding saved — receipts will use it from the next print', 'success');
    } catch (e) { toast('Error: ' + e.message, 'error', 8000); }
  };
});
