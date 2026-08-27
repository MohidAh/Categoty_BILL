// Offline Outbox v3.1 — Generic entity queue with client_uuid idempotency
// Supports: sale | return | customer_payment | stock_adjustment
// Each entry gets a crypto.randomUUID() for server-side dedup.
// Sync: on 'online' + startup + manual button; exponential backoff 2s→60s; 5 failures → 'failed'.

const DB_NAME = 'billbook-offline';
const DB_VERSION = 2;
const STORE = 'outbox';

// ─── IndexedDB open helper (v2: renamed store, added entity field) ───
function openDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = (e) => {
      const db = e.target.result;
      // Create new outbox store
      if (!db.objectStoreNames.contains(STORE)) {
        const store = db.createObjectStore(STORE, { keyPath: 'id', autoIncrement: true });
        store.createIndex('entity', 'entity', { unique: false });
        store.createIndex('client_uuid', 'client_uuid', { unique: false });
        store.createIndex('status', 'status', { unique: false });
      }
      // Keep old sales_queue for backward compat (SW reads it)
      if (!db.objectStoreNames.contains('sales_queue')) {
        db.createObjectStore('sales_queue', { keyPath: 'id', autoIncrement: true });
      }
    };
    req.onsuccess = (e) => resolve(e.target.result);
    req.onerror = (e) => reject(e.target.error);
  });
}

// ─── Generate client_uuid ───
function genUUID() {
  if (crypto && crypto.randomUUID) return crypto.randomUUID();
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = Math.random() * 16 | 0;
    return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
  });
}

// ─── Queue any entity for later sync ───
export async function queueEntity(entity, endpoint, method, payload) {
  const db = await openDB();
  const clientUuid = genUUID();
  return new Promise((resolve, reject) => {
    const tx = db.transaction([STORE], 'readwrite');
    const store = tx.objectStore(STORE);
    const item = {
      entity,           // 'sale' | 'return' | 'customer_payment' | 'stock_adjustment'
      endpoint,         // '/api/sales' etc
      method,            // 'POST' | 'PUT'
      payload,
      client_uuid: clientUuid,
      created_at: new Date().toISOString(),
      sync_attempts: 0,
      status: 'pending', // 'pending' | 'syncing' | 'synced' | 'failed'
      last_error: null,
    };
    const req = store.add(item);
    req.onsuccess = () => resolve({ id: req.result, client_uuid: clientUuid });
    req.onerror = () => reject(req.error);
  });
}

// ─── Queue a sale (backward-compat wrapper) ───
export async function queueSale(payload) {
  // Add client_uuid to payload if not present
  if (!payload.client_uuid) {
    payload.client_uuid = genUUID();
  }
  const result = await queueEntity('sale', '/api/sales', 'POST', payload);
  // Also add to legacy sales_queue for SW backward compat
  const db = await openDB();
  return new Promise((resolve) => {
    const tx = db.transaction(['sales_queue'], 'readwrite');
    tx.objectStore('sales_queue').add({
      payload, created_at: new Date().toISOString(), sync_attempts: 0,
    });
    tx.oncomplete = () => resolve(result.id);
    tx.onerror = () => resolve(result.id); // Don't fail if legacy store fails
  });
}

// ─── Get count of pending items ───
export async function getQueueCount() {
  try {
    const db = await openDB();
    return new Promise((resolve) => {
      const tx = db.transaction([STORE], 'readonly');
      const store = tx.objectStore(STORE);
      const countReq = store.count();
      countReq.onsuccess = () => resolve(countReq.result);
      countReq.onerror = () => resolve(0);
    });
  } catch {
    return 0;
  }
}

// ─── Get pending count by entity type ───
export async function getQueueCountByEntity() {
  try {
    const db = await openDB();
    return new Promise((resolve) => {
      const tx = db.transaction([STORE], 'readonly');
      const store = tx.objectStore(STORE);
      const req = store.getAll();
      req.onsuccess = () => {
        const items = req.result || [];
        const counts = { sale: 0, return: 0, customer_payment: 0, stock_adjustment: 0 };
        for (const item of items) {
          if (item.status !== 'synced' && counts[item.entity] !== undefined) {
            counts[item.entity]++;
          }
        }
        resolve(counts);
      };
      req.onerror = () => resolve({ sale: 0, return: 0, customer_payment: 0, stock_adjustment: 0 });
    });
  } catch {
    return { sale: 0, return: 0, customer_payment: 0, stock_adjustment: 0 };
  }
}

// ─── Get all queued items ───
export async function getQueuedSales() {
  try {
    const db = await openDB();
    return new Promise((resolve) => {
      const tx = db.transaction([STORE], 'readonly');
      const store = tx.objectStore(STORE);
      const getAllReq = store.getAll();
      getAllReq.onsuccess = () => resolve(getAllReq.result || []);
      getAllReq.onerror = () => resolve([]);
    });
  } catch {
    return [];
  }
}

// ─── Remove from queue (after successful sync) ───
export async function removeFromQueue(id) {
  const db = await openDB();
  return new Promise((resolve) => {
    const tx = db.transaction([STORE], 'readwrite');
    tx.objectStore(STORE).delete(id);
    tx.oncomplete = () => resolve(true);
    tx.onerror = () => resolve(false);
  });
}

// ─── Mark item status ───
export async function updateItemStatus(id, status, error = null) {
  const db = await openDB();
  return new Promise((resolve) => {
    const tx = db.transaction([STORE], 'readwrite');
    const store = tx.objectStore(STORE);
    const getReq = store.get(id);
    getReq.onsuccess = () => {
      const item = getReq.result;
      if (item) {
        item.status = status;
        item.sync_attempts = (item.sync_attempts || 0) + 1;
        if (error) item.last_error = error;
        if (status === 'synced') {
          store.delete(id);
        } else {
          store.put(item);
        }
      }
    };
    tx.oncomplete = () => resolve(true);
    tx.onerror = () => resolve(false);
  });
}

// ─── Trigger flush (exponential backoff) ───
let flushInFlight = false;
let backoffMs = 2000;
const MAX_BACKOFF = 60000;
const MAX_ATTEMPTS = 5;

export async function triggerFlush() {
  if (flushInFlight) return;
  flushInFlight = true;

  // Update sync pill
  window.dispatchEvent(new CustomEvent('outbox-syncing'));

  const queued = await getQueuedSales();
  const pending = queued.filter(q => q.status !== 'synced' && q.status !== 'failed');
  let allOK = true;

  for (const item of pending) {
    if (item.sync_attempts >= MAX_ATTEMPTS) {
      await updateItemStatus(item.id, 'failed', 'Max attempts reached');
      window.dispatchEvent(new CustomEvent('outbox-item-failed', {
        detail: { id: item.id, entity: item.entity, error: 'Max retry attempts reached' }
      }));
      continue;
    }

    try {
      const body = { ...item.payload };
      if (item.client_uuid) body.client_uuid = item.client_uuid;

      const res = await fetch(item.endpoint, {
        method: item.method || 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify(body),
      });

      if (res.ok) {
        await removeFromQueue(item.id);
        window.dispatchEvent(new CustomEvent('outbox-item-synced', {
          detail: { id: item.id, entity: item.entity }
        }));
        backoffMs = 2000; // Reset backoff on success
      } else if (res.status === 409) {
        // Duplicate (idempotent) — already synced, remove
        await removeFromQueue(item.id);
        backoffMs = 2000;
      } else {
        // Server error — increment attempts, keep in queue
        await updateItemStatus(item.id, 'pending', `HTTP ${res.status}`);
        allOK = false;
        break;
      }
    } catch (e) {
      // Network error — will retry with backoff
      await updateItemStatus(item.id, 'pending', e.message);
      allOK = false;
      break;
    }
  }

  flushInFlight = false;

  if (allOK) {
    window.dispatchEvent(new CustomEvent('outbox-synced'));
  } else {
    // Schedule retry with exponential backoff
    setTimeout(() => {
      if (navigator.onLine) triggerFlush();
    }, backoffMs);
    backoffMs = Math.min(backoffMs * 2, MAX_BACKOFF);
  }
}

// ─── Online/offline detection ───
export function isOnline() {
  return navigator.onLine;
}

// ─── Initialize ───
let initialized = false;
export function initOfflineQueue() {
  if (initialized) return;
  initialized = true;

  window.addEventListener('online', () => {
    backoffMs = 2000; // Reset backoff
    triggerFlush();
  });

  // Startup flush
  if (navigator.onLine) {
    setTimeout(() => triggerFlush(), 2000);
  }
}

// ─── Generate offline invoice number ───
export function generateOfflineInvoiceNo() {
  const date = new Date();
  const ymd = date.toISOString().slice(0, 10).replace(/-/g, '');
  const hms = date.toTimeString().slice(0, 8).replace(/:/g, '');
  return `OFFLINE-${ymd}-${hms}`;
}

// ─── Get failed items for manual retry ───
export async function getFailedItems() {
  const all = await getQueuedSales();
  return all.filter(q => q.status === 'failed');
}

// ─── Retry a failed item ───
export async function retryFailedItem(id) {
  const db = await openDB();
  return new Promise((resolve) => {
    const tx = db.transaction([STORE], 'readwrite');
    const store = tx.objectStore(STORE);
    const getReq = store.get(id);
    getReq.onsuccess = () => {
      const item = getReq.result;
      if (item) {
        item.status = 'pending';
        item.sync_attempts = 0;
        item.last_error = null;
        store.put(item);
      }
    };
    tx.oncomplete = () => {
      triggerFlush();
      resolve(true);
    };
  });
}
