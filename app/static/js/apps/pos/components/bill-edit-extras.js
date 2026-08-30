// Bill Edit extras — validation flags + confirm flow (Phase 5 extraction)
// These functions are called from bill-edit.js's route handler via
// window.__initBillEditExtras(id, itemsBody). Wrapped in try-catch so
// import-time execution doesn't crash the module chain.
import { $, $$, esc, fmt, fmtRs, toast, showLoading, hideLoading, openModal, closeModal, icon } from '../../../utils.js';
import { api, apiPost, apiPut, apiDelete, apiUpload } from '../../../api.js';
import { navigate, reload } from '../../../router.js';

window.__initBillEditExtras = function(id, itemsBody) {
  try {
  function updateVerifyProgress() {
    const rows = $$('.item-row');
    const verified = $$('.item-row .i-verified:checked').length;
    const total = rows.length;
    const el = $('#verify-progress');
    if (el) {
      el.textContent = total > 0 ? `${verified}/${total} verified` : '';
      el.style.color = verified === total && total > 0 ? 'var(--success-text)' : 'var(--text-tertiary)';
    }
    // Update header checkbox
    const headerCb = $('#verify-all-cb');
    if (headerCb) headerCb.checked = total > 0 && verified === total;
  }

  // Bind verify checkbox changes (delegated)
  itemsBody.addEventListener('change', (e) => {
    if (e.target.classList.contains('i-verified')) {
      const tr = e.target.closest('tr');
      if (tr) tr.classList.toggle('verified', e.target.checked);
      updateVerifyProgress();
    }
  });

  // Verify all button
  $('#verify-all-btn').addEventListener('click', () => {
    const cbs = $$('.item-row .i-verified');
    const allChecked = cbs.length > 0 && cbs.every(cb => cb.checked);
    cbs.forEach(cb => {
      cb.checked = !allChecked;
      cb.closest('tr').classList.toggle('verified', !allChecked);
    });
    updateVerifyProgress();
  });

  // Verify all header checkbox
  $('#verify-all-cb').addEventListener('change', (e) => {
    $$('.item-row .i-verified').forEach(cb => {
      cb.checked = e.target.checked;
      cb.closest('tr').classList.toggle('verified', e.target.checked);
    });
    updateVerifyProgress();
  });

  updateVerifyProgress();

  // ---- Items search/filter ----
  const filterInput = $('#items-filter');
  let reviewMode = false;
  function applyFilter() {
    const q = filterInput.value.toLowerCase().trim();
    $$('.item-row').forEach(tr => {
      let show = true;
      if (q) {
        const raw = (tr.querySelector('.i-raw')?.value || '').toLowerCase();
        const code = (tr.querySelector('.i-code')?.value || '').toLowerCase();
        show = raw.includes(q) || code.includes(q);
      }
      if (show && reviewMode) {
        // In review mode, show ALL items that trigger any validation rule:
        // - Low confidence / very low confidence (AI flagged)
        // - Duplicates (same name + same price)
        // - Price = 0 (unread)
        // - Qty = 0 (unread)
        // - Price > 100,000 (unreasonably high)
        // - Qty > 1,000 (unreasonably high)
        // - Line total > 10x average (outlier)
        // - Not yet verified (so user can check them off)
        const isLowConf = tr.classList.contains('low-confidence') || tr.classList.contains('very-low-confidence');
        const isDup = tr.classList.contains('duplicate-item') || tr.classList.contains('duplicate-item-suspect');
        const isVerified = tr.classList.contains('verified');
        const price = parseFloat(tr.querySelector('.i-price')?.value) || 0;
        const qty = parseFloat(tr.querySelector('.i-qty')?.value) || 0;
        const unit = tr.querySelector('.i-unit')?.value || 'pcs';
        const pieces = unit === 'dozen' ? qty * 12 : qty;
        const lineTotal = parseFloat(tr.querySelector('.i-lt')?.textContent?.replace(/,/g, '')) || 0;

        // Compute average line total across all items for outlier check
        if (!window.__avgLineTotal) {
          let sum = 0, count = 0;
          $$('.item-row').forEach(r => {
            const lt = parseFloat(r.querySelector('.i-lt')?.textContent?.replace(/,/g, '')) || 0;
            if (lt > 0) { sum += lt; count++; }
          });
          window.__avgLineTotal = count > 0 ? sum / count : 0;
        }

        const hasIssue =
          isLowConf ||                          // AI confidence < 0.9
          isDup ||                               // Duplicate item
          price === 0 ||                         // Price unread
          qty === 0 ||                           // Qty unread
          price > 100000 ||                      // Unreasonably high price
          pieces > 1000 ||                       // Unreasonably high qty
          (window.__avgLineTotal > 0 && lineTotal > window.__avgLineTotal * 10);  // Outlier

        show = hasIssue && !isVerified;
      }
      tr.style.display = show ? '' : 'none';
    });
  }
  filterInput.addEventListener('input', applyFilter);

  // Review mode toggle
  $('#review-mode-btn').addEventListener('click', () => {
    reviewMode = !reviewMode;
    window.__avgLineTotal = null; // Force recalc
    const btn = $('#review-mode-btn');
    if (reviewMode) {
      btn.classList.add('btn-secondary');
      btn.classList.remove('btn-ghost');
      btn.innerHTML = `${icon('check', 12)} Review Mode ON`;
    } else {
      btn.classList.remove('btn-secondary');
      btn.classList.add('btn-ghost');
      btn.innerHTML = `${icon('alert', 12)} Review Mode`;
    }
    applyFilter();
  });

  // ---- Duplicate detection ----
  function detectDuplicates() {
    const rows = $$('.item-row');
    const seen = {}; // key = lowercased name
    const seenNamePrice = {}; // key = name + price
    let dupCount = 0;
    rows.forEach(tr => {
      tr.classList.remove('duplicate-item');
      const raw = (tr.querySelector('.i-raw')?.value || '').trim().toLowerCase();
      const price = tr.querySelector('.i-price')?.value || '0';
      if (!raw) return;
      // Same name = possible dup; same name + same price = likely dup
      const keyName = raw;
      const keyNamePrice = `${raw}:${price}`;
      if (seenNamePrice[keyNamePrice]) {
        tr.classList.add('duplicate-item');
        dupCount++;
      } else if (seen[keyName]) {
        // Same name but different price — weaker signal
        tr.classList.add('duplicate-item-suspect');
      }
      seen[keyName] = true;
      seenNamePrice[keyNamePrice] = true;
    });
    return dupCount;
  }

  // ---- Summary badges ----
  function updateSummary() {
    const rows = $$('.item-row');
    const total = rows.length;
    const verified = $$('.item-row.verified').length;
    const lowConf = $$('.item-row.low-confidence').length;
    const veryLowConf = $$('.item-row.very-low-confidence').length;
    const dups = $$('.item-row.duplicate-item').length;
    const suspects = $$('.item-row.duplicate-item-suspect').length;

    const badges = [];
    badges.push(`<span class="summary-badge">${total} item${total !== 1 ? 's' : ''}</span>`);
    if (verified > 0) badges.push(`<span class="summary-badge summary-ok">${verified} verified</span>`);
    if (veryLowConf > 0) badges.push(`<span class="summary-badge summary-danger">${veryLowConf} low confidence</span>`);
    else if (lowConf > 0) badges.push(`<span class="summary-badge summary-warning">${lowConf} review</span>`);
    if (dups > 0) badges.push(`<span class="summary-badge summary-danger">${dups} duplicate</span>`);
    if (suspects > 0) badges.push(`<span class="summary-badge summary-warning">${suspects} suspect</span>`);

    const el = $('#items-summary');
    if (el) el.innerHTML = badges.join('');
  }

  // Run duplicate detection + summary on any input change
  // Also update the DUP badges
  function updateDupBadges() {
    $$('.item-row').forEach(tr => {
      const existing = tr.querySelector('.duplicate-flag');
      if (tr.classList.contains('duplicate-item')) {
        if (!existing) {
          const raw = tr.querySelector('.i-raw');
          if (raw && raw.parentNode) {
            const badge = document.createElement('span');
            badge.className = 'duplicate-flag';
            badge.textContent = 'DUP';
            badge.title = 'Duplicate item — same name and price as another row';
            raw.parentNode.insertBefore(badge, raw.nextSibling);
          }
        }
      } else {
        if (existing) existing.remove();
      }
    });
  }
  itemsBody.addEventListener('input', () => {
    detectDuplicates();
    updateDupBadges();
    updateSummary();
  });
  detectDuplicates();
  updateDupBadges();
  updateSummary();

  // ---- Next unverified button ----
  $('#next-unverified-btn').addEventListener('click', () => {
    const unverified = $('.item-row:not(.verified) .i-verified');
    if (unverified) {
      unverified.checked = true;
      unverified.closest('tr').classList.add('verified');
      updateVerifyProgress();
      updateSummary();
      // Scroll to it
      unverified.closest('tr').scrollIntoView({ behavior: 'smooth', block: 'center' });
      toast('Item verified', 'success');
    } else {
      toast('All items verified!', 'success');
    }
  });

  // ---- Column sorting ----
  let sortState = { col: null, dir: 1 };
  $$('.sortable').forEach(th => {
    th.style.cursor = 'pointer';
    th.addEventListener('click', () => {
      const sortKey = th.dataset.sort;
      if (sortState.col === sortKey) {
        sortState.dir *= -1; // toggle direction
      } else {
        sortState.col = sortKey;
        sortState.dir = 1;
      }
      const rows = [...$$('.item-row')];
      rows.sort((a, b) => {
        let va, vb;
        if (sortKey === 'raw') {
          va = (a.querySelector('.i-raw')?.value || '').toLowerCase();
          vb = (b.querySelector('.i-raw')?.value || '').toLowerCase();
          return va.localeCompare(vb) * sortState.dir;
        } else if (sortKey === 'code') {
          va = (a.querySelector('.i-code')?.value || '').toLowerCase();
          vb = (b.querySelector('.i-code')?.value || '').toLowerCase();
          return va.localeCompare(vb) * sortState.dir;
        } else if (sortKey === 'price') {
          va = parseFloat(a.querySelector('.i-price')?.value) || 0;
          vb = parseFloat(b.querySelector('.i-price')?.value) || 0;
          return (va - vb) * sortState.dir;
        } else if (sortKey === 'qty') {
          va = parseFloat(a.querySelector('.i-qty')?.value) || 0;
          vb = parseFloat(b.querySelector('.i-qty')?.value) || 0;
          return (va - vb) * sortState.dir;
        } else if (sortKey === 'profit') {
          va = parseFloat(a.querySelector('.profit-value')?.textContent?.replace(/[^0-9-]/g, '')) || 0;
          vb = parseFloat(b.querySelector('.profit-value')?.textContent?.replace(/[^0-9-]/g, '')) || 0;
          return (va - vb) * sortState.dir;
        }
        return 0;
      });
      // Re-append in sorted order
      rows.forEach(r => itemsBody.appendChild(r));
      recalcGrand();
    });
  });

  // ---- Keyboard shortcuts for review ----
  // Only active when not typing in an input
  // v8.5.4: was `el.addEventListener` — `el` was not defined in this scope,
  // causing a ReferenceError that was silently caught by the outer try/catch,
  // which prevented the Save/Delete/Add-pages handlers below from registering.
  // Fixed: use `document` instead of the undefined `el`.
  document.addEventListener('keydown', (e) => {
    const tag = e.target.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;

    if (e.key === 'v' || e.key === 'V') {
      // Mark first unverified item as verified
      const unverified = $('.item-row:not(.verified) .i-verified');
      if (unverified) {
        unverified.checked = true;
        unverified.closest('tr').classList.add('verified');
        updateVerifyProgress();
        toast('Item verified', 'success');
      }
    } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      // Navigate between item rows
      e.preventDefault();
      const rows = $$('.item-row');
      if (!rows.length) return;
      const current = document.activeElement.closest('.item-row');
      let idx = current ? [...rows].indexOf(current) : -1;
      if (e.key === 'ArrowDown') idx = Math.min(idx + 1, rows.length - 1);
      else idx = Math.max(idx - 1, 0);
      const input = rows[idx].querySelector('.i-raw');
      if (input) input.focus();
    }
  });

  // Payment status toggle
  $('#f_payment').addEventListener('change', () => {
    const v = $('#f_payment').value;
    $('#due-row').style.display = v === 'credit' ? '' : 'none';
  });

  // Save button
  $('#save-btn').addEventListener('click', async () => {
    const items = $$('.item-row').map(tr => ({
      raw: tr.querySelector('.i-raw').value,
      item_code: tr.querySelector('.i-code').value,
      price: parseFloat(tr.querySelector('.i-price').value) || 0,
      qty: parseFloat(tr.querySelector('.i-qty').value) || 0,
      unit: tr.querySelector('.i-unit').value,
      category_id: tr.querySelector('.i-cat').value ? parseInt(tr.querySelector('.i-cat').value) : null,
      page_no: tr.dataset.page ? parseInt(tr.dataset.page) : null,
    }));
    const writtenTotal = $('#f_total').value === '' ? null : parseFloat($('#f_total').value);
    if (items.length === 0) {
      if (!confirm('Save this bill with no items?')) return;
    }
    showLoading('Saving...');
    try {
      await apiPost(`/api/bills/${id}/confirm`, {
        supplier_name: $('#f_supplier').value,
        phone: $('#f_phone').value,
        bill_date: $('#f_date').value,
        bill_no: $('#f_billno').value,
        written_total: writtenTotal,
        payment_status: $('#f_payment').value,
        credit_due_date: $('#f_payment').value === 'credit' ? $('#f_due').value : null,
        items,
      });
      hideLoading();
      toast('Bill saved successfully', 'success');
      // v8.5.4: redirect to bills list (not back to the same bill edit page)
      // so the user sees the confirmed status + can pick another bill.
      navigate('/bills');
    } catch (e) {
      hideLoading();
      toast('Save failed: ' + e.message, 'error');
    }
  });

  // Delete button
  $('#delete-btn').addEventListener('click', () => {
    openModal('Delete Bill', `<p>Delete <strong>Bill #${id}</strong>? You can undo this from the toast notification.</p>`,
      `<button class="btn btn-secondary" data-modal-close>Cancel</button>
       <button class="btn btn-danger" id="confirm-delete-btn">${icon('trash', 14)} Delete</button>`);
    document.getElementById('confirm-delete-btn').addEventListener('click', async () => {
      closeModal();
      showLoading('Deleting...');
      try {
        await apiDelete(`/api/bills/${id}`);
        hideLoading();
        toast(`Deleted bill #${id}`, 'success', {
          duration: 6000,
          action: {
            label: 'Undo',
            onClick: async () => {
              try {
                await apiPost(`/api/bills/${id}/restore`, {});
                toast(`Restored bill #${id}`, 'success');
                navigate('/bills/' + id);
              } catch (e) {
                toast('Restore failed: ' + e.message, 'error');
              }
            },
          },
        });
        navigate('/bills');
      } catch (e) {
        hideLoading();
        toast('Delete failed: ' + e.message, 'error');
      }
    });
  });

  // Add images button — v8.18.5: now an async job (render → AI-extract → merge
  // items into this bill). Live progress streams in a modal. On finish we
  // RELOAD the route — the old code called navigate() to the SAME hash, which
  // doesn't fire hashchange, so the page never re-rendered and the new image
  // (and its items) never appeared on screen.
  $('#add-pages-btn').addEventListener('click', () => {
    const input = document.createElement('input');
    input.type = 'file';
    input.multiple = true;
    input.accept = '.pdf,.png,.jpg,.jpeg,.webp';
    input.onchange = async () => {
      if (!input.files.length) return;
      const nFiles = input.files.length;
      const fd = new FormData();
      for (const f of input.files) fd.append('files', f);

      openModal(`Adding ${nFiles} file${nFiles > 1 ? 's' : ''} to Bill #${id}`, `
        <p class="text-sm text-dim" style="margin-top:0">New pages are rendered, read by AI, and their items merged into this bill. This can take a minute — keep this window open.</p>
        <div class="progress-container mt-3">
          <div class="progress-bar-wrap">
            <div class="progress-bar" id="ap-progress-bar" style="width:2%"></div>
          </div>
          <div class="flex justify-between mt-2 text-sm">
            <span id="ap-progress-stage" class="text-dim">Uploading…</span>
            <span id="ap-progress-pct" class="font-semibold">0%</span>
          </div>
        </div>
        <div class="mt-3">
          <div class="text-xs text-dim mb-2">Activity</div>
          <div id="ap-event-log" class="event-log" style="max-height:180px"></div>
        </div>`,
        `<button class="btn btn-secondary" data-modal-close>Close</button>`);

      let jobId = null;
      try {
        const r = await apiUpload(`/api/bills/${id}/add-pages`, fd);
        jobId = r.job_id;
      } catch (e) {
        closeModal();
        toast('Failed: ' + e.message, 'error');
        return;
      }

      const setStage = (msg, pct) => {
        const bar = $('#ap-progress-bar');
        const stage = $('#ap-progress-stage');
        const pctEl = $('#ap-progress-pct');
        if (bar && pct != null) bar.style.width = pct + '%';
        if (pctEl && pct != null) pctEl.textContent = pct + '%';
        if (stage && msg) stage.textContent = msg;
      };
      const logEvent = (msg, level) => {
        const log = $('#ap-event-log');
        if (!log) return;
        const color = level === 'error' ? 'text-danger' : level === 'warning' ? 'text-warning'
          : level === 'success' ? 'text-success' : 'text-dim';
        const ts = new Date().toLocaleTimeString();
        log.insertAdjacentHTML('afterbegin',
          `<div class="event-row ${color}"><span class="event-ts">${ts}</span><span class="event-msg">${esc(msg)}</span></div>`);
      };

      let finished = false;
      let es = null;
      // Reload the bill so the new images + extracted items appear.
      // If the user closed the progress modal early and stayed on the page,
      // DON'T force a reload (it would wipe their in-progress edits) — offer
      // a "Refresh" action on the toast instead.
      const finish = (ok, message) => {
        if (finished) return;
        finished = true;
        if (es) es.close();
        const stillOnModal = !!$('#ap-progress-bar');
        const stillOnBill = location.hash.startsWith('#/bills/' + id);
        closeModal();
        if (stillOnModal) {
          toast(message, ok ? 'success' : 'error');
          if (ok) reload();
        } else if (stillOnBill) {
          toast(message + ' — refresh to see them', ok ? 'success' : 'error', {
            duration: 10000,
            action: { label: 'Refresh', onClick: () => reload() },
          });
        } else {
          toast(message, ok ? 'success' : 'error');
        }
      };

      es = new EventSource(`/api/jobs/${jobId}/stream`);
      es.onmessage = (e) => {
        const d = JSON.parse(e.data);
        if (d.terminal) {
          if (d.status === 'done') {
            const r = d.result || {};
            const nItems = r.items_extracted || 0;
            finish(true, `Added ${r.added_pages || 0} page(s)` +
              (nItems ? `, extracted ${nItems} item(s)` : ', no items extracted'));
          } else {
            finish(false, 'Adding pages failed: ' + (d.error || 'unknown error'));
          }
          return;
        }
        setStage(d.message || d.stage, d.progress);
        logEvent(d.message || d.stage, d.level);
      };
      es.onerror = () => {
        // SSE dropped — poll the job status as a fallback (same pattern as
        // the new-bill upload page).
        if (finished) return;
        setTimeout(async () => {
          if (finished) return;
          try {
            const job = await api(`/api/jobs/${jobId}`);
            if (job.status === 'done' || job.status === 'error') {
              es.onmessage({ data: JSON.stringify({ terminal: true, status: job.status, result: job.result, error: job.error }) });
            }
          } catch {}
        }, 3000);
      };
    };
    input.click();
  });

  // Create Similar (duplicate as template)
  $('#duplicate-btn').addEventListener('click', async () => {
    if (!confirm(`Create a new bill as a copy of #${id}? Items and supplier will be copied; date and total left blank for you to fill in.`)) return;
    showLoading('Creating copy...');
    try {
      const r = await apiPost(`/api/bills/${id}/duplicate`, {});
      hideLoading();
      toast(`Created bill #${r.id} from #${id}`, 'success');
      navigate('/bills/' + r.id);
    } catch (e) {
      hideLoading();
      toast('Failed: ' + e.message, 'error');
    }
  });
  } catch (e) {
    console.warn('bill-edit-extras: not in bill-edit context', e.message);
  }
};
