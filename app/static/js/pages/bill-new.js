// New bill upload page with async + SSE progress
// Renders inside the Billing app shell — no internal topbar (shell provides it).
import { route, navigate } from '../router.js';
import { apiUpload, apiPost, api } from '../api.js';
import { $, toast, showLoading, hideLoading, esc, icon, iconHtml } from '../utils.js';

const SVG = {
  upload: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>',
  file: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>',
  image: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>',
  x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
  plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>',
  edit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>',
  brain: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96.44 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24 2.5 2.5 0 0 1 1.98-3A2.5 2.5 0 0 1 9.5 2Z"/><path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96.44 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-1.98-3A2.5 2.5 0 0 0 14.5 2Z"/></svg>',
  arrowLeft: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>',
};

route('/bills/new', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-success">${SVG.upload}</div>
      <div>
        <h2 class="pos-page-header-title">Upload New Bill</h2>
        <p class="pos-page-header-sub">Upload images or PDFs for AI-powered extraction.</p>
      </div>
    </div>

    <div class="card" id="upload-card">
      <div class="upload-zone" id="drop-zone">
        <div class="upload-zone-icon">${SVG.upload}</div>
        <h3>Drop files here or click to upload</h3>
        <p>Supports PDF, PNG, JPG, WebP &mdash; multiple files allowed (up to 20 files, 100 MB each)</p>
        <input type="file" id="file-input" multiple accept=".pdf,.png,.jpg,.jpeg,.webp" hidden>
        <button class="btn" id="choose-btn">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.file}</span>
          Choose Files
        </button>
      </div>

      <div id="selected-files" class="mt-4" style="display:none">
        <h4 style="margin-bottom:8px">Selected Files</h4>
        <div id="file-list"></div>
        <div class="flex gap-3 mt-4" style="justify-content:center">
          <button class="btn" id="upload-btn">
            <span style="display:inline-flex;width:14px;height:14px">${SVG.upload}</span>
            Start Upload &amp; Extraction
          </button>
          <button class="btn btn-secondary" id="clear-btn">Clear</button>
        </div>
      </div>

      <div class="flex gap-3 mt-6" style="justify-content:center">
        <button class="btn btn-secondary" id="manual-btn">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.edit}</span>
          Manual Entry Instead
        </button>
      </div>
    </div>

    <div id="progress-card" style="display:none"></div>

    <div class="card mt-4" style="padding:16px">
      <h3 style="margin-bottom:12px">How it works</h3>
      <div class="grid grid-3" style="gap:16px">
        <div style="padding:12px;background:var(--bg-2,#F8FAFC);border-radius:8px">
          <div class="kpi-label">
            <span style="display:inline-flex;width:12px;height:12px;vertical-align:-2px;margin-right:6px">${SVG.upload}</span>
            Step 1
          </div>
          <p class="mt-2 text-sm" style="line-height:1.5"><strong>Upload</strong> &mdash; Select bill images or PDFs from your device. Large PDFs are processed in chunks.</p>
        </div>
        <div style="padding:12px;background:var(--bg-2,#F8FAFC);border-radius:8px">
          <div class="kpi-label">
            <span style="display:inline-flex;width:12px;height:12px;vertical-align:-2px;margin-right:6px">${SVG.brain}</span>
            Step 2
          </div>
          <p class="mt-2 text-sm" style="line-height:1.5"><strong>AI Extracts</strong> &mdash; Gemini reads supplier, date, items, prices, and totals. Multi-page bills processed 3 pages at a time.</p>
        </div>
        <div style="padding:12px;background:var(--bg-2,#F8FAFC);border-radius:8px">
          <div class="kpi-label">
            <span style="display:inline-flex;width:12px;height:12px;vertical-align:-2px;margin-right:6px">${SVG.check}</span>
            Step 3
          </div>
          <p class="mt-2 text-sm" style="line-height:1.5"><strong>You Review</strong> &mdash; Edit any field, fix mismatches, then save. Extraction is a starting point, not the final word.</p>
        </div>
      </div>
    </div>`;

  const dropZone = $('#drop-zone');
  const fileInput = $('#file-input');
  let selectedFiles = [];

  $('#choose-btn').onclick = () => fileInput.click();
  dropZone.onclick = (e) => {
    if (e.target.tagName !== 'BUTTON' && !e.target.closest('button')) fileInput.click();
  };
  fileInput.onchange = () => {
    selectedFiles = [...fileInput.files];
    showSelectedFiles();
  };

  ['dragover', 'dragenter'].forEach(ev => dropZone.addEventListener(ev, (e) => {
    e.preventDefault();
    dropZone.classList.add('dragover');
  }));
  ['dragleave', 'drop'].forEach(ev => dropZone.addEventListener(ev, (e) => {
    e.preventDefault();
    dropZone.classList.remove('dragover');
  }));
  dropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    if (e.dataTransfer.files.length) {
      selectedFiles = [...e.dataTransfer.files];
      showSelectedFiles();
    }
  });

  function showSelectedFiles() {
    const totalSizeEl = $('#total-size');
    if (totalSizeEl) totalSizeEl.remove();

    if (!selectedFiles.length) {
      $('#selected-files').style.display = 'none';
      return;
    }
    const totalSize = selectedFiles.reduce((s, f) => s + f.size, 0);
    $('#file-list').innerHTML = selectedFiles.map((f, i) => {
      const sizeStr = f.size > 1024 * 1024 ? `${(f.size / 1024 / 1024).toFixed(1)} MB` : `${(f.size / 1024).toFixed(0)} KB`;
      const ext = f.name.split('.').pop().toLowerCase();
      const ico = ext === 'pdf' ? SVG.file : SVG.image;
      return `<div class="flex items-center gap-3" style="padding:8px;border:1px solid var(--border);border-radius:6px;margin-bottom:6px">
        <span style="display:inline-flex;width:18px;height:18px;color:var(--text-tertiary)">${ico}</span>
        <div style="flex:1">
          <div class="font-semibold">${esc(f.name)}</div>
          <div class="text-xs text-dim">${sizeStr}</div>
        </div>
        <button class="btn btn-ghost btn-sm btn-icon" data-remove="${i}" title="Remove">${SVG.x}</button>
      </div>`;
    }).join('');
    $('#selected-files').style.display = 'block';
    $('#selected-files').insertAdjacentHTML('beforebegin',
      `<div id="total-size" class="text-xs text-dim mb-2">Total: ${(totalSize / 1024 / 1024).toFixed(1)} MB &middot; ${selectedFiles.length} file(s)</div>`);

    $$('[data-remove]').forEach(b => b.onclick = () => {
      selectedFiles.splice(parseInt(b.dataset.remove), 1);
      showSelectedFiles();
    });
  }

  $('#clear-btn').onclick = () => {
    selectedFiles = [];
    fileInput.value = '';
    showSelectedFiles();
  };

  $('#upload-btn').onclick = async () => {
    if (!selectedFiles.length) {
      toast('Select files first', 'error');
      return;
    }
    const uploadCard = $('#upload-card');
    uploadCard.style.opacity = '0.5';
    uploadCard.style.pointerEvents = 'none';

    const progressCard = $('#progress-card');
    progressCard.style.display = 'block';
    progressCard.innerHTML = renderProgressInitial(selectedFiles);

    try {
      const fd = new FormData();
      for (const f of selectedFiles) fd.append('files', f);
      const r = await apiUpload('/api/upload-async', fd);
      streamProgress(r.job_id, r.bill_id);
    } catch (e) {
      toast('Upload failed: ' + e.message, 'error');
      uploadCard.style.opacity = '1';
      uploadCard.style.pointerEvents = 'auto';
      progressCard.style.display = 'none';
    }
  };

  function renderProgressInitial(files) {
    return `
      <div class="card">
        <div class="card-title">
          <h3>
            <span style="display:inline-flex;width:16px;height:16px;vertical-align:-3px;margin-right:6px">${SVG.brain}</span>
            Processing Upload
          </h3>
          <button class="btn btn-ghost btn-sm" id="cancel-btn" style="display:none">
            <span style="display:inline-flex;width:12px;height:12px">${SVG.x}</span>
            Cancel
          </button>
        </div>
        <div class="progress-container mt-4">
          <div class="progress-bar-wrap">
            <div class="progress-bar" id="progress-bar" style="width:0%"></div>
          </div>
          <div class="flex justify-between mt-2 text-sm">
            <span id="progress-stage" class="text-dim">Queued...</span>
            <span id="progress-pct" class="font-semibold">0%</span>
          </div>
        </div>
        <div class="mt-4">
          <div class="text-xs text-dim mb-2">Activity Log</div>
          <div id="event-log" class="event-log"></div>
        </div>
        <div class="mt-4" id="error-detail" style="display:none"></div>
        <div class="mt-4" id="success-actions" style="display:none"></div>
      </div>`;
  }

  function streamProgress(jobId, billId) {
    const es = new EventSource(`/api/jobs/${jobId}/stream`);
    window.__currentEventSource = es;
    $('#cancel-btn').style.display = 'inline-flex';

    es.onmessage = (e) => {
      const d = JSON.parse(e.data);
      if (d.terminal) {
        es.close();
        window.__currentEventSource = null;
        $('#cancel-btn').style.display = 'none';
        if (d.status === 'done') {
          const result = d.result || {};
          $('#success-actions').style.display = 'block';
          $('#success-actions').innerHTML = `
            <div class="alert alert-success">
              <span style="display:inline-flex;width:18px;height:18px;margin-right:8px">${SVG.check}</span>
              <div><strong>Extraction complete!</strong>
                <div class="text-sm mt-2">${result.items_count || 0} items extracted via ${esc(result.provider || 'AI')}</div>
              </div>
            </div>
            <div class="flex gap-3 mt-4">
              <button class="btn" id="success-review-btn">
                <span style="display:inline-flex;width:14px;height:14px">${SVG.check}</span>
                Review Bill
              </button>
              <button class="btn btn-secondary" id="success-another-btn">
                <span style="display:inline-flex;width:14px;height:14px">${SVG.plus}</span>
                Upload Another
              </button>
            </div>`;
          $('#success-review-btn').onclick = () => navigate('/bills/' + billId);
          $('#success-another-btn').onclick = () => navigate('/bills/new');
          setTimeout(() => { if (location.hash !== `#/bills/${billId}`) navigate('/bills/' + billId); }, 2000);
        } else if (d.status === 'error') {
          $('#error-detail').style.display = 'block';
          $('#error-detail').innerHTML = `
            <div class="alert alert-danger">
              <span style="display:inline-flex;width:18px;height:18px;margin-right:8px">${SVG.x}</span>
              <div><strong>Extraction failed</strong>
                <div class="text-sm mt-2">${esc(d.error || 'Unknown error')}</div>
                <div class="text-xs text-dim mt-2">You can still review the bill and enter data manually.</div>
              </div>
            </div>
            <div class="flex gap-3 mt-4">
              <button class="btn" id="error-manual-btn">
                <span style="display:inline-flex;width:14px;height:14px">${SVG.arrowLeft}</span>
                Enter Manually
              </button>
              <button class="btn btn-secondary" id="error-retry-btn">
                <span style="display:inline-flex;width:14px;height:14px">${SVG.upload}</span>
                Try Again
              </button>
            </div>`;
          $('#error-manual-btn').onclick = () => navigate('/bills/' + billId);
          $('#error-retry-btn').onclick = () => navigate('/bills/new');
        }
        return;
      }

      if (d.progress !== null && d.progress !== undefined) {
        $('#progress-bar').style.width = d.progress + '%';
        $('#progress-pct').textContent = d.progress + '%';
      }
      $('#progress-stage').textContent = d.message || d.stage;

      const log = $('#event-log');
      const level = d.level || 'info';
      const color = level === 'error' ? 'text-danger' : level === 'warning' ? 'text-warning' : level === 'success' ? 'text-success' : 'text-dim';
      const ts = new Date(d.ts * 1000).toLocaleTimeString();
      log.innerHTML = `<div class="event-row ${color}"><span class="event-ts">${ts}</span><span class="event-msg">${esc(d.message)}</span></div>` + log.innerHTML;
    };

    es.onerror = () => {
      $('#event-log').insertAdjacentHTML('afterbegin',
        `<div class="event-row text-warning"><span class="event-ts">${new Date().toLocaleTimeString()}</span><span class="event-msg">Connection lost. Retrying...</span></div>`);
      setTimeout(async () => {
        try {
          const job = await api(`/api/jobs/${jobId}`);
          if (job.status === 'done' || job.status === 'error') {
            es.close();
            const ev = { data: JSON.stringify({ terminal: true, status: job.status, result: job.result, error: job.error }) };
            es.onmessage(ev);
          }
        } catch {}
      }, 3000);
    };
  }

  $('#cancel-btn') && ($('#cancel-btn').onclick = () => {
    // The button is recreated on each render; this handler may not always be wired.
    // Use a delegation pattern below.
  });
  // Use event delegation for cancel-btn since it's recreated dynamically
  document.addEventListener('click', function cancelHandler(e) {
    const btn = e.target.closest('#cancel-btn');
    if (!btn) return;
    if (window.__currentEventSource) {
      window.__currentEventSource.close();
      window.__currentEventSource = null;
    }
    toast('Upload cancelled', 'info');
    const progressCard = $('#progress-card');
    if (progressCard) progressCard.style.display = 'none';
    const uploadCard = $('#upload-card');
    if (uploadCard) {
      uploadCard.style.opacity = '1';
      uploadCard.style.pointerEvents = 'auto';
    }
    document.removeEventListener('click', cancelHandler);
  });

  $('#manual-btn').onclick = async () => {
    showLoading('Creating blank bill...');
    try {
      const r = await apiPost('/api/bills/empty', {});
      hideLoading();
      navigate('/bills/' + r.id);
    } catch (e) {
      hideLoading();
      toast('Error: ' + e.message, 'error');
    }
  };
});
