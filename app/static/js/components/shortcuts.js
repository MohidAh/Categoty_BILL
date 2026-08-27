// Keyboard shortcuts overlay — press ? to see all shortcuts
import { $, esc, icon } from '../utils.js';

const SHORTCUTS = [
  {
    section: 'Navigation',
    items: [
      { keys: ['D'], label: 'Go to Dashboard' },
      { keys: ['B'], label: 'Go to Bills' },
      { keys: ['F'], label: 'Go to Item Search' },
      { keys: ['S'], label: 'Go to Suppliers' },
      { keys: ['R'], label: 'Go to Reports' },
      { keys: ['I'], label: 'Go to Insights' },
      { keys: ['M'], label: 'Go to More Tools' },
      { keys: [','], label: 'Go to Settings' },
      { keys: ['N'], label: 'New Bill upload' },
    ],
  },
  {
    section: 'POS (on /pos screen)',
    items: [
      { keys: ['F1'], label: 'Add 1st category to cart' },
      { keys: ['F2'], label: 'Add 2nd category to cart' },
      { keys: ['F3'], label: 'Add 3rd category to cart' },
      { keys: ['F4'], label: 'Add 4th category to cart' },
      { keys: ['F5'], label: 'Add 5th category to cart' },
      { keys: ['F6'], label: 'Add 6th category to cart' },
      { keys: ['F7'], label: 'Add 7th category to cart' },
      { keys: ['F8'], label: 'Open barcode scanner' },
      { keys: ['F9'], label: 'Complete sale (checkout)' },
      { keys: ['F10'], label: 'Hold current cart (park sale)' },
      { keys: ['F11'], label: 'Clear cart' },
      { keys: ['F12'], label: 'Save cart as quotation' },
    ],
  },
  {
    section: 'Actions',
    items: [
      { keys: ['⌘', 'K'], label: 'Open command palette' },
      { keys: ['?'], label: 'Show this shortcuts overlay' },
      { keys: ['Esc'], label: 'Close overlay / cancel' },
    ],
  },
  {
    section: 'In tables',
    items: [
      { keys: ['↑', '↓'], label: 'Navigate command palette results' },
      { keys: ['↵'], label: 'Select / save inline edit' },
    ],
  },
];

export function initShortcutsOverlay() {
  document.addEventListener('keydown', (e) => {
    // Only trigger on ? key (Shift+/) when not typing in an input
    const tag = e.target.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
    if (e.shiftKey && e.key === '?') {
      e.preventDefault();
      openOverlay();
    }
    if (e.key === 'Escape') {
      const overlay = $('#shortcuts-overlay');
      if (overlay) closeOverlay();
    }
  });
}

function openOverlay() {
  if ($('#shortcuts-overlay')) return;
  const root = document.createElement('div');
  root.id = 'shortcuts-overlay';
  root.className = 'cmdk-overlay';
  root.innerHTML = `
    <div class="shortcuts-modal" onclick="event.stopPropagation()">
      <div class="shortcuts-header">
        <h2>Keyboard Shortcuts</h2>
        <button class="btn btn-ghost btn-icon btn-sm" id="shortcuts-close">${icon('x', 14)}</button>
      </div>
      <div class="shortcuts-grid">
        ${SHORTCUTS.map(s => `
          <div class="shortcuts-section">
            <div class="shortcuts-section-title">${esc(s.section)}</div>
            <div class="shortcuts-list">
              ${s.items.map(it => `
                <div class="shortcut-row">
                  <span class="shortcut-label">${esc(it.label)}</span>
                  <span class="shortcut-keys">${it.keys.map(k => `<kbd>${esc(k)}</kbd>`).join('')}</span>
                </div>
              `).join('')}
            </div>
          </div>
        `).join('')}
      </div>
      <div class="shortcuts-footer">
        Press <kbd>?</kbd> anywhere to open this. Press <kbd>Esc</kbd> to close.
      </div>
    </div>`;
  root.addEventListener('click', closeOverlay);
  document.body.appendChild(root);
  $('#shortcuts-close').onclick = closeOverlay;
}

function closeOverlay() {
  const o = $('#shortcuts-overlay');
  if (o) o.remove();
}
