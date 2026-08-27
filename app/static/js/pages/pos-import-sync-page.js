// v8.2.3 — POS Backup Import page
// Import daily backup zip from third-party Ezi POS system. Dedup via UNQCODE.
import { route } from '../router.js';
import { api, apiPost, apiUpload, apiDelete } from '../api.js';
import { $, esc, fmtRs, fmtDate, toast, openModal, closeModal, skeletonCards, errorBox } from '../utils.js';

const SVG = {
  upload: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>',
  alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>',
  info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>',
  refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>',
};

route('/pos-import', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-info">${SVG.upload}</div>
      <div>
        <h2 class="pos-page-header-title">POS Backup Import</h2>
        <p class="pos-page-header-sub">Import daily backup zip from your Ezi POS. Duplicates are automatically skipped — safe to re-import.</p>
      </div>
      <div class="pos-page-header-actions">
        <button class="btn btn-secondary btn-sm" id="pi-refresh">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.refresh}</span>
          Refresh
        </button>
      </div>
    </div>
    <div id="pi-out">${skeletonCards(2)}</div>`;

  $('#pi-refresh').onclick = loadSummary;
  await loadSummary();

  async function loadSummary() {
    try {
      const [summary, status] = await Promise.all([
        api('/api/pos-import/summary'),
        api('/api/pos-import/status'),
      ]);
      renderPage(summary, status);
    } catch (e) {
      $('#pi-out').innerHTML = errorBox(e.message);
    }
  }

  function renderPage(summary, status) {
    const available = status.available;
    const totalRecords = summary.total_imported_records || 0;
    const totalAmount = summary.total_imported_amount || 0;
    const recentImports = summary.recent_imports || [];
    // v8.5: prefer the structured import_runs (pos_imports table) over activity_log
    const importRuns = summary.import_runs || [];

    let importsHtml = '';
    if (importRuns.length === 0 && recentImports.length === 0) {
      importsHtml = '<div class="text-dim text-sm" style="padding:16px;text-align:center">No imports yet. Upload your first backup below.</div>';
    } else if (importRuns.length > 0) {
      // v8.5: render the structured pos_imports table — has sale_count, expense_count,
      // total_revenue, total_cogs, date_range, status, notes (warnings)
      importsHtml = `<table class="table">
        <thead><tr>
          <th>When</th><th>Source</th><th>File</th><th>Date Range</th>
          <th>Sales</th><th>Expenses</th><th>Revenue</th><th>COGS</th>
          <th>Warnings</th><th>Status</th><th></th>
        </tr></thead>
        <tbody>
          ${importRuns.map(run => {
            const warnings = (run.notes || '').split(';').filter(x => x.trim()).length;
            const warningBadge = warnings > 0
              ? `<span class="chip chip-warning" title="${esc(run.notes || '')}" style="display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:6px;background:var(--warning-soft,#FEF3C7);color:var(--warning-text,#D97706);font-size:11px;font-weight:600">${SVG.alert}<span>${warnings}</span></span>`
              : '<span class="text-dim text-xs">—</span>';
            const statusBadge = run.status === 'deleted'
              ? '<span class="chip chip-error" style="display:inline-block;padding:2px 8px;border-radius:6px;background:var(--danger-soft,#FEE2E2);color:var(--danger-text,#DC2626);font-size:11px;font-weight:600">deleted</span>'
              : run.status === 'importing'
              ? '<span class="chip chip-info" style="display:inline-block;padding:2px 8px;border-radius:6px;background:var(--accent-soft,#DBEAFE);color:var(--accent-text,#2563EB);font-size:11px;font-weight:600">importing</span>'
              : '<span class="chip chip-success" style="display:inline-block;padding:2px 8px;border-radius:6px;background:var(--success-soft,#f0fdf4);color:var(--success-text,#16A34A);font-size:11px;font-weight:600">imported</span>';
            const canDelete = run.status !== 'deleted';
            const canSync = run.status === 'imported';
            return `<tr ${run.status === 'deleted' ? 'style="opacity:0.5"' : ''}>
              <td class="text-sm">${esc(fmtDate(run.created_at))}</td>
              <td class="text-sm">${esc(run.source_name || '?')}</td>
              <td class="text-sm"><code>${esc(run.filename || '?')}</code></td>
              <td class="text-xs text-dim">${run.date_range_start || '—'} → ${run.date_range_end || '—'}</td>
              <td><strong>${run.sale_count || 0}</strong></td>
              <td>${run.expense_count || 0}</td>
              <td style="font-weight:600">${fmtRs(run.total_revenue || 0)}</td>
              <td class="text-dim text-sm">${fmtRs(run.total_cogs || 0)}</td>
              <td>${warningBadge}</td>
              <td>${statusBadge}</td>
              <td style="white-space:nowrap">
                <button class="btn btn-ghost btn-sm btn-icon" data-drill-run="${run.id}" title="View imported invoices" style="margin-right:4px">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:12px;height:12px"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                </button>
                ${canSync ? `<button class="btn btn-secondary btn-sm" data-sync-exp-deletions="${run.id}" title="Detect and sync expenses deleted in EZI POS" style="margin-right:4px;font-size:11px;padding:4px 8px">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:11px;height:11px"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
                  Sync Deleted Expenses
                </button>` : ''}
                ${canDelete ? `<button class="btn btn-ghost btn-sm btn-icon" data-delete-run="${run.id}" title="Delete import and all its data">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:12px;height:12px"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                </button>` : ''}
              </td>
            </tr>`;
          }).join('')}
        </tbody>
      </table>`;
    } else {
      // Fallback: legacy activity_log view
      importsHtml = `<table class="table">
        <thead><tr><th>When</th><th>File</th><th>Sales</th><th>Expenses</th><th>Skipped</th><th>Total</th><th></th></tr></thead>
        <tbody>
          ${recentImports.map(imp => {
            const meta = JSON.parse(imp.metadata || '{}');
            return `<tr>
              <td class="text-sm">${esc(fmtDate(imp.created_at))}</td>
              <td class="text-sm"><code>${esc(meta.backup_file || '?')}</code></td>
              <td>${meta.sales_imported || 0}</td>
              <td>${meta.expenses_imported || 0}</td>
              <td class="text-dim">${meta.skipped || 0}</td>
              <td style="font-weight:600">${fmtRs(meta.total_sales_amount || 0)}</td>
              <td><button class="btn btn-ghost btn-sm btn-icon" data-delete-import="${imp.id}" title="Delete import and all its data"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:12px;height:12px"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg></button></td>
            </tr>`;
          }).join('')}
        </tbody>
      </table>`;
    }

    $('#pi-out').innerHTML = `
      ${!available ? `<div class="card" style="padding:12px;background:var(--danger-soft,#FEE2E2);border:1px solid var(--danger,#DC2626);margin-bottom:16px">
        <strong style="color:var(--danger-text,#DC2626)">dbfread library not installed.</strong>
        Run: <code>pip install dbfread</code> to enable POS backup import.
      </div>` : ''}

      <div class="grid grid-3" style="gap:12px;margin-bottom:16px">
        <div class="card" style="padding:16px;text-align:center">
          <div class="text-dim text-sm" style="text-transform:uppercase;font-weight:600">Imported Sales</div>
          <div style="font-size:28px;font-weight:700;margin-top:4px">${totalRecords}</div>
        </div>
        <div class="card" style="padding:16px;text-align:center">
          <div class="text-dim text-sm" style="text-transform:uppercase;font-weight:600">Total Amount</div>
          <div style="font-size:28px;font-weight:700;margin-top:4px;color:var(--success-text,#16A34A)">${fmtRs(totalAmount)}</div>
        </div>
        <div class="card" style="padding:16px;text-align:center">
          <div class="text-dim text-sm" style="text-transform:uppercase;font-weight:600">Import Runs</div>
          <div style="font-size:28px;font-weight:700;margin-top:4px">${recentImports.length}</div>
        </div>
      </div>

      <div class="card" style="padding:16px;margin-bottom:16px">
        <h3 style="margin:0 0 12px">Upload Backup Zip</h3>
        <div class="card" style="padding:12px;background:var(--bg-2,#F1F5F9);margin-bottom:12px;display:flex;gap:8px;align-items:flex-start">
          <span style="display:inline-flex;width:16px;height:16px;color:var(--text-dim,#64748B);flex-shrink:0;margin-top:2px">${SVG.info}</span>
          <div class="text-sm text-dim">
            <strong>How it works:</strong> Each daily backup (BU*.zip) from your Ezi POS contains the full database.
            BillBook uses the <code>UNQCODE</code> field to skip records already imported — so re-importing the same
            backup or importing a newer one (which contains all old + new records) never duplicates data.
            Safe to upload every day.
          </div>
        </div>
        <div class="card" style="padding:12px;background:var(--success-soft,#f0fdf4);border-left:3px solid var(--success,#16a34a);margin-bottom:12px;display:flex;gap:8px;align-items:flex-start">
          <span style="display:inline-flex;width:16px;height:16px;color:var(--success,#16a34a);flex-shrink:0;margin-top:2px">${SVG.check}</span>
          <div class="text-sm">
            <strong>v8.16.8 — what gets synced automatically on each import:</strong>
            <ul style="margin:4px 0 0 18px;padding:0;line-height:1.6">
              <li><strong>New sales/expenses</strong> in EZI POS → added to BillBook</li>
              <li><strong>Modified expenses</strong> in EZI POS (amount, description, date changed) → updated in BillBook + cash drawer auto-adjusted for the difference</li>
              <li><strong>Expenses you added manually</strong> in BillBook (e.g. owner draws, custom expenses) → <strong>never touched</strong> by POS imports (only EZI-imported expenses are synced)</li>
            </ul>
            To <strong>delete expenses that were removed in EZI POS</strong>, click the
            <strong>"Sync Deleted Expenses"</strong> button on each import run — that step requires
            manager PIN and is not automatic (because deletions are irreversible).
          </div>
        </div>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <input type="file" id="pi-file" accept=".zip" class="input" style="flex:1;min-width:200px" ${!available ? 'disabled' : ''}>
          <button class="btn btn-primary" id="pi-upload-btn" ${!available ? 'disabled' : ''}>
            <span style="display:inline-flex;width:14px;height:14px">${SVG.upload}</span>
            Import Backup
          </button>
        </div>
        <div id="pi-progress" style="display:none;margin-top:12px">
          <div style="padding:12px;background:var(--accent-soft,#DBEAFE);border-radius:8px;color:var(--accent-text,#2563EB);font-size:14px">
            <span style="display:inline-flex;width:16px;height:16px;animation:spin 1s linear infinite;vertical-align:middle;margin-right:8px">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>
            </span>
            <span id="pi-progress-text">Importing...</span>
          </div>
        </div>
        <div id="pi-result" style="margin-top:12px"></div>
      </div>

      <div class="card" style="padding:16px">
        <h3 style="margin:0 0 12px">Recent Imports</h3>
        ${importsHtml}
      </div>`;

    // Wire upload
    const uploadBtn = $('#pi-upload-btn');
    const fileInput = $('#pi-file');
    if (uploadBtn && available) {
      uploadBtn.onclick = async () => {
        const file = fileInput.files[0];
        if (!file) { toast('Select a backup zip file first', 'error'); return; }
        if (!file.name.toLowerCase().endsWith('.zip')) { toast('File must be a .zip archive', 'error'); return; }
        // Show progress
        $('#pi-progress').style.display = 'block';
        $('#pi-progress-text').textContent = `Importing ${file.name}...`;
        $('#pi-result').innerHTML = '';
        uploadBtn.disabled = true;
        try {
          const formData = new FormData();
          formData.append('file', file);
          const r = await apiUpload('/api/pos-import/upload', formData);
          $('#pi-progress').style.display = 'none';
          // Show result
          const warningsHtml = (r.warnings && r.warnings.length > 0) ? `
            <details style="margin-top:8px">
              <summary style="cursor:pointer;font-size:12px;color:var(--warning-text,#D97706);font-weight:600">
                ${SVG.alert} ${r.warning_count} warning(s) — click to view
              </summary>
              <div style="margin-top:6px;padding:8px;background:var(--warning-soft,#FEF3C7);border-radius:6px;font-size:11px;color:var(--text);max-height:200px;overflow:auto">
                ${r.warnings.slice(0, 30).map(w => `<div style="padding:2px 0;border-bottom:1px solid rgba(0,0,0,0.05)">${esc(w)}</div>`).join('')}
              </div>
            </details>` : '';
          $('#pi-result').innerHTML = `<div class="card" style="padding:12px;background:var(--success-soft,#f0fdf4);border:1px solid var(--success,#16A34A)">
            <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px">
              <span style="display:inline-flex;width:18px;height:18px;color:var(--success-text,#16A34A)">${SVG.check}</span>
              <strong style="color:var(--success-text,#16A34A)">Import complete</strong>
              ${r.import_run_id ? `<span class="chip chip-info" style="margin-left:auto;font-size:11px;padding:2px 8px;background:var(--accent-soft,#DBEAFE);color:var(--accent-text,#2563EB);border-radius:6px">Run #${r.import_run_id}</span>` : ''}
            </div>
            <div style="font-size:13px;color:var(--text)">
              <strong>${r.imported_sales}</strong> sales imported ·
              <strong>${r.imported_expenses}</strong> new expenses ·
              ${r.updated_expenses ? `<strong style="color:var(--warning-text,#D97706)">${r.updated_expenses}</strong> expenses updated · ` : ''}
              <strong>${r.skipped_duplicates}</strong> duplicates skipped<br>
              Backup date: ${r.backup_date} · Shop: ${r.shop_name} · Total: ${fmtRs(r.total_sales_amount)}
              ${r.total_cogs ? ` · COGS: ${fmtRs(r.total_cogs)}` : ''}
            </div>
            ${r.sales_by_date && Object.keys(r.sales_by_date).length > 0 ? `
              <details style="margin-top:8px"><summary style="cursor:pointer;font-size:12px;color:var(--text-dim)">Sales by date</summary>
              <div style="margin-top:4px;font-size:12px">
                ${Object.entries(r.sales_by_date).map(([d, v]) => `<div>${d}: ${fmtRs(v)}</div>`).join('')}
              </div></details>` : ''}
            ${warningsHtml}
          </div>`;
          toast(
            `Imported ${r.imported_sales} sales, ${r.imported_expenses} new expenses` +
            (r.updated_expenses ? `, ${r.updated_expenses} updated` : ''),
            'success', 5000
          );
          await loadSummary();
        } catch (e) {
          $('#pi-progress').style.display = 'none';
          $('#pi-result').innerHTML = `<div class="card" style="padding:12px;background:var(--danger-soft,#FEE2E2);border:1px solid var(--danger,#DC2626)">
            <strong style="color:var(--danger-text,#DC2626)">Import failed: ${esc(e.message)}</strong>
          </div>`;
        }
        uploadBtn.disabled = false;
      };
    }

    // v8.5: Wire delete import buttons (new: data-delete-run uses /by-id/{run_id})
    document.querySelectorAll('[data-delete-run]').forEach(btn => {
      btn.onclick = async () => {
        const runId = btn.dataset.deleteRun;
        if (!confirm('Delete this import and ALL its data?\n\nThis will permanently remove:\n• All sales from this import\n• All sale items\n• All cash drawer entries\n• All expenses from this import\n• Stock state will be reversed\n• Customer totals will be reversed\n\nThis cannot be undone.')) return;
        try {
          const r = await apiDelete(`/api/pos-import/by-id/${runId}`);
          toast(`Deleted: ${r.deleted_sales} sales, ${r.deleted_items} items, ${r.deleted_expense_imports} expenses, ${r.stock_reversed} stock reversals`, 'success');
          loadSummary();
        } catch (e) {
          toast('Delete failed: ' + e.message, 'error');
        }
      };
    });

    // v8.16.7: Wire Sync Deleted Expenses buttons
    document.querySelectorAll('[data-sync-exp-deletions]').forEach(btn => {
      btn.onclick = async () => {
        const runId = btn.dataset.syncExpDeletions;
        // Step 1: Detect (dry-run)
        try {
          const r = await apiPost(`/api/pos-import/detect-expense-deletions/${runId}`, {});
          if (r.missing_count === 0) {
            toast(`No deleted expenses detected. (${r.total_imported} expenses checked)`, 'info', 5000);
            return;
          }
          // Show summary
          const details = r.missing_expenses.map(e =>
            `• Rs ${e.amount.toFixed(2)} — ${e.description} (${e.date})`
          ).join('\n');
          const proceed = confirm(
            `Found ${r.missing_count} expense(s) deleted in EZI POS:\n\n${details}\n\n` +
            `Total to reverse: Rs ${r.missing_total_amount.toFixed(2)}\n\n` +
            (r.high_risk ? `⚠️ HIGH RISK: more than ${r.threshold_pct}% of expenses are missing.\n` : '') +
            `Click OK to enter your Manager PIN and apply these deletions.`
          );
          if (!proceed) return;
          // Step 2: Ask for PIN
          const pin = prompt(`Enter Manager PIN to apply ${r.missing_count} expense deletions:`);
          if (!pin) return;
          // Step 3: Apply
          const r2 = await apiPost('/api/pos-import/apply-expense-deletions', {
            missing_expenses: r.missing_expenses,
            import_run_id: parseInt(runId),
            manager_pin: pin,
            confirm: true,
          });
          toast(
            `Synced: ${r2.applied} deleted, ${r2.skipped} already gone, ` +
            `Rs ${r2.reversed_amount.toFixed(2)} reversed in cash drawer.`,
            'success', 6000
          );
          loadSummary();
        } catch (e) {
          toast('Expense sync failed: ' + e.message, 'error');
        }
      };
    });

    // v8.5: Wire drill-down buttons — show imported invoices for this run
    document.querySelectorAll('[data-drill-run]').forEach(btn => {
      btn.onclick = async () => {
        const runId = btn.dataset.drillRun;
        try {
          const r = await api(`/api/pos-import/run/${runId}`);
          const rows = (r.sales || []).map(s => `<tr>
            <td><code>${esc(s.invoice_no || '')}</code></td>
            <td>${esc(s.customer_name || 'Walk-in')}</td>
            <td>${esc(s.payment_method || '')} · ${esc(s.payment_status || '')}</td>
            <td style="font-weight:600">${fmtRs(s.total || 0)}</td>
            <td class="text-xs text-dim">${esc(s.created_at || '')}</td>
            <td><a href="#/pos/sale/${s.id}" class="btn btn-ghost btn-sm" onclick="closeModal()">View</a></td>
          </tr>`).join('');
          openModal(
            `<div style="display:flex;justify-content:space-between;align-items:center">
               <h3 style="margin:0">Import Run #${runId}</h3>
               <span class="text-dim text-sm">${esc(r.run.filename || '')} · ${r.sale_count} sales</span>
             </div>
             <div style="margin-top:12px;max-height:60vh;overflow:auto">
               ${rows ? `<table class="table"><thead><tr>
                 <th>Invoice</th><th>Customer</th><th>Payment</th><th>Total</th><th>When</th><th></th>
               </tr></thead><tbody>${rows}</tbody></table>` : '<div class="text-dim text-sm" style="padding:16px">No sales in this run.</div>'}
             </div>`,
            `<button class="btn btn-secondary" data-modal-close>Close</button>`
          );
        } catch (e) {
          toast('Failed to load import details: ' + e.message, 'error');
        }
      };
    });

    // Legacy delete-by-activity-log-id buttons (fallback for old summary rows)
    document.querySelectorAll('[data-delete-import]').forEach(btn => {
      btn.onclick = async () => {
        const importId = btn.dataset.deleteImport;
        if (!confirm('Delete this import and ALL its data?\n\nThis will permanently remove:\n• All sales from this import\n• All sale items\n• All cash drawer entries\n• All expenses from this import\n\nThis cannot be undone.')) return;
        try {
          const r = await apiDelete(`/api/pos-import/${importId}`);
          toast(`Deleted: ${r.deleted_sales} sales, ${r.deleted_items} items, ${r.deleted_expense_imports} expenses`, 'success');
          loadSummary();
        } catch (e) {
          toast('Delete failed: ' + e.message, 'error');
        }
      };
    });
  }
});
