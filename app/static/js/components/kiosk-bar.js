// Kiosk bar — minimal top bar shown only in POS kiosk mode.
// Shows: brand, today's date/cashier, and an Exit button (PIN-protected).
// The exit button prompts for the manager PIN to leave POS kiosk mode.

import { $, esc, icon, toast, openModal, closeModal } from '../utils.js';
import { navigate } from '../router.js';
import { apiPost } from '../api.js';

export function renderKioskBar(currentPath = '/pos') {
  const today = new Date().toLocaleDateString('en-PK', {
    weekday: 'short', day: 'numeric', month: 'short', year: 'numeric',
  });
  const now = new Date().toLocaleTimeString('en-PK', {
    hour: '2-digit', minute: '2-digit',
  });

  // Determine which kiosk page is active for nav pills
  const pills = [
    { path: '/pos', label: 'Sell', icon: 'store' },
    { path: '/pos/sales', label: 'History', icon: 'bills' },
    { path: '/pos/quotes', label: 'Quotes', icon: 'file' },
  ];

  return `
    <div class="kiosk-bar">
      <div class="kiosk-bar-left">
        <div class="kiosk-brand">
          <span class="kiosk-brand-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 19V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M3 7h18"/><path d="M8 11h8M8 15h6"/></svg></span>
          <div>
            <div class="kiosk-brand-name">BillBook</div>
            <div class="kiosk-brand-sub">Point of Sale</div>
          </div>
        </div>
        <nav class="kiosk-nav">
          ${pills.map(p => `
            <a href="#${p.path}" class="kiosk-nav-pill ${currentPath === p.path ? 'active' : ''}">
              ${p.label}
            </a>`).join('')}
        </nav>
      </div>
      <div class="kiosk-bar-right">
        <div class="kiosk-clock">
          <div class="kiosk-clock-time" id="kiosk-time">${now}</div>
          <div class="kiosk-clock-date">${today}</div>
        </div>
        <button class="btn btn-danger btn-sm" id="kiosk-exit-btn" title="Exit POS (manager PIN required)">
          ${icon('lock', 14)} Exit POS
        </button>
      </div>
    </div>`;
}

export function initKioskBar() {
  // Live clock
  const tick = () => {
    const t = $('#kiosk-time');
    if (t) t.textContent = new Date().toLocaleTimeString('en-PK', { hour: '2-digit', minute: '2-digit' });
  };
  setInterval(tick, 1000 * 30);

  // Exit button — PIN protected
  const exitBtn = $('#kiosk-exit-btn');
  if (exitBtn) {
    exitBtn.onclick = () => promptExitPin();
  }
}

function promptExitPin() {
  openModal('Exit POS Mode', `
    <p>Enter the manager PIN to leave POS mode.</p>
    <div class="mt-3">
      <input class="input" id="exit-pin-input" type="password" placeholder="Manager PIN" autocomplete="off" autofocus>
      <p class="text-xs text-dim mt-2">Default PIN is your login password.</p>
    </div>`,
    `<button class="btn btn-secondary" data-modal-close>Cancel</button>
     <button class="btn" id="exit-pin-confirm">Exit POS</button>`);
  // Enter key triggers confirm
  const input = $('#exit-pin-input');
  if (input) {
    input.onkeydown = (e) => { if (e.key === 'Enter') $('#exit-pin-confirm').click(); };
  }
  $('#exit-pin-confirm').onclick = async () => {
    const pin = $('#exit-pin-input').value;
    if (!pin) { toast('Enter PIN', 'error'); return; }
    try {
      // v8.5: use the dedicated /api/security/verify-pin endpoint
      // instead of /api/login. /api/login creates a real session row
      // (which would pollute the sessions table and show up in the
      // active-sessions list); /api/security/verify-pin is stateless
      // and only returns ok/forbidden.
      const res = await fetch('/api/security/verify-pin', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pin }),
      });
      if (res.ok) {
        closeModal();
        toast('Exiting POS mode', 'success');
        navigate('/');
      } else {
        toast('Wrong PIN', 'error');
        $('#exit-pin-input').value = '';
        $('#exit-pin-input').focus();
      }
    } catch (e) {
      toast('Error verifying PIN', 'error');
    }
  };
}
