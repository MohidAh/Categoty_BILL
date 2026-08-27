// BillBook Help System — Help page + floating help button
// Provides searchable FAQ articles + AI-powered "Ask AI" chat assistant.

import { route } from '../router.js';
import { api, apiPost } from '../api.js';
import { $, esc, toast, openModal, closeModal, skeletonCards, errorBox } from '../utils.js';

const SVG = {
  help: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>',
  send: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>',
  close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
  sparkles: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1"/></svg>',
  chevron: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"/></svg>',
};

// ─── Help Page (/help) ───────────────────────────────────────────
route('/help', async (el) => {
  el.innerHTML = `
    <div class="pos-page-header">
      <div class="pos-page-header-icon chip-info">${SVG.help}</div>
      <div>
        <h2 class="pos-page-header-title">Help & Guide</h2>
        <p class="pos-page-header-sub">Search how-to articles or ask the AI assistant anything about BillBook.</p>
      </div>
    </div>

    <div class="card mb-4" style="padding:16px">
      <div class="search-input" style="max-width:600px;margin:0 auto">
        ${SVG.search}
        <input class="input" id="help-search" placeholder="Search articles... (e.g., 'make sale', 'refund', 'pair phone')" style="padding-left:36px">
      </div>
    </div>

    <div id="help-ai-section" class="card mb-4" style="padding:16px;border:2px solid var(--accent,#2563EB)">
      <div style="display:flex;gap:12px;align-items:center;margin-bottom:12px">
        <div style="width:36px;height:36px;background:var(--accent-soft,#DBEAFE);color:var(--accent-text,#2563EB);border-radius:10px;display:flex;align-items:center;justify-content:center">
          <span style="display:inline-flex;width:18px;height:18px">${SVG.sparkles}</span>
        </div>
        <div>
          <strong>Ask AI</strong>
          <div class="text-dim text-sm">Type any question — about the system, your business, or accounting</div>
        </div>
      </div>
      <div style="display:flex;gap:8px;max-width:600px">
        <input class="input" id="help-ai-input" placeholder="e.g., 'How do I process a refund?' or 'What is COGS?'" style="flex:1">
        <button class="btn btn-primary" id="help-ai-btn">
          <span style="display:inline-flex;width:14px;height:14px">${SVG.send}</span>
          Ask
        </button>
      </div>
      <div id="help-ai-answer" style="margin-top:12px"></div>
    </div>

    <div id="help-articles">${skeletonCards(3)}</div>`;

  let allArticles = [];
  let categories = [];

  // Load articles
  try {
    const r = await api('/api/help/articles');
    allArticles = r.articles || [];
    categories = r.categories || [];
    renderArticles(allArticles);
  } catch (e) {
    $('#help-articles').innerHTML = errorBox(e.message);
  }

  // Search filter
  $('#help-search').oninput = (e) => {
    const q = e.target.value.toLowerCase();
    if (!q) { renderArticles(allArticles); return; }
    const filtered = allArticles.filter(a =>
      a.question.toLowerCase().includes(q) ||
      a.keywords.some(k => k.toLowerCase().includes(q)) ||
      a.answer.toLowerCase().includes(q)
    );
    renderArticles(filtered);
  };

  // AI ask
  $('#help-ai-btn').onclick = askAI;
  $('#help-ai-input').onkeydown = (e) => { if (e.key === 'Enter') askAI(); };

  async function askAI() {
    const question = $('#help-ai-input').value.trim();
    if (!question) return;
    $('#help-ai-answer').innerHTML = '<div class="text-dim text-sm">Thinking...</div>';
    $('#help-ai-btn').disabled = true;
    try {
      const r = await apiPost('/api/help/ask', { question });
      const sourceBadge = r.source === 'faq' ? '<span class="chip chip-success chip-sm">FAQ Match</span>'
        : r.source === 'ai' ? '<span class="chip chip-info chip-sm">AI Answer</span>'
        : r.source === 'faq_fuzzy' ? '<span class="chip chip-warning chip-sm">Close Match</span>'
        : '';
      const staleBadge = r.stale ? ' <span class="chip chip-warning chip-sm">Cached</span>' : '';
      const suggestions = (r.suggestions || []).map(s =>
        `<button class="btn btn-secondary btn-sm" style="margin:4px 4px 0 0" data-suggestion="${esc(s)}">${esc(s)}</button>`
      ).join('');
      $('#help-ai-answer').innerHTML = `
        <div style="padding:12px;background:var(--bg-2,#F8FAFC);border-radius:8px;border-left:3px solid var(--accent,#2563EB)">
          <div style="margin-bottom:8px">${sourceBadge}${staleBadge}</div>
          <div style="white-space:pre-wrap;line-height:1.5">${esc(r.answer)}</div>
          ${suggestions ? `<div style="margin-top:12px"><div class="text-dim text-sm" style="margin-bottom:4px">Related:</div>${suggestions}</div>` : ''}
        </div>`;
      // Wire suggestion buttons
      document.querySelectorAll('[data-suggestion]').forEach(btn => {
        btn.onclick = () => {
          $('#help-ai-input').value = btn.getAttribute('data-suggestion');
          askAI();
        };
      });
    } catch (e) {
      $('#help-ai-answer').innerHTML = `<div class="text-danger text-sm">${esc(e.message)}</div>`;
    }
    $('#help-ai-btn').disabled = false;
  }

  function renderArticles(articles) {
    if (articles.length === 0) {
      $('#help-articles').innerHTML = '<div class="card text-center text-dim" style="padding:24px">No articles found. Try the Ask AI box above.</div>';
      return;
    }
    // Group by category
    const grouped = {};
    for (const a of articles) {
      if (!grouped[a.category]) grouped[a.category] = [];
      grouped[a.category].push(a);
    }
    const catOrder = categories.map(c => c.id);
    let html = '';
    for (const catId of catOrder) {
      if (!grouped[catId]) continue;
      const cat = categories.find(c => c.id === catId);
      html += `<div class="card mb-3">
        <h3 style="margin-bottom:8px;padding-bottom:8px;border-bottom:1px solid var(--border,#E2E8F0)">${esc(cat?.name || catId)}</h3>
        ${grouped[catId].map(a => `
          <details style="margin-bottom:8px">
            <summary style="cursor:pointer;font-weight:600;padding:8px 0;color:var(--text-strong,#0F172A)">${esc(a.question)}</summary>
            <div style="padding:8px 0 12px 16px;white-space:pre-wrap;line-height:1.5;color:var(--text,#1E293B)">${esc(a.answer)}</div>
          </details>
        `).join('')}
      </div>`;
    }
    $('#help-articles').innerHTML = html;
  }
});

// ─── Floating Help Button ─────────────────────────────────────────
// Injected on every page — opens a quick-help chat modal.

let _helpButtonInjected = false;

export function initFloatingHelp() {
  if (_helpButtonInjected) return;
  _helpButtonInjected = true;

  // Inject the floating button CSS + element
  const style = document.createElement('style');
  style.textContent = `
    .bb-help-fab {
      position: fixed; bottom: 20px; right: 20px; z-index: 9998;
      width: 48px; height: 48px; border-radius: 50%;
      background: var(--accent, #2563EB); color: white; border: none;
      cursor: pointer; display: flex; align-items: center; justify-content: center;
      box-shadow: 0 4px 12px rgba(37, 99, 235, 0.4);
      transition: transform 0.2s, box-shadow 0.2s, opacity 0.2s;
      position: fixed;
    }
    .bb-help-fab:hover { transform: scale(1.1); box-shadow: 0 6px 16px rgba(37, 99, 235, 0.5); }
    .bb-help-fab svg { width: 22px; height: 22px; }
    /* v8.16.1: Hide help FAB on POS/kiosk pages */
    body.kiosk-mode .bb-help-fab { display: none !important; }
    .bb-help-fab-off {
      background: var(--warning, #D97706) !important;
      box-shadow: 0 4px 12px rgba(217, 119, 6, 0.4) !important;
    }
    .bb-help-fab-badge {
      position: absolute; top: -4px; right: -4px;
      background: var(--warning-text, #D97706); color: white;
      font-size: 9px; font-weight: 700; padding: 2px 5px; border-radius: 8px;
      border: 2px solid var(--surface, #fff);
      letter-spacing: 0.3px;
    }
    .bb-help-kill-banner {
      padding: 6px 10px; background: var(--warning-soft, #FEF3C7);
      color: var(--warning-text, #D97706); font-size: 11px;
      border-bottom: 1px solid var(--border, #E2E8F0);
    }
    .bb-help-modal-overlay {
      position: fixed; bottom: 80px; right: 20px; z-index: 9999;
      width: 400px; max-width: calc(100vw - 40px); height: 550px; max-height: 80vh;
      background: var(--surface, #fff); border: 1px solid var(--border, #E2E8F0);
      border-radius: 12px; box-shadow: 0 8px 32px rgba(0,0,0,0.15);
      display: flex; flex-direction: column; overflow: hidden;
    }
    .bb-help-modal-header {
      padding: 12px 16px; background: var(--accent, #2563EB); color: white;
      display: flex; justify-content: space-between; align-items: center;
    }
    .bb-help-modal-header strong { font-size: 14px; }
    .bb-help-modal-body {
      flex: 1; overflow-y: auto; padding: 12px 16px; min-height: 300px; max-height: 400px;
    }
    .bb-help-modal-input {
      padding: 8px 12px; border-top: 1px solid var(--border, #E2E8F0);
      display: flex; gap: 8px; background: var(--bg, #F8FAFC);
    }
    .bb-help-modal-input input {
      flex: 1; padding: 8px 12px; border: 1px solid var(--border, #E2E8F0);
      border-radius: 8px; font-size: 13px; background: var(--surface, #fff);
      color: var(--text, #1E293B);
    }
    .bb-help-modal-input button {
      padding: 8px 12px; border: none; border-radius: 8px;
      background: var(--accent, #2563EB); color: white; cursor: pointer; font-size: 13px;
    }
    .bb-help-msg { margin-bottom: 10px; }
    .bb-help-msg-user { text-align: right; }
    .bb-help-msg-user span {
      display: inline-block; padding: 8px 12px; background: var(--accent, #2563EB);
      color: white; border-radius: 12px 12px 4px 12px; font-size: 13px; max-width: 280px;
      text-align: left;
    }
    .bb-help-msg-bot span {
      display: inline-block; padding: 8px 12px; background: var(--bg-2, #F1F5F9);
      color: var(--text, #1E293B); border-radius: 12px 12px 12px 4px; font-size: 13px;
      max-width: 300px; white-space: pre-wrap; line-height: 1.4;
    }
    .bb-help-suggestions { margin-top: 8px; }
    .bb-help-suggestions button {
      font-size: 11px; padding: 4px 8px; margin: 2px; border: 1px solid var(--border, #E2E8F0);
      border-radius: 6px; background: var(--surface, #fff); color: var(--text-dim, #64748B);
      cursor: pointer;
    }
    .bb-help-suggestions button:hover { background: var(--bg-2, #F1F5F9); }
    .bb-help-source { font-size: 10px; color: var(--text-dim, #94A3B8); margin-top: 4px; }
  `;
  document.head.appendChild(style);

  // Create the floating button
  const fab = document.createElement('button');
  fab.className = 'bb-help-fab';
  fab.innerHTML = SVG.help;
  fab.title = 'Ask for help';
  fab.onclick = toggleHelpModal;
  document.body.appendChild(fab);

  // Check kill-switch status and update the FAB appearance
  refreshHelpFabState();
  // Re-check every 60s in case the kill switch is toggled elsewhere
  setInterval(refreshHelpFabState, 60000);
}

async function refreshHelpFabState() {
  try {
    const r = await api('/api/ai/kill-switch');
    const fab = document.querySelector('.bb-help-fab');
    if (!fab) return;
    const isOff = !!r.disabled;
    if (isOff) {
      fab.classList.add('bb-help-fab-off');
      // Add badge if not present
      if (!fab.querySelector('.bb-help-fab-badge')) {
        const badge = document.createElement('span');
        badge.className = 'bb-help-fab-badge';
        badge.textContent = 'AI OFF';
        fab.appendChild(badge);
      }
      fab.title = 'Help — AI is disabled, using FAQ only';
    } else {
      fab.classList.remove('bb-help-fab-off');
      const badge = fab.querySelector('.bb-help-fab-badge');
      if (badge) badge.remove();
      fab.title = 'Ask for help';
    }
    window._helpKillSwitchOn = isOff;
  } catch {}
}

let _helpModalOpen = false;
let _helpHistory = [];

function toggleHelpModal() {
  if (_helpModalOpen) {
    closeHelpModal();
  } else {
    openHelpModal();
  }
}

function openHelpModal() {
  _helpModalOpen = true;
  const isKillOn = !!window._helpKillSwitchOn;
  const killBannerHtml = isKillOn
    ? `<div class="bb-help-kill-banner">
         <strong>AI is disabled</strong> — answering from FAQ only.
         <a href="#/insights/ai-usage" style="color:inherit;text-decoration:underline">Enable AI</a> for richer answers.
       </div>`
    : '';
  const overlay = document.createElement('div');
  overlay.id = 'bb-help-overlay';
  overlay.className = 'bb-help-modal-overlay';
  overlay.innerHTML = `
    <div class="bb-help-modal-header">
      <strong>${SVG.sparkles} BillBook Help</strong>
      <button onclick="document.getElementById('bb-help-overlay')?.remove(); window._helpModalOpen=false;"
              style="background:none;border:none;color:white;cursor:pointer;font-size:18px">x</button>
    </div>
    ${killBannerHtml}
    <div class="bb-help-modal-body" id="bb-help-body">
      <div class="bb-help-msg bb-help-msg-bot">
        <span>Hi! I can help with anything about BillBook — how to make a sale, check profit, pair your phone, fix errors, or understand accounting. What do you need?</span>
      </div>
      <div class="bb-help-suggestions">
        <button data-q="How do I make a sale?">How do I make a sale?</button>
        <button data-q="What is my profit?">What is my profit?</button>
        <button data-q="How do I pair my phone?">How do I pair my phone?</button>
      </div>
    </div>
    <div class="bb-help-modal-input">
      <input type="text" id="bb-help-input" placeholder="Ask anything..." autocomplete="off">
      <button id="bb-help-send">${SVG.send}</button>
    </div>`;
  document.body.appendChild(overlay);

  // Wire input
  const input = document.getElementById('bb-help-input');
  const sendBtn = document.getElementById('bb-help-send');
  sendBtn.onclick = () => sendHelpMessage(input.value);
  input.onkeydown = (e) => { if (e.key === 'Enter') sendHelpMessage(input.value); };

  // Wire suggestion buttons
  overlay.querySelectorAll('[data-q]').forEach(btn => {
    btn.onclick = () => { input.value = btn.getAttribute('data-q'); sendHelpMessage(input.value); };
  });

  input.focus();
}

function closeHelpModal() {
  _helpModalOpen = false;
  const overlay = document.getElementById('bb-help-overlay');
  if (overlay) overlay.remove();
}

async function sendHelpMessage(question) {
  if (!question.trim()) return;
  const input = document.getElementById('bb-help-input');
  const body = document.getElementById('bb-help-body');
  if (!body) return;

  // Show user message
  body.innerHTML += `<div class="bb-help-msg bb-help-msg-user"><span>${esc(question)}</span></div>`;
  input.value = '';
  body.scrollTop = body.scrollHeight;

  // Show thinking
  const thinkId = 'bb-help-thinking-' + Date.now();
  body.innerHTML += `<div class="bb-help-msg bb-help-msg-bot" id="${thinkId}"><span>Thinking...</span></div>`;
  body.scrollTop = body.scrollHeight;

  try {
    const r = await apiPost('/api/help/ask', { question });
    const thinkEl = document.getElementById(thinkId);
    if (thinkEl) thinkEl.remove();

    const sourceLabel = r.source === 'faq' ? 'FAQ' : r.source === 'ai' ? 'AI' : r.source === 'faq_fuzzy' ? 'Close Match' : '';
    const staleLabel = r.stale ? ' (cached)' : '';
    const suggestionsHtml = (r.suggestions || []).slice(0, 3).map(s =>
      `<button data-q="${esc(s)}">${esc(s)}</button>`
    ).join('');

    body.innerHTML += `<div class="bb-help-msg bb-help-msg-bot">
      <span>${esc(r.answer)}</span>
      ${sourceLabel ? `<div class="bb-help-source">${sourceLabel}${staleLabel}</div>` : ''}
      ${suggestionsHtml ? `<div class="bb-help-suggestions">${suggestionsHtml}</div>` : ''}
    </div>`;

    // Wire new suggestion buttons
    body.querySelectorAll('[data-q]').forEach(btn => {
      btn.onclick = () => { input.value = btn.getAttribute('data-q'); sendHelpMessage(input.value); };
    });
    body.scrollTop = body.scrollHeight;
  } catch (e) {
    const thinkEl = document.getElementById(thinkId);
    if (thinkEl) thinkEl.remove();
    body.innerHTML += `<div class="bb-help-msg bb-help-msg-bot"><span>Sorry, I couldn't process that. ${esc(e.message)}</span></div>`;
    body.scrollTop = body.scrollHeight;
  }
}

// Make sendHelpMessage accessible globally for inline onclick
window._helpModalOpen = false;
window.sendHelpMessage = sendHelpMessage;
