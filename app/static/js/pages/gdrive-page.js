// v8.18.0 — Google Drive settings page (/settings/gdrive)
// Full management UI for the Drive integration that previously only existed
// in the setup wizard: connect/disconnect, backup now, restore-test, the
// daily backup hour, and the NEW POS backup auto-import watcher.
//
// AUTO-IMPORT: drop POS backup zips (BU*.zip) into the "BillBook POS
// Imports" folder in the operator's Google Drive — from any device — and
// BillBook imports them automatically every 15 minutes through the same
// idempotent pipeline as the manual upload page (UNQCODE dedup = safe).
import { route } from '../router.js';
import { api, apiPost } from '../api.js';
import { $, esc, toast, openModal, closeModal, skeletonCards, errorBox, btnBusy, btnOk } from '../utils.js';

const SVG = {
  cloud: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z"/></svg>',
  backup: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/></svg>',
  refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>',
  upload: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>',
  download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>',
  alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>',
};

route('/settings/gdrive', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon">${SVG.cloud}</div>
      <div>
        <h2 class="pos-page-header-title">Google Drive</h2>
        <p class="pos-page-header-sub">Cloud backups to your own Google Drive, and automatic import of POS backup zips uploaded from any device.</p>
      </div>
      <div class="pos-page-header-actions"></div>
    </div>
    <div id="gd-out">${skeletonCards(2)}</div>`;

  await load();

  async function load() {
    let status, autoImport;
    try {
      [status, autoImport] = await Promise.all([
        api('/api/gdrive/status'),
        api('/api/gdrive/auto-import'),
      ]);
    } catch (e) {
      $('#gd-out').innerHTML = errorBox(e.message);
      return;
    }
    render(status, autoImport);
  }

  function render(status, ai) {
    const connected = status.connected;
    const hour = status.auto_backup_hour ?? 2;
    const hours = Array.from({ length: 24 }, (_, h) =>
      `<option value="${h}" ${h === hour ? 'selected' : ''}>${String(h).padStart(2, '0')}:00</option>`).join('');

    const recent = (ai.recent || []).map(r => `
      <tr>
        <td class="text-sm" style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(r.name)}">${esc(r.name)}</td>
        <td class="text-xs text-dim" style="white-space:nowrap">${esc(r.at || '')}</td>
        <td>${r.ok
          ? `<span class="chip chip-success" style="font-size:11px;font-weight:600;padding:2px 8px;border-radius:6px">${SVG.check} ${r.sales || 0} sales</span>`
          : `<span class="chip chip-error" style="font-size:11px;font-weight:600;padding:2px 8px;border-radius:6px" title="${esc(r.error || '')}">failed</span>`}</td>
      </tr>`).join('');

    $('#gd-out').innerHTML = `
      ${ai.needs_reconnect ? `
      <div class="card" style="padding:12px;background:var(--warning-soft,#FEF3C7);border-left:3px solid var(--warning,#D97706);margin-bottom:16px;display:flex;gap:10px;align-items:flex-start">
        <span style="display:inline-flex;width:16px;height:16px;color:var(--warning-text,#D97706);flex-shrink:0;margin-top:2px">${SVG.alert}</span>
        <div class="text-sm">
          <strong>Re-connect needed for manual uploads.</strong> Your Drive connection predates v8.18.0 and
          can only see files created by BillBook itself. Disconnect and connect again (one time) to also
          auto-import POS zips uploaded manually from a phone or any other device.
        </div>
      </div>` : ''}

      <div class="card" style="padding:16px;margin-bottom:16px">
        <div class="card-title" style="display:flex;justify-content:space-between;align-items:center">
          <h3 style="display:flex;align-items:center;gap:8px">
            <span style="display:inline-flex;width:18px;height:18px">${SVG.cloud}</span>
            Cloud Backup
          </h3>
          <span class="chip ${connected ? 'chip-success' : 'chip-error'}" style="font-size:11px;font-weight:600;padding:3px 10px;border-radius:6px">
            ${connected ? 'Connected' : 'Not connected'}
          </span>
        </div>
        ${connected ? `
          <div class="text-sm text-dim" style="margin-bottom:12px">
            Connected ${esc(status.connected_at || '')} · folder <strong>${esc(status.folder_name || 'BillBook Backups')}</strong> ·
            retention ${status.retention_days || 30} days<br>
            Last backup: <strong>${esc(status.last_backup_at || 'never')}</strong>
            ${status.last_backup_file ? ` (${esc(status.last_backup_file)})` : ''} ·
            Restore-test: ${status.last_restore_test_ok ? '<strong style="color:var(--success-text,#16A34A)">OK</strong>' : status.last_restore_test_at ? '<strong style="color:var(--danger-text,#DC2626)">FAILED</strong>' : 'never run'}
          </div>
          <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
            <label class="text-sm text-dim" for="gd-hour">Daily backup at</label>
            <select class="input" id="gd-hour" style="width:auto">${hours}</select>
            <button class="btn btn-primary btn-sm" id="gd-backup-now">
              <span style="display:inline-flex;width:13px;height:13px">${SVG.backup}</span> Backup Now
            </button>
            <button class="btn btn-secondary btn-sm" id="gd-restore-test">
              <span style="display:inline-flex;width:13px;height:13px">${SVG.check}</span> Run Restore-Test
            </button>
            <button class="btn btn-ghost btn-sm" id="gd-disconnect" style="color:var(--danger-text,#DC2626)">Disconnect</button>
          </div>
        ` : `
          <p class="text-sm text-dim" style="margin-bottom:12px">
            Connect your Google account to store encrypted daily database backups in your own Drive.
            BillBook can only see the folders it creates — never the rest of your Drive.
          </p>
          <button class="btn btn-primary" id="gd-connect">
            <span style="display:inline-flex;width:14px;height:14px">${SVG.cloud}</span>
            Connect Google Account
          </button>
        `}
      </div>

      <div class="card" style="padding:16px">
        <div class="card-title" style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
          <h3 style="display:flex;align-items:center;gap:8px">
            <span style="display:inline-flex;width:18px;height:18px">${SVG.upload}</span>
            POS Backup Auto-Import
          </h3>
          <label style="display:flex;align-items:center;gap:8px;cursor:pointer;user-select:none">
            <span class="text-sm">${ai.enabled ? 'On' : 'Off'}</span>
            <input type="checkbox" id="gd-ai-toggle" ${ai.enabled ? 'checked' : ''} style="width:18px;height:18px;cursor:pointer" ${connected ? '' : ''}>
          </label>
        </div>
        <div class="text-sm text-dim" style="margin-bottom:12px">
          Upload a POS backup zip (<code>BU*.zip</code>) to the <strong>${esc(ai.folder_name || 'BillBook POS Imports')}</strong>
          folder in your Google Drive — from your Ezi POS machine, phone, or any device — and BillBook imports it
          automatically every ${ai.check_interval_min || 15} minutes. Duplicates are skipped automatically (UNQCODE
          dedup), same as the manual import page. Processed files are moved to a <code>Processed</code> subfolder.
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:12px">
          <button class="btn btn-secondary btn-sm" id="gd-ai-check" ${connected && ai.enabled ? '' : 'disabled'}>
            <span style="display:inline-flex;width:13px;height:13px">${SVG.refresh}</span> Check Now
          </button>
          <span class="text-xs text-dim">Last check: ${esc(ai.last_check_at || 'never')}</span>
        </div>
        ${ai.last_result && ai.last_result.checked ? `
          <div class="text-xs text-dim" style="margin-bottom:12px;padding:8px 12px;background:var(--bg-2,#F1F5F9);border-radius:8px">
            Last run: ${ai.last_result.imported_files || 0} file(s) imported ·
            ${ai.last_result.imported_sales || 0} sales ·
            ${ai.last_result.imported_expenses || 0} expenses ·
            ${ai.last_result.skipped_duplicates || 0} duplicates skipped
            ${(ai.last_result.errors || []).length ? `<br><span style="color:var(--danger-text,#DC2626)">${esc(ai.last_result.errors[0])}</span>` : ''}
          </div>` : ''}
        ${recent ? `
          <div class="table-wrap"><table class="table">
            <thead><tr><th>File</th><th>When</th><th>Result</th></tr></thead>
            <tbody>${recent}</tbody>
          </table></div>` : `
          <div class="text-dim text-sm" style="padding:12px;text-align:center">
            No POS backups imported from Drive yet.
          </div>`}
      </div>`;

    wire(status, ai);
  }

  function wire(status, ai) {
    const connected = status.connected;

    // ── Connect flow: open Google consent in a popup, poll until connected ──
    const connectBtn = $('#gd-connect');
    if (connectBtn) connectBtn.onclick = async () => {
      if (connectBtn.disabled) return;
      try {
        const r = await api('/api/gdrive/connect-url');
        if (!r.url) { toast('Could not start Google sign-in', 'error'); return; }
        btnBusy(connectBtn, 'Waiting for Google…');
        window.open(r.url, 'billbook-gdrive', 'width=520,height=680');
        // Poll status until the OAuth callback lands (max 3 minutes).
        const deadline = Date.now() + 180000;
        const poll = setInterval(async () => {
          try {
            const s = await api('/api/gdrive/status');
            if (s.connected) {
              clearInterval(poll);
              toast('Google Drive connected', 'success');
              load();
            } else if (Date.now() > deadline) {
              clearInterval(poll);
              btnOk(connectBtn);
            }
          } catch (e) { /* keep polling */ }
        }, 2500);
      } catch (e) {
        toast('Could not start Google sign-in: ' + e.message, 'error');
        btnOk(connectBtn);
      }
    };

    // ── Daily backup hour ──
    const hourSel = $('#gd-hour');
    if (hourSel) hourSel.onchange = async () => {
      try {
        await apiPost('/api/gdrive/auto-backup', { hour: parseInt(hourSel.value, 10) });
        toast('Daily backup hour saved', 'success');
      } catch (e) { toast('Could not save: ' + e.message, 'error'); }
    };

    // ── Backup now ──
    const backupBtn = $('#gd-backup-now');
    if (backupBtn) backupBtn.onclick = async () => {
      if (!btnBusy(backupBtn, 'Backing up…')) return;
      try {
        const r = await apiPost('/api/gdrive/backup-now', {});
        toast(`Backup uploaded to Drive (${r.size_mb} MB)`, 'success');
        load();
      } catch (e) {
        toast('Backup failed: ' + e.message, 'error', 7000);
        btnOk(backupBtn);
      }
    };

    // ── Restore test ──
    const testBtn = $('#gd-restore-test');
    if (testBtn) testBtn.onclick = async () => {
      if (!btnBusy(testBtn, 'Testing…')) return;
      try {
        const r = await apiPost('/api/gdrive/restore-test', {});
        if (r.ok) toast(`Restore-test OK — ${r.file_name} is a healthy backup`, 'success', 6000);
        else toast('Restore-test FAILED: ' + (r.error || r.integrity_check || 'unknown'), 'error', 8000);
        load();
      } catch (e) {
        toast('Restore-test failed: ' + e.message, 'error', 7000);
        btnOk(testBtn);
      }
    };

    // ── Disconnect ──
    const discBtn = $('#gd-disconnect');
    if (discBtn) discBtn.onclick = async () => {
      if (!confirm('Disconnect Google Drive?\n\nDaily cloud backups and POS auto-import will stop. Your existing Drive files are NOT deleted.')) return;
      if (!btnBusy(discBtn, 'Disconnecting…')) return;
      try {
        await apiPost('/api/gdrive/disconnect', {});
        toast('Google Drive disconnected', 'info');
        load();
      } catch (e) {
        toast('Disconnect failed: ' + e.message, 'error');
        btnOk(discBtn);
      }
    };

    // ── Auto-import toggle ──
    const aiToggle = $('#gd-ai-toggle');
    if (aiToggle) aiToggle.onchange = async () => {
      try {
        await apiPost('/api/gdrive/auto-import', { enabled: aiToggle.checked });
        toast(aiToggle.checked
          ? 'Auto-import enabled — Drive folder is now watched'
          : 'Auto-import disabled', 'success');
        load();
      } catch (e) {
        toast('Could not save: ' + e.message, 'error');
        aiToggle.checked = !aiToggle.checked;
      }
    };

    // ── Check now ──
    const checkBtn = $('#gd-ai-check');
    if (checkBtn) checkBtn.onclick = async () => {
      if (!btnBusy(checkBtn, 'Checking Drive…')) return;
      try {
        const r = await apiPost('/api/gdrive/auto-import/check', {});
        if (r.ok && !r.errors?.length) {
          toast(r.imported_files
            ? `Imported ${r.imported_files} file(s): ${r.imported_sales} sales, ${r.imported_expenses} expenses`
            : 'No new POS backups in the Drive folder', r.imported_files ? 'success' : 'info', 6000);
        } else {
          toast('Check failed: ' + (r.errors?.[0] || 'unknown error'), 'error', 8000);
        }
        load();
      } catch (e) {
        toast('Check failed: ' + e.message, 'error', 7000);
        btnOk(checkBtn);
      }
    };
  }
});
