/**
 * OptAware Portal — Client-side JavaScript
 *
 * Globals exposed:
 *   fetchAPI(path, options?)     — authenticated fetch helper
 *   escHtml(str)                 — HTML-escape a string
 *   approveAction(id, btn?)      — approve a pending action
 *   denyAction(id, btn?)         — deny a pending action
 *   serviceAction(name, action, btn?) — trigger a service control action
 *   showActionDetail(act)        — open action detail modal (set by actions page)
 *   showServiceDetail(svc)       — open service detail modal (set by services page)
 */

'use strict';

/* ============================================================
   API HELPER
   ============================================================ */

/**
 * Fetch a portal API endpoint.
 *
 * @param {string} endpoint  - Full URL or path (e.g. '/api/services').
 *                             Paths that start with '/' are used as-is.
 * @param {RequestInit} [options] - Optional fetch init overrides.
 * @returns {Promise<any|null>}  Parsed JSON or null on error.
 */
async function fetchAPI(endpoint, options = {}) {
  const url = endpoint.startsWith('http') ? endpoint : endpoint;
  const headers = {
    'Content-Type': 'application/json',
    ...(options.headers || {}),
  };

  try {
    const response = await fetch(url, { ...options, headers });
    if (response.status === 204) return {};
    if (!response.ok) {
      const body = await response.text().catch(() => '');
      console.warn('[OptAware] API', response.status, url, body);
      return null;
    }
    const ct = response.headers.get('content-type') || '';
    if (ct.includes('application/json')) return await response.json();
    return await response.text();
  } catch (err) {
    console.error('[OptAware] fetch error', url, err);
    return null;
  }
}

/* ============================================================
   HTML ESCAPE
   ============================================================ */

/**
 * Escape a string for safe insertion into HTML.
 * @param {any} str
 * @returns {string}
 */
function escHtml(str) {
  if (str == null) return '';
  const s = String(str);
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/* ============================================================
   ACTION APPROVE / DENY  (global, used by dashboard + actions page)
   ============================================================ */

window.approveAction = async function(id, btn) {
  if (!confirm('Approve this action?')) return;
  if (btn) { btn.disabled = true; btn.textContent = 'Approving…'; }

  const result = await fetchAPI('/api/actions/' + id + '/approve', { method: 'POST' });
  if (result !== null) {
    _showToast('Action approved.', 'success');
    const card = document.getElementById('pending-' + id);
    if (card) {
      card.style.transition = 'opacity 0.3s';
      card.style.opacity = '0';
      setTimeout(() => card.remove(), 300);
    }
  } else {
    _showToast('Failed to approve action.', 'error');
    if (btn) { btn.disabled = false; btn.textContent = '✓ Approve'; }
  }
};

window.denyAction = async function(id, btn) {
  if (!confirm('Deny this action? It will be removed from the queue.')) return;
  if (btn) { btn.disabled = true; btn.textContent = 'Denying…'; }

  const result = await fetchAPI('/api/actions/' + id + '/deny', { method: 'POST' });
  if (result !== null) {
    _showToast('Action denied.', 'info');
    const card = document.getElementById('pending-' + id);
    if (card) {
      card.style.transition = 'opacity 0.3s';
      card.style.opacity = '0';
      setTimeout(() => card.remove(), 300);
    }
  } else {
    _showToast('Failed to deny action.', 'error');
    if (btn) { btn.disabled = false; btn.textContent = '✗ Deny'; }
  }
};

/* ============================================================
   SERVICE CONTROL  (global)
   ============================================================ */

window.serviceAction = async function(name, action, btn) {
  const labels = { start: 'Starting', stop: 'Stopping', restart: 'Restarting', diagnose: 'Diagnosing' };
  const origText = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = (labels[action] || action) + '…'; }

  const result = await fetchAPI('/api/services/' + encodeURIComponent(name) + '/' + action, { method: 'POST' });
  if (result !== null) {
    const msg = (result && result.message) ? result.message : action + ' queued for ' + name;
    _showToast(msg, 'success');
  } else {
    _showToast('Failed to ' + action + ' ' + name + '.', 'error');
  }
  if (btn) { btn.disabled = false; btn.textContent = origText; }
};

/* ============================================================
   TOAST
   ============================================================ */

function _showToast(msg, type) {
  const el = document.createElement('div');
  el.className = 'toast toast-' + (type || 'info');
  el.textContent = msg;
  document.body.appendChild(el);
  // Trigger transition
  requestAnimationFrame(() => {
    requestAnimationFrame(() => el.classList.add('toast-visible'));
  });
  setTimeout(() => {
    el.classList.remove('toast-visible');
    setTimeout(() => el.remove(), 400);
  }, 3500);
}

/* ============================================================
   WEBSOCKET — real-time events
   ============================================================ */

(function initWebSocket() {
  let ws = null;
  let reconnectDelay = 3000;

  function connect() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    try {
      ws = new WebSocket(proto + '//' + location.host + '/ws/events');
    } catch (e) {
      scheduleReconnect();
      return;
    }

    ws.addEventListener('open', () => {
      console.debug('[OptAware] WebSocket connected');
      reconnectDelay = 3000;
      // Update agent status dot to indicate live connection
      const dot = document.getElementById('agent-status-dot');
      if (dot) dot.className = 'status-dot running';
    });

    ws.addEventListener('close', () => {
      console.debug('[OptAware] WebSocket closed, reconnecting…');
      scheduleReconnect();
    });

    ws.addEventListener('error', () => {
      ws.close();
    });

    ws.addEventListener('message', (evt) => {
      let data;
      try { data = JSON.parse(evt.data); } catch { return; }
      _handleWsMessage(data);
    });
  }

  function scheduleReconnect() {
    setTimeout(connect, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 1.5, 30000);
  }

  function _handleWsMessage(data) {
    if (!data) return;

    // New event broadcast
    if (data.type === 'event' || data.severity) {
      const sev = data.severity || 'info';
      const toastType = (sev === 'critical' || sev === 'error') ? 'error' : 'info';
      _showToast('[' + sev.toUpperCase() + '] ' + (data.message || ''), toastType);

      // If the events page timeline is present, prepend the new event
      const timeline = document.getElementById('events-timeline');
      if (timeline && !timeline.querySelector('.loading-state')) {
        const div = document.createElement('div');
        div.className = 'event-card sev-' + sev;
        div.innerHTML = `
          <div class="event-card-left">
            <span class="sev-badge sev-${escHtml(sev)}">${escHtml(sev)}</span>
          </div>
          <div class="event-card-body">
            <div class="event-card-msg">${escHtml(data.message || '')}</div>
            <div class="event-card-meta">
              ${data.service_name ? '<span class="meta-chip">' + escHtml(data.service_name) + '</span>' : ''}
              <span class="meta-time">just now (live)</span>
            </div>
          </div>
        `;
        timeline.insertBefore(div, timeline.firstChild);
      }
    }

    // Metric push
    if (data.type === 'metrics') {
      _applyMetricPush(data);
    }
  }

  function _applyMetricPush(m) {
    function setTxt(id, val) {
      const el = document.getElementById(id);
      if (el) el.textContent = val;
    }
    function setBar(id, pct) {
      const el = document.getElementById(id);
      if (!el) return;
      const c = Math.min(100, Math.max(0, pct || 0));
      el.style.width = c + '%';
      el.classList.remove('bar-low', 'bar-mid', 'bar-high');
      el.classList.add(c > 85 ? 'bar-high' : c > 60 ? 'bar-mid' : 'bar-low');
    }
    if (m.cpu_percent != null) {
      setTxt('cpu-value', m.cpu_percent.toFixed(1) + '%');
      setBar('cpu-bar', m.cpu_percent);
    }
    if (m.memory_percent != null) {
      setTxt('mem-value', m.memory_percent.toFixed(1) + '%');
      setBar('mem-bar', m.memory_percent);
    }
  }

  // Delay initial connect slightly to let page finish loading
  setTimeout(connect, 1200);
})();

/* ============================================================
   STUBS for pages that override these (prevents ReferenceError
   when the base template's inline scripts call them before a
   page's own script block has run)
   ============================================================ */

if (typeof window.showActionDetail === 'undefined') {
  window.showActionDetail = function() {};
}
if (typeof window.showServiceDetail === 'undefined') {
  window.showServiceDetail = function() {};
}

/* ============================================================
   GLOBAL ERROR BOUNDARY — prevents uncaught rejections from
   breaking the entire page
   ============================================================ */

window.addEventListener('unhandledrejection', function(evt) {
  console.warn('[OptAware] Unhandled promise rejection:', evt.reason);
  evt.preventDefault();
});
