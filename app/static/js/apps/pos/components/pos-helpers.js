// POS shared helpers — extracted from pos.js Phase 5
export { helpers }

// Helper functions
function fmtTime(ts) {
  if (!ts) return '';
  return ts.slice(11, 16);
}

function paymentBadge(method) {
  const m = {
    cash: 'badge-success',
    card: 'badge-accent',
    online: 'badge-accent',
    credit: 'badge-danger',
    split: 'badge-warning',
  };
  const labels = { cash: 'Cash', card: 'Card', online: 'Online', credit: 'Credit', split: 'Split' };
  return `<span class="badge ${m[method] || 'badge-success'}">${labels[method] || method}</span>`;
}

// ==================================================================
// /pos — main POS screen
// ==================================================================
