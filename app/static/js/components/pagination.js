// Pagination component
// v8.16.1: Fixed pagination causing "page not found" — the onclick handlers
// were generating location.hash='/path?page=2' but the slice(1) was removing
// the leading '/' from the hash, causing the router to not find the route.
// Fixed by NOT slicing — the hash should be '#/path?page=2', not '#path?page=2'.

export function pagination(data, currentPage, basePath, query = {}) {
  if (data.pages_total <= 1) return '';
  const q = new URLSearchParams(query).toString();
  // v8.16.1: Build the full hash path WITHOUT slicing the leading /
  // The link function returns '/path?page=2' (with leading /)
  const link = (p) => {
    const params = q ? `${q}&page=${p}` : `page=${p}`;
    return `${basePath}?${params}`;
  };

  let html = '<div class="pagination">';
  // v8.16.1: location.hash should be set to '#/path?page=2' (with leading /)
  // The old code used link().slice(1) which removed the leading /, causing
  // location.hash='bills?page=2' instead of '/bills?page=2' → route not found
  html += `<button class="btn btn-secondary" ${currentPage <= 1 ? 'disabled' : ''} onclick="location.hash='${link(currentPage - 1)}'">${chevronLeft()}</button>`;

  for (let i = 1; i <= data.pages_total; i++) {
    if (i === 1 || i === data.pages_total || Math.abs(i - currentPage) <= 2) {
      html += `<button class="btn btn-secondary ${i === currentPage ? 'active' : ''}" onclick="location.hash='${link(i)}'">${i}</button>`;
    } else if (Math.abs(i - currentPage) === 3) {
      html += '<button class="btn btn-secondary" disabled>…</button>';
    }
  }
  html += `<button class="btn btn-secondary" ${currentPage >= data.pages_total ? 'disabled' : ''} onclick="location.hash='${link(currentPage + 1)}'">${chevronRight()}</button>`;
  html += '</div>';
  return html;
}

function chevronLeft() {
  return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><polyline points="15 18 9 12 15 6"/></svg>';
}
function chevronRight() {
  return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><polyline points="9 18 15 12 9 6"/></svg>';
}
