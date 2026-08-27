// Settings app — Employees (Staff UI) + Security
// Part 2 of Settings app (split for file size). Part 1: settings-pages.js (General, Tax, Backups, Appearance).
import { route, navigate, reload } from '../router.js';
import { api, apiPost, apiPut, apiDelete } from '../api.js';
import { $, $$, esc, fmt, fmtDate, toast, showLoading, hideLoading,
         openModal, closeModal, skeletonCards, errorBox, emptyState, icon, iconHtml } from '../utils.js';

const SVG = {
  users: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
  plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>',
  save: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>',
  edit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
  lock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>',
  alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
  key: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/></svg>',
  phone: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z"/></svg>',
  x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
};

function statCard(label, value, chipClass, svgIcon, sub = '') {
  return `<div class="stat-card">
    <div class="stat-card-icon ${chipClass}">${svgIcon}</div>
    <div class="stat-card-label">${esc(label)}</div>
    <div class="stat-card-value">${value}</div>
    ${sub ? `<div class="stat-card-delta">${sub}</div>` : ''}
  </div>`;
}

// ═══════════════════════════════════════════════════
// EMPLOYEES — list + add + edit + delete + set PIN
// ═══════════════════════════════════════════════════
route('/settings/employees', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon">${SVG.users}</div>
      <div>
        <h2 class="pos-page-header-title">Employees & Roles</h2>
        <p class="pos-page-header-sub">Manage staff who can log in via PIN at the POS. Roles: cashier, manager, admin.</p>
      </div>
      <div class="pos-page-header-actions">
        <button class="btn" id="emp-add-btn">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.plus}</span>
          Add Employee
        </button>
      </div>
    </div>
    <div id="emp-stats" class="mb-4"></div>
    <div class="card">
      <div class="card-title"><h3>Staff Members</h3></div>
      <div id="emp-list">${skeletonCards(2)}</div>
    </div>`;

  $('#emp-add-btn').onclick = () => openEmpModal(null);
  await loadEmployees();

  async function loadEmployees() {
    try {
      const r = await api('/api/employees');
      const list = r.employees || [];
      // Stats
      const active = list.filter(e => e.active).length;
      const managers = list.filter(e => e.role === 'manager' || e.role === 'admin').length;
      const withPin = list.filter(e => e.pin).length;
      $('#emp-stats').innerHTML = `
        <div class="grid grid-3">
          ${statCard('Total Staff', list.length, 'chip-primary', SVG.users)}
          ${statCard('Managers/Admins', managers, 'chip-warning', SVG.lock)}
          ${statCard('With PIN Set', withPin, 'chip-success', SVG.key, `${list.length - withPin} need PIN`)}
        </div>`;

      if (!list.length) {
        $('#emp-list').innerHTML = emptyState('No employees yet', 'Add staff members to enable PIN-based POS login.', '', '');
        const eb = document.querySelector('.empty-state button');
        if (eb) eb.onclick = () => openEmpModal(null);
        return;
      }

      $('#emp-list').innerHTML = `
        <div class="table-wrap">
          <table>
            <thead><tr>
              <th>Name</th><th>Phone</th><th>Role</th>
              <th>PIN</th><th>Status</th><th>Created</th><th>Actions</th>
            </tr></thead>
            <tbody>${list.map(e => `<tr>
              <td class="font-semibold">${esc(e.name)}</td>
              <td class="text-sm">${esc(e.phone || '—')}</td>
              <td><span class="badge ${e.role === 'admin' ? 'badge-danger' : e.role === 'manager' ? 'badge-warning' : 'badge-success'}">${esc(e.role)}</span></td>
              <td>${e.pin ? '<span class="badge badge-success">Set</span>' : '<span class="badge badge-warning">Not set</span>'}</td>
              <td>${e.active ? '<span class="badge badge-success">Active</span>' : '<span class="badge badge-secondary">Inactive</span>'}</td>
              <td class="text-sm text-dim">${fmtDate(e.created_at)}</td>
              <td>
                <div class="flex gap-2">
                  <button class="btn btn-secondary btn-sm" data-emp-pin="${e.id}" title="Set PIN">${SVG.key}</button>
                  <button class="btn btn-ghost btn-sm btn-icon" data-emp-edit="${e.id}" title="Edit">${SVG.edit}</button>
                  <button class="btn btn-ghost btn-sm btn-icon" data-emp-toggle="${e.id}" data-active="${e.active}" title="${e.active ? 'Deactivate' : 'Activate'}">${e.active ? SVG.x : SVG.check}</button>
                </div>
              </td>
            </tr>`).join('')}</tbody>
          </table>
        </div>`;

      // Wire up buttons
      $$('[data-emp-pin]').forEach(b => b.onclick = () => openPinModal(parseInt(b.dataset.empPin)));
      $$('[data-emp-edit]').forEach(b => b.onclick = () => {
        const id = parseInt(b.dataset.empEdit);
        const emp = list.find(e => e.id === id);
        openEmpModal(id, emp);
      });
      $$('[data-emp-toggle]').forEach(b => b.onclick = async () => {
        const id = parseInt(b.dataset.empToggle);
        const currentlyActive = b.dataset.active === '1';
        try {
          await apiPut(`/api/employees/${id}`, { active: currentlyActive ? 0 : 1 });
          toast(currentlyActive ? 'Employee deactivated' : 'Employee activated', 'success');
          loadEmployees();
        } catch (e) { toast('Error: ' + e.message, 'error'); }
      });
    } catch (e) {
      $('#emp-list').innerHTML = errorBox(e.message);
    }
  }

  function openEmpModal(id, emp = null) {
    openModal(id ? 'Edit Employee' : 'Add Employee', `
      <div><label>Name</label><input class="input" id="e-name" value="${emp ? esc(emp.name) : ''}" placeholder="Full name"></div>
      <div class="mt-3"><label>Phone</label><input class="input" id="e-phone" value="${emp ? esc(emp.phone || '') : ''}" placeholder="03001234567"></div>
      <div class="mt-3"><label>Role</label><select class="select" id="e-role">
        <option value="cashier" ${emp?.role === 'cashier' ? 'selected' : ''}>Cashier (POS only)</option>
        <option value="manager" ${emp?.role === 'manager' ? 'selected' : ''}>Manager (POS + reports)</option>
        <option value="admin" ${emp?.role === 'admin' ? 'selected' : ''}>Admin (full access)</option>
      </select></div>
      ${id ? '' : '<p class="text-xs text-dim mt-2">After saving, click the key icon to set a PIN for POS login.</p>'}`,
      `<button class="btn btn-secondary" data-modal-close>Cancel</button>
       <button class="btn" id="e-save-btn">${SVG.save} Save</button>`);
    $('#e-save-btn').onclick = async () => {
      const payload = {
        name: $('#e-name').value,
        phone: $('#e-phone').value,
        role: $('#e-role').value,
      };
      if (!payload.name) { toast('Name is required', 'error'); return; }
      try {
        if (id) {
          await apiPut(`/api/employees/${id}`, payload);
        } else {
          // POST /api/employees uses query params
          await apiPost(`/api/employees?name=${encodeURIComponent(payload.name)}&phone=${encodeURIComponent(payload.phone)}&role=${payload.role}`, {});
        }
        toast('Employee saved', 'success');
        closeModal();
        loadEmployees();
      } catch (e) { toast('Error: ' + e.message, 'error'); }
    };
  }

  function openPinModal(empId) {
    openModal('Set POS PIN', `
      <p class="text-sm text-dim">Set a 4-8 digit PIN for POS login. The employee will enter this at the POS screen to start a shift.</p>
      <div class="mt-3"><label>New PIN (4-8 digits)</label><input class="input" id="pin-input" type="password" inputmode="numeric" maxlength="8" placeholder="1234" autofocus></div>
      <div class="mt-2"><label>Confirm PIN</label><input class="input" id="pin-confirm" type="password" inputmode="numeric" maxlength="8" placeholder="1234"></div>`,
      `<button class="btn btn-secondary" data-modal-close>Cancel</button>
       <button class="btn" id="pin-save-btn">${SVG.key} Set PIN</button>`);
    $('#pin-save-btn').onclick = async () => {
      const pin = $('#pin-input').value;
      const conf = $('#pin-confirm').value;
      if (pin !== conf) { toast('PINs do not match', 'error'); return; }
      if (!pin || !pin.match(/^\d{4,8}$/)) { toast('PIN must be 4-8 digits', 'error'); return; }
      try {
        await apiPost(`/api/employees/${empId}/pin`, { pin });
        toast('PIN set successfully', 'success');
        closeModal();
        loadEmployees();
      } catch (e) { toast('Error: ' + e.message, 'error'); }
    };
  }
});

// ═══════════════════════════════════════════════════
// SECURITY — change password + active sessions
// ═══════════════════════════════════════════════════
route('/settings/security', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon">${SVG.lock}</div>
      <div>
        <h2 class="pos-page-header-title">Security</h2>
        <p class="pos-page-header-sub">Change your login password and manage active sessions.</p>
      </div>
    </div>

    <div class="grid grid-2 mb-4">
      <div class="card">
        <h3>Change Password</h3>
        <p class="text-sm text-dim mt-2">Min 8 characters. Use a mix of letters, numbers, and symbols for best security.</p>
        <div class="mt-3"><label>Current Password</label><input class="input" id="pw-old" type="password"></div>
        <div class="mt-2"><label>New Password (min 8 chars)</label><input class="input" id="pw-new" type="password"></div>
        <div class="mt-2"><label>Confirm New Password</label><input class="input" id="pw-conf" type="password"></div>
        <button class="btn mt-3" id="pw-save-btn">${SVG.save} Change Password</button>
      </div>
      <div class="card">
        <h3>Active Sessions</h3>
        <p class="text-sm text-dim mt-2">Active login sessions on this account. Revoke any you don't recognize.</p>
        <div id="sec-sessions" class="mt-3"><p class="text-dim text-sm">Loading...</p></div>
      </div>
    </div>

    <div class="card">
      <h3>Security Tips</h3>
      <div class="grid grid-3 mt-3">
        <div>
          <div class="kpi-label">${SVG.lock} Strong Password</div>
          <p class="mt-2 text-sm">Use 12+ characters with uppercase, lowercase, numbers, and symbols. Avoid dictionary words.</p>
        </div>
        <div>
          <div class="kpi-label">${SVG.key} Employee PINs</div>
          <p class="mt-2 text-sm">Set unique 4-8 digit PINs for each employee. Change PINs periodically for better security.</p>
        </div>
        <div>
          <div class="kpi-label">${SVG.alert} Session Hygiene</div>
          <p class="mt-2 text-sm">Revoke sessions on shared/public devices. Each session expires after 30 days automatically.</p>
        </div>
      </div>
    </div>`;

  $('#pw-save-btn').onclick = async () => {
    const oldPw = $('#pw-old').value;
    const newPw = $('#pw-new').value;
    const conf = $('#pw-conf').value;
    if (newPw !== conf) { toast('Passwords do not match', 'error'); return; }
    if (newPw.length < 8) { toast('Min 8 characters', 'error'); return; }
    try {
      await apiPost('/api/change-password', { old_password: oldPw, new_password: newPw });
      toast('Password changed', 'success');
      $('#pw-old').value = '';
      $('#pw-new').value = '';
      $('#pw-conf').value = '';
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  };

  await loadSessions();

  async function loadSessions() {
    try {
      const r = await api('/api/sessions');
      const list = r.sessions || [];
      if (!list.length) {
        $('#sec-sessions').innerHTML = '<p class="text-dim text-sm">No active sessions.</p>';
        return;
      }
      $('#sec-sessions').innerHTML = `
        <div class="table-wrap">
          <table>
            <thead><tr><th>Token</th><th>Created</th><th>Expires</th><th>Role</th><th></th></tr></thead>
            <tbody>${list.map(s => `<tr>
              <td class="font-mono text-xs">${esc(s.token_prefix)}...</td>
              <td class="text-sm">${fmtDate(s.created_at)}</td>
              <td class="text-sm">${fmtDate(s.expires_at)}</td>
              <td><span class="badge badge-accent">${esc(s.role)}</span></td>
              <td><button class="btn btn-ghost btn-sm btn-icon" data-session-revoke="${esc(s.token_prefix)}" title="Revoke">${SVG.x}</button></td>
            </tr>`).join('')}</tbody>
          </table>
        </div>`;
      $$('[data-session-revoke]').forEach(b => b.onclick = async () => {
        if (!confirm('Revoke this session? The device will need to log in again.')) return;
        try {
          await apiDelete(`/api/sessions/${b.dataset.sessionRevoke}`);
          toast('Session revoked', 'success');
          loadSessions();
        } catch (e) { toast('Error: ' + e.message, 'error'); }
      });
    } catch (e) {
      $('#sec-sessions').innerHTML = `<p class="text-dim text-sm">Could not load sessions: ${esc(e.message)}</p>`;
    }
  }
});
