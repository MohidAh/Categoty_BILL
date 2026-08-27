// v8.16.12 — AI Assistant chat UI
// Uses CSS variables (var(--surface), var(--accent-pink), etc.) so the
// chat card follows the system color scheme — light mode OR dark mode.
import { route, navigate } from '../router.js';
import { api, apiPost } from '../api.js';
import { $, esc, toast, openModal, closeModal, skeletonCards, errorBox } from '../utils.js';

const SVG = {
  sparkles: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1"/></svg>',
  send: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>',
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>',
  alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
  brain: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96.44 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24 2.5 2.5 0 0 1 1.98-3A2.5 2.5 0 0 1 9.5 2Z"/><path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96.44 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-1.98-3A2.5 2.5 0 0 0 14.5 2Z"/></svg>',
  power: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18.36 6.64a9 9 0 1 1-12.73 0"/><line x1="12" y1="2" x2="12" y2="12"/></svg>',
  database: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/></svg>',
  download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
  chevron: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>',
  shield: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>',
  signal: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M2 12h2M6 8h2M10 4h2M14 8h2M18 12h2"/><circle cx="12" cy="14" r="2"/></svg>',
};


let _chatHistory = [];
let _killSwitchOn = false;

route('/insights/agent', async (el) => {
  // Load history from localStorage
  try { _chatHistory = JSON.parse(localStorage.getItem('bb_agent_history') || '[]'); } catch { _chatHistory = []; }

  el.innerHTML = `
    <div id="agent-kill-banner" style="display:none;margin-bottom:12px"></div>

    <div style="background:var(--surface);border:1px solid var(--border);border-radius:16px;overflow:hidden;display:flex;flex-direction:column;box-shadow:0 4px 24px rgba(0,0,0,0.08)">

      <!-- Card Header -->
      <div style="padding:16px 20px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;gap:12px;background:var(--elevated)">
        <div style="display:flex;align-items:center;gap:12px;flex:1;min-width:0">
          <div style="width:40px;height:40px;border-radius:12px;background:var(--accent-pink-soft);border:1px solid var(--accent-pink);display:flex;align-items:center;justify-content:center;flex-shrink:0">
            <span style="display:inline-flex;width:20px;height:20px;color:var(--accent-pink)">${SVG.signal}</span>
          </div>
          <div style="min-width:0">
            <h3 style="margin:0;font-size:16px;font-weight:600;color:var(--text);letter-spacing:-0.01em">AI Assistant</h3>
            <p style="margin:2px 0 0;font-size:12px;color:var(--text-2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
              Ask any question about your business data — powered by your sales, inventory, and profit reports.
            </p>
          </div>
        </div>
        <div style="display:flex;gap:6px;flex-shrink:0">
          <button class="btn btn-sm" id="agent-clear" title="Clear chat history"
            style="background:transparent;border:1px solid var(--border-strong);color:var(--text-2);padding:6px 12px;border-radius:8px;font-size:12px;display:flex;align-items:center;gap:5px;cursor:pointer;transition:all .15s"
            onmouseover="this.style.borderColor='var(--accent-pink)';this.style.color='var(--accent-pink)'"
            onmouseout="this.style.borderColor='var(--border-strong)';this.style.color='var(--text-2)'">
            <span style="display:inline-flex;width:13px;height:13px">${SVG.trash}</span>
            Clear Chat
          </button>
          <button class="btn btn-sm" id="agent-export" title="Export chat as text"
            style="background:transparent;border:1px solid var(--border-strong);color:var(--text-2);padding:6px 12px;border-radius:8px;font-size:12px;display:flex;align-items:center;gap:5px;cursor:pointer;transition:all .15s"
            onmouseover="this.style.borderColor='var(--accent-pink)';this.style.color='var(--accent-pink)'"
            onmouseout="this.style.borderColor='var(--border-strong)';this.style.color='var(--text-2)'">
            <span style="display:inline-flex;width:13px;height:13px">${SVG.download}</span>
            Export
          </button>
        </div>
      </div>

      <!-- Chat Body -->
      <div id="agent-chat" style="flex:1;overflow-y:auto;padding:20px;min-height:calc(100dvh - 22rem);max-height:calc(100dvh - 22rem)">
        ${_chatHistory.length === 0 ? renderWelcome() : _chatHistory.map(renderMessage).join('')}
      </div>

      <!-- Input Area -->
      <div style="padding:12px 20px 16px;border-top:1px solid var(--border);background:var(--elevated)">
        <div id="agent-suggestions" style="margin-bottom:10px;display:flex;gap:6px;flex-wrap:wrap">
          <button class="btn btn-sm" data-q="What is my actual overall margin?" style="margin:0;background:var(--surface);border:1px solid var(--border);color:var(--text-2);padding:5px 12px;border-radius:999px;font-size:12px;cursor:pointer;transition:all .15s"
            onmouseover="this.style.borderColor='var(--accent-pink)';this.style.color='var(--accent-pink)'"
            onmouseout="this.style.borderColor='var(--border)';this.style.color='var(--text-2)'">
            What is my margin?
          </button>
          <button class="btn btn-sm" data-q="How much cash can I safely withdraw?" style="margin:0;background:var(--surface);border:1px solid var(--border);color:var(--text-2);padding:5px 12px;border-radius:999px;font-size:12px;cursor:pointer;transition:all .15s"
            onmouseover="this.style.borderColor='var(--accent-pink)';this.style.color='var(--accent-pink)'"
            onmouseout="this.style.borderColor='var(--border)';this.style.color='var(--text-2)'">
            How much can I withdraw?
          </button>
          <button class="btn btn-sm" data-q="What is my break-even daily target?" style="margin:0;background:var(--surface);border:1px solid var(--border);color:var(--text-2);padding:5px 12px;border-radius:999px;font-size:12px;cursor:pointer;transition:all .15s"
            onmouseover="this.style.borderColor='var(--accent-pink)';this.style.color='var(--accent-pink)'"
            onmouseout="this.style.borderColor='var(--border)';this.style.color='var(--text-2)'">
            Break-even target?
          </button>
          <button class="btn btn-sm" data-q="Which customers have outstanding credit?" style="margin:0;background:var(--surface);border:1px solid var(--border);color:var(--text-2);padding:5px 12px;border-radius:999px;font-size:12px;cursor:pointer;transition:all .15s"
            onmouseover="this.style.borderColor='var(--accent-pink)';this.style.color='var(--accent-pink)'"
            onmouseout="this.style.borderColor='var(--border)';this.style.color='var(--text-2)'">
            Who owes me?
          </button>
        </div>
        <div style="display:flex;gap:8px;align-items:center">
          <input id="agent-input" placeholder="Ask about sales, suppliers, inventory, profit..."
            autocomplete="off"
            style="flex:1;background:var(--bg);border:1px solid var(--border-strong);border-radius:12px;padding:12px 16px;font-size:14px;color:var(--text);outline:none;font-family:inherit;transition:border-color .15s"
            onfocus="this.style.borderColor='var(--accent-pink)'"
            onblur="this.style.borderColor='var(--border-strong)'"
            ${_killSwitchOn ? 'disabled' : ''}>
          <button id="agent-send"
            style="width:44px;height:44px;border-radius:12px;background:var(--accent-pink);border:none;display:flex;align-items:center;justify-content:center;cursor:pointer;transition:background .15s;flex-shrink:0"
            onmouseover="this.style.background='var(--accent-pink)'"
            onmouseout="this.style.background='var(--accent-pink)'"
            ${_killSwitchOn ? 'disabled' : ''}>
            <span style="display:inline-flex;width:18px;height:18px;color:white">${SVG.send}</span>
          </button>
        </div>
      </div>
    </div>`;

  const chat = $('#agent-chat');
  chat.scrollTop = chat.scrollHeight;

  $('#agent-send').onclick = () => sendQuestion();
  $('#agent-input').onkeydown = (e) => { if (e.key === 'Enter') sendQuestion(); };
  $('#agent-clear').onclick = () => {
    if (!confirm('Clear chat history?')) return;
    _chatHistory = [];
    try { localStorage.removeItem('bb_agent_history'); } catch {}
    chat.innerHTML = renderWelcome();
  };
  $('#agent-export').onclick = exportChat;

  // Wire suggestion buttons
  wireSuggestions();

  // Check kill-switch status on load
  await refreshKillSwitch();

  async function refreshKillSwitch() {
    try {
      const r = await api('/api/ai/kill-switch');
      _killSwitchOn = !!r.disabled;
      renderKillBanner();
    } catch {}
  }

  function renderKillBanner() {
    const banner = $('#agent-kill-banner');
    const input = $('#agent-input');
    const send = $('#agent-send');
    if (_killSwitchOn) {
      banner.style.display = '';
      banner.innerHTML = `<div style="padding:12px 16px;background:var(--danger-soft);border:1px solid var(--danger);border-radius:12px;display:flex;gap:12px;align-items:center">
        <span style="display:inline-flex;width:20px;height:20px;color:var(--danger)">${SVG.power}</span>
        <div style="flex:1">
          <strong style="color:var(--danger)">AI Kill Switch is ON</strong>
          <div style="font-size:13px;color:var(--danger);margin-top:2px;opacity:0.8">
            The AI Assistant is disabled. Heuristic features (trends, break-even, margin alerts) continue to work.
            You can re-enable it on the <a href="#/insights/ai-usage" style="color:inherit;text-decoration:underline">AI Usage page</a>.
          </div>
        </div>
      </div>`;
      if (input) input.disabled = true;
      if (send) send.disabled = true;
    } else {
      banner.style.display = 'none';
      banner.innerHTML = '';
      if (input) input.disabled = false;
      if (send) send.disabled = false;
    }
  }

  function exportChat() {
    if (_chatHistory.length === 0) {
      toast('No chat to export', 'info');
      return;
    }
    let text = 'BillBook AI Assistant — Chat Export\n';
    text += 'Date: ' + new Date().toLocaleString() + '\n';
    text += '='.repeat(60) + '\n\n';
    _chatHistory.forEach((msg) => {
      if (msg.role === 'user') {
        text += 'YOU: ' + msg.text + '\n\n';
      } else {
        text += 'AI: ' + msg.text + '\n';
        if (msg.trace && msg.trace.length) {
          text += '  [Tool trace: ' + msg.trace.length + ' steps]\n';
        }
        text += '\n';
      }
    });
    const blob = new Blob([text], { type: 'text/plain' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'billbook_chat_' + new Date().toISOString().slice(0, 10) + '.txt';
    a.click();
    URL.revokeObjectURL(a.href);
    toast('Chat exported', 'success');
  }

  async function sendQuestion() {
    if (_killSwitchOn) {
      toast('AI is disabled. Enable it on the AI Usage page.', 'error');
      return;
    }
    const input = $('#agent-input');
    const question = input.value.trim();
    if (!question) return;
    input.value = '';

    // Add user message
    _chatHistory.push({ role: 'user', text: question, ts: new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}) });
    renderChat();
    saveHistory();

    // Show thinking indicator
    const thinkId = 'agent-thinking-' + Date.now();
    chat.innerHTML += `<div id="${thinkId}" style="margin-bottom:16px">
      <div style="display:flex;gap:10px;align-items:flex-start">
        <div style="width:32px;height:32px;border-radius:10px;background:var(--accent-pink-soft);display:flex;align-items:center;justify-content:center;flex-shrink:0">
          <span style="display:inline-flex;width:14px;height:14px;color:var(--accent-pink);animation:pulse 1.5s ease-in-out infinite">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
          </span>
        </div>
        <div style="padding:12px 16px;background:var(--elevated);border-radius:4px 16px 16px 16px;font-size:14px;color:var(--text-2)">
          <span style="display:inline-flex;gap:4px;align-items:center">
            <span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--accent-pink);animation:bounce 1.4s ease-in-out infinite both"></span>
            <span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--accent-pink);animation:bounce 1.4s ease-in-out infinite both;animation-delay:.2s"></span>
            <span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--accent-pink);animation:bounce 1.4s ease-in-out infinite both;animation-delay:.4s"></span>
          </span>
          <span style="margin-left:8px">Thinking...</span>
        </div>
      </div>
    </div>
    <style>
      @keyframes bounce {0%,80%,100%{transform:scale(0)}40%{transform:scale(1)}}
      @keyframes pulse {0%,100%{opacity:1}50%{opacity:0.5}}
    </style>`;
    chat.scrollTop = chat.scrollHeight;

    try {
      const r = await apiPost('/api/agent/ask', { question });
      const thinkEl = document.getElementById(thinkId);
      if (thinkEl) thinkEl.remove();

      if (r.tool_trace && r.tool_trace.some(t => t.step === 'kill_switch')) {
        _killSwitchOn = true;
        renderKillBanner();
      }

      _chatHistory.push({
        role: 'agent',
        text: r.answer,
        trace: r.tool_trace || [],
        suggestions: r.suggested_followups || [],
        question: question,
        ts: new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}),
        model: r.model || 'agent',
      });
      renderChat();
      saveHistory();
    } catch (e) {
      const thinkEl = document.getElementById(thinkId);
      if (thinkEl) thinkEl.remove();
      _chatHistory.push({ role: 'agent', text: 'Sorry, I encountered an error: ' + e.message, trace: [], suggestions: [], question: question, ts: new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}) });
      renderChat();
      saveHistory();
    }
  }

  function renderChat() {
    chat.innerHTML = _chatHistory.map(renderMessage).join('') + renderSuggestions();
    chat.scrollTop = chat.scrollHeight;
    wireSuggestions();
    document.querySelectorAll('[data-trace-toggle]').forEach(t => {
      t.onclick = () => {
        const id = t.getAttribute('data-trace-toggle');
        const detail = document.getElementById(id);
        if (detail) {
          const isHidden = detail.style.display === 'none';
          detail.style.display = isHidden ? '' : 'none';
          t.querySelector('.chev').style.transform = isHidden ? 'rotate(180deg)' : '';
        }
      };
    });
  }

  function renderSuggestions() {
    const last = _chatHistory[_chatHistory.length - 1];
    if (!last || last.role !== 'agent' || !last.suggestions || last.suggestions.length === 0) return '';
    return `<div style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap">
      <span style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:0.04em;align-self:center;margin-right:4px">Follow up:</span>
      ${last.suggestions.map(s => `<button data-q="${esc(s)}" style="background:var(--surface);border:1px solid var(--border);color:var(--text-2);padding:5px 12px;border-radius:999px;font-size:12px;cursor:pointer;transition:all .15s"
        onmouseover="this.style.borderColor='var(--accent-pink)';this.style.color='var(--accent-pink)'"
        onmouseout="this.style.borderColor='var(--border)';this.style.color='var(--text-2)'">${esc(s)}</button>`).join('')}
    </div>`;
  }

  function wireSuggestions() {
    document.querySelectorAll('[data-q]').forEach(btn => {
      btn.onclick = () => { $('#agent-input').value = btn.getAttribute('data-q'); sendQuestion(); };
    });
  }

  function saveHistory() {
    try { localStorage.setItem('bb_agent_history', JSON.stringify(_chatHistory.slice(-20))); } catch {}
  }
});

function renderWelcome() {
  return `<div style="text-align:center;padding:48px 20px">
    <div style="width:56px;height:56px;margin:0 auto 20px;background:var(--accent-pink-soft);border:1px solid var(--accent-pink);border-radius:16px;display:flex;align-items:center;justify-content:center">
      <span style="display:inline-flex;width:28px;height:28px;color:var(--accent-pink)">${SVG.signal}</span>
    </div>
    <h3 style="margin:0 0 8px;font-size:18px;font-weight:600;color:var(--text)">Ask the AI Assistant</h3>
    <p style="font-size:14px;color:var(--text-2);max-width:440px;margin:0 auto 8px;line-height:1.5">
      I can answer questions about your sales, profit, margins, stock, expenses, and more.
      I use real data from your system — my numbers match your reports exactly.
    </p>
    <p style="font-size:12px;color:var(--muted);margin-top:12px">Try one of the suggestions below to get started.</p>
  </div>`;
}

function renderMessage(msg) {
  if (msg.role === 'user') {
    return `<div style="margin-bottom:16px;display:flex;justify-content:flex-end">
      <span style="display:inline-block;padding:10px 16px;background:var(--accent-pink);color:var(--text-on-accent);border-radius:16px 16px 4px 16px;font-size:14px;max-width:420px;text-align:left;line-height:1.5">${esc(msg.text)}</span>
    </div>`;
  }

  // Agent message
  let traceHtml = '';
  if (msg.trace && msg.trace.length > 0) {
    const traceId = 'trace-' + Math.random().toString(36).slice(2, 9);
    const hasKillSwitch = msg.trace.some(t => t.step === 'kill_switch');
    if (hasKillSwitch) {
      traceHtml = `<div style="margin-bottom:8px;padding:8px 12px;background:rgba(249,24,128,0.08);border-radius:8px;font-size:12px;color:var(--danger);display:flex;align-items:center;gap:6px">
        <span style="display:inline-flex;width:12px;height:12px">${SVG.power}</span>
        AI was disabled when this question was asked.
      </div>`;
    } else {
      const summaryHtml = msg.trace
        .filter(s => s.step === 'tool_result' && s.status === 'ok')
        .map(s => `<div style="color:var(--success);margin-bottom:3px;font-size:12px;display:flex;align-items:center;gap:5px">
          <span style="display:inline-flex;width:12px;height:12px;flex-shrink:0">${SVG.check}</span>
          <span>${esc(s.summary || s.tool || 'done')}</span>
        </div>`).join('');
      const detailHtml = msg.trace.map(step => {
        if (step.step === 'tool_call') {
          return `<div style="color:var(--text-2);margin-bottom:4px;padding:6px 10px;background:var(--bg);border-radius:6px;font-size:12px;display:flex;align-items:center;gap:6px">
            <span style="display:inline-flex;width:12px;height:12px;flex-shrink:0">${SVG.search}</span>
            <span>calling <code style="background:var(--surface);padding:1px 5px;border-radius:3px;font-size:11px;color:var(--accent-pink)">${esc(step.tool)}</code></span>
          </div>`;
        } else if (step.step === 'tool_result' && step.status === 'ok') {
          return `<div style="color:var(--success);margin-bottom:4px;padding:6px 10px;background:var(--bg);border-radius:6px;font-size:12px;display:flex;align-items:center;gap:6px">
            <span style="display:inline-flex;width:12px;height:12px;flex-shrink:0">${SVG.check}</span>
            <strong>${esc(step.tool)}:</strong> ${esc(step.summary || 'ok')}
          </div>`;
        } else if (step.step === 'tool_result' && step.status === 'error') {
          return `<div style="color:var(--danger);margin-bottom:4px;padding:6px 10px;background:var(--bg);border-radius:6px;font-size:12px;display:flex;align-items:center;gap:6px">
            <span style="display:inline-flex;width:12px;height:12px;flex-shrink:0">${SVG.alert}</span>
            <strong>${esc(step.tool)} error:</strong> ${esc(step.error || 'unknown')}
          </div>`;
        }
        return '';
      }).join('');
      const callCount = msg.trace.filter(s => s.step === 'tool_call').length;
      traceHtml = `<div style="margin-bottom:8px;padding:8px 12px;background:var(--bg);border:1px solid var(--border);border-radius:10px;font-size:12px">
        <div style="display:flex;justify-content:space-between;align-items:center;cursor:pointer" data-trace-toggle="${traceId}">
          <div style="display:flex;align-items:center;gap:6px;color:var(--text-2)">
            <span style="display:inline-flex;width:12px;height:12px">${SVG.database}</span>
            <span>Tool trace (${callCount} call${callCount === 1 ? '' : 's'})</span>
          </div>
          <span class="chev" style="display:inline-flex;width:14px;height:14px;transition:transform .2s;transform:rotate(180deg);color:var(--text-2)">${SVG.chevron}</span>
        </div>
        <div id="${traceId}" style="margin-top:6px">
          ${summaryHtml}
          <details style="margin-top:6px">
            <summary style="cursor:pointer;color:var(--muted);font-size:11px;outline:none">Show full trace</summary>
            <div style="margin-top:4px">${detailHtml}</div>
          </details>
        </div>
      </div>`;
    }
  }

  // Parity check link
  let parityHtml = '';
  if (msg.question && /margin/i.test(msg.question) && /\d+(\.\d+)?\s*%/.test(msg.text || '')) {
    parityHtml = `<div style="margin-top:6px;font-size:11px;color:var(--muted);display:flex;align-items:center;gap:4px">
      <span style="display:inline-flex;width:12px;height:12px">${SVG.shield}</span>
      <span>Numbers match the <a href="#/reports/margins" style="color:inherit;text-decoration:underline">Margins report</a></span>
    </div>`;
  }

  // Timestamp
  const ts = msg.ts || '';
  const model = msg.model ? `via ${msg.model}` : '';
  const meta = [model, ts].filter(Boolean).join(' · ') || '';

  return `<div style="margin-bottom:20px">
    <div style="display:flex;gap:10px;align-items:flex-start">
      <div style="width:32px;height:32px;border-radius:10px;background:var(--accent-pink-soft);border:1px solid var(--accent-pink);display:flex;align-items:center;justify-content:center;flex-shrink:0">
        <span style="display:inline-flex;width:16px;height:16px;color:var(--accent-pink)">${SVG.brain}</span>
      </div>
      <div style="flex:1;min-width:0">
        ${traceHtml}
        <div style="padding:14px 18px;background:var(--elevated);border:1px solid var(--border);border-radius:4px 16px 16px 16px;font-size:14px;line-height:1.6;color:var(--text);white-space:pre-wrap">${esc(msg.text)}</div>
        ${meta ? `<div style="margin-top:4px;font-size:11px;color:var(--muted);padding-left:4px">${esc(meta)}</div>` : ''}
        ${parityHtml}
      </div>
    </div>
  </div>`;
}
