// API wrapper with structured error handling
export class ApiError extends Error {
  constructor(message, status, detail) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

// C7 fix (v8.13.4): read the CSRF token from the meta tag injected by the
// server on every page render. The token is per-session and required as
// X-CSRF-Token on every non-GET request. Combined with SameSite=Strict,
// this prevents cross-site forged mutating requests even if a victim's
// browser would otherwise send the auth cookie.
function getCsrfToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.content : '';
}

// M4 fix (v8.13.4): accept an optional AbortSignal so callers (especially
// search inputs) can cancel an in-flight request when a newer one starts.
// Without this, an old slow response could overwrite a fresh fast one.
export async function api(url, options = {}) {
  const opts = { ...options };
  // Inject CSRF token on mutating requests
  const method = (opts.method || 'GET').toUpperCase();
  if (method !== 'GET' && method !== 'HEAD') {
    opts.headers = { ...(opts.headers || {}) };
    if (!opts.headers['X-CSRF-Token']) {
      const token = getCsrfToken();
      if (token) opts.headers['X-CSRF-Token'] = token;
    }
  }
  let res;
  try {
    res = await fetch(url, opts);
  } catch (e) {
    // Distinguish abort from real network errors
    if (e.name === 'AbortError') throw new ApiError('Request aborted', 0, e);
    throw new ApiError(`Network error: ${e.message}. Check your connection.`, 0, e);
  }

  if (res.status === 401) {
    window.location.href = '/login';
    throw new ApiError('Login required', 401);
  }

  // v8.19: license gate — when the app locks (no/expired/moved license),
  // every API call returns 403 {code: 'license_required'}; funnel the user
  // to the activation page. Uses a clone so the body stays readable below
  // for other 403s (RBAC etc.).
  if (res.status === 403) {
    try {
      const peek = await res.clone().json();
      if (peek && peek.code === 'license_required') {
        window.location.href = '/license';
        throw new ApiError('License required', 403, peek);
      }
    } catch (e) {
      if (e instanceof ApiError) throw e;
      // body wasn't JSON (or empty) — fall through to generic handling
    }
  }

  if (!res.ok) {
    let msg = res.statusText;
    let detail = null;
    try {
      const j = await res.json();
      msg = j.error || j.detail || msg;
      detail = j;
    } catch {
      try { msg = await res.text(); } catch {}
    }
    // 422 = validation error from FastAPI
    if (res.status === 422 && detail?.detail) {
      msg = detail.detail.map(d => `${d.loc?.slice(-1)[0] || 'field'}: ${d.msg}`).join('; ');
    }
    throw new ApiError(msg, res.status, detail);
  }

  if (res.status === 204) return null;
  return res.json();
}

export async function apiUpload(url, formData, signal) {
  return api(url, { method: 'POST', body: formData, signal });
}

export async function apiPost(url, data, signal) {
  return api(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
    signal,
  });
}

export async function apiPut(url, data, signal) {
  return api(url, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
    signal,
  });
}

export async function apiDelete(url, signal) {
  return api(url, { method: 'DELETE', signal });
}
