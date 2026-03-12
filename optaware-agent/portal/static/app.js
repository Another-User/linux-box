/**
 * OptAware Portal — Client-side JavaScript
 */

const API_BASE = '/api';
let ws = null;
let autoRefresh = null;

// --- API Helper ---

async function fetchAPI(endpoint, options = {}) {
    const defaults = {
        headers: {
            'Content-Type': 'application/json',
            'X-API-Key': localStorage.getItem('optaware_api_key') || '',
        },
    };
    const config = { ...defaults, ...options, headers: { ...defaults.headers, ...options.headers } };

    try {
        const response = await fetch(`${API_BASE}${endpoint}`, config);
        if (response.status === 401) {
            showNotification('Authentication required. Set API key in Settings.', 'error');
            return null;
        }
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        return await response.json();
    } catch (err) {
        console.error(`API error (${endpoint}):`, err);
        showNotification(`API error: ${err.message}`, 'error');
        return null;
    }
}

// --- Notifications ---

function showNotification(message, type = 'info') {
    const container = document.getElementById('notifications') || createNotificationContainer();
    const el = document.createElement('div');
    el.className = `notification notification-${type}`;
    el.textContent = message;
    el.style.cssText = `
        padding: 12px 16px; margin-bottom: 8px; border-radius: 8px; font-size: 0.9rem;
        background: ${type === 'error' ? 'rgba(233,69,96,0.2)' : type === 'success' ? 'rgba(0,200,83,0.2)' : 'rgba(66,165,245,0.2)'};
        color: ${type === 'error' ? '#e94560' : type === 'success' ? '#00c853' : '#42a5f5'};
        border: 1px solid ${type === 'error' ? '#e94560' : type === 'success' ? '#00c853' : '#42a5f5'};
        animation: fadeIn 0.3s ease;
    `;
    container.appendChild(el);
    setTimeout(() => el.remove(), 5000);
}

function createNotificationContainer() {
    const c = document.createElement('div');
    c.id = 'notifications';
    c.style.cssText = 'position: fixed; top: 70px; right: 20px; z-index: 1000; width: 350px;';
    document.body.appendChild(c);
    return c;
}

// --- Dashboard ---

async function loadDashboard() {
    const status = await fetchAPI('/status');
    if (!status) return;

    updateMetricCard('cpu', status.cpu_percent, '%');
    updateMetricCard('memory', status.memory_percent, '%');
    updateMetricCard('disk', status.disk_percent, '%');
    updateMetricCard('services', status.services_running, ` / ${status.services_total}`);
}

function updateMetricCard(id, value, suffix = '') {
    const el = document.getElementById(`metric-${id}`);
    if (el) {
        el.textContent = typeof value === 'number' ? value.toFixed(1) + suffix : value + suffix;
        // Color coding
        if (typeof value === 'number' && suffix === '%') {
            el.style.color = value > 90 ? '#e94560' : value > 75 ? '#ffa726' : '#00c853';
        }
    }
}

function startAutoRefresh(interval = 30000) {
    stopAutoRefresh();
    loadDashboard();
    autoRefresh = setInterval(loadDashboard, interval);
}

function stopAutoRefresh() {
    if (autoRefresh) {
        clearInterval(autoRefresh);
        autoRefresh = null;
    }
}

// --- Services ---

async function loadServices() {
    const services = await fetchAPI('/services');
    const grid = document.getElementById('services-grid');
    if (!services || !grid) return;

    grid.innerHTML = services.map(svc => `
        <div class="service-card">
            <div class="service-card-header">
                <span class="service-card-name">${svc.display_name}</span>
                <span class="badge badge-${svc.status}">${svc.status}</span>
            </div>
            <div class="service-card-meta">
                Type: ${svc.service_type} | Unit: ${svc.systemd_unit || 'N/A'} | Port: ${svc.port || 'N/A'}
            </div>
            <div class="service-card-actions">
                <button class="btn btn-sm btn-success" onclick="serviceAction('${svc.name}', 'restart')">Restart</button>
                <button class="btn btn-sm" onclick="serviceDiagnose('${svc.name}')">Diagnose</button>
            </div>
        </div>
    `).join('');
}

async function serviceAction(name, action) {
    if (!confirm(`${action} service "${name}"?`)) return;
    const result = await fetchAPI(`/services/${name}/${action}`, { method: 'POST' });
    if (result) {
        showNotification(`${action} queued for ${name}`, 'success');
        setTimeout(loadServices, 2000);
    }
}

async function serviceDiagnose(name) {
    showNotification(`Running diagnostics for ${name}...`, 'info');
}

// --- Events ---

async function loadEvents(severity = 'all', service = 'all') {
    const params = new URLSearchParams();
    if (severity !== 'all') params.set('severity', severity);
    if (service !== 'all') params.set('service', service);

    const events = await fetchAPI(`/events?${params}`);
    const container = document.getElementById('events-list');
    if (!events || !container) return;

    if (events.length === 0) {
        container.innerHTML = '<p class="loading">No events to display. Events appear when the daemon is running.</p>';
        return;
    }

    container.innerHTML = events.map(evt => `
        <div class="event-card severity-${evt.severity}">
            <div style="display: flex; justify-content: space-between;">
                <span class="badge badge-${evt.severity}">${evt.severity}</span>
                <span class="event-time">${new Date(evt.timestamp).toLocaleString()}</span>
            </div>
            <div class="event-message">${evt.message}</div>
            ${evt.service_name ? `<div class="event-time">Service: ${evt.service_name}</div>` : ''}
        </div>
    `).join('');
}

function filterEvents() {
    const severity = document.getElementById('severity-filter')?.value || 'all';
    const service = document.getElementById('service-filter')?.value || 'all';
    loadEvents(severity, service);
}

// --- Actions ---

async function loadActions() {
    const actions = await fetchAPI('/actions');
    const container = document.getElementById('actions-list');
    if (!actions || !container) return;

    if (actions.length === 0) {
        container.innerHTML = '<p class="loading">No pending actions.</p>';
        return;
    }

    container.innerHTML = actions.map(action => `
        <div class="card">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <div>
                    <strong>${action.action_type}</strong>
                    <span class="badge badge-${action.status}">${action.status}</span>
                </div>
                <div class="btn-group">
                    ${action.status === 'pending' ? `
                        <button class="btn btn-sm btn-success" onclick="approveAction('${action.id}')">Approve</button>
                        <button class="btn btn-sm btn-danger" onclick="denyAction('${action.id}')">Deny</button>
                    ` : ''}
                </div>
            </div>
            <div class="event-time">${new Date(action.created_at).toLocaleString()}</div>
        </div>
    `).join('');
}

async function approveAction(id) {
    if (!confirm('Approve this action?')) return;
    const result = await fetchAPI(`/actions/${id}/approve`, { method: 'POST' });
    if (result) {
        showNotification('Action approved', 'success');
        loadActions();
    }
}

async function denyAction(id) {
    const reason = prompt('Reason for denial:');
    if (reason === null) return;
    const result = await fetchAPI(`/actions/${id}/deny`, { method: 'POST', body: JSON.stringify({ reason }) });
    if (result) {
        showNotification('Action denied', 'info');
        loadActions();
    }
}

// --- Chat (Ask) ---

async function sendQuestion() {
    const input = document.getElementById('chat-input');
    const messages = document.getElementById('chat-messages');
    if (!input || !messages) return;

    const question = input.value.trim();
    if (!question) return;

    // Add user message
    messages.innerHTML += `<div class="chat-message user">${escapeHtml(question)}</div>`;
    input.value = '';
    messages.scrollTop = messages.scrollHeight;

    // Show loading
    messages.innerHTML += `<div class="chat-message assistant loading" id="loading-msg">Thinking...</div>`;
    messages.scrollTop = messages.scrollHeight;

    const result = await fetchAPI('/ask', {
        method: 'POST',
        body: JSON.stringify({ question }),
    });

    // Remove loading
    document.getElementById('loading-msg')?.remove();

    if (result) {
        messages.innerHTML += `<div class="chat-message assistant">${escapeHtml(result.answer)}</div>`;
    } else {
        messages.innerHTML += `<div class="chat-message assistant">Error: Could not get a response.</div>`;
    }
    messages.scrollTop = messages.scrollHeight;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// --- WebSocket ---

function connectWebSocket() {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${location.host}/ws/events`);

    ws.onopen = () => console.log('WebSocket connected');
    ws.onclose = () => {
        console.log('WebSocket disconnected, reconnecting in 5s...');
        setTimeout(connectWebSocket, 5000);
    };
    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            handleRealtimeEvent(data);
        } catch (e) {
            console.error('WebSocket parse error:', e);
        }
    };
}

function handleRealtimeEvent(data) {
    if (data.type === 'event') {
        showNotification(`[${data.severity}] ${data.message}`, data.severity === 'error' ? 'error' : 'info');
    }
}

// --- Settings ---

function saveApiKey() {
    const key = document.getElementById('api-key-input')?.value;
    if (key) {
        localStorage.setItem('optaware_api_key', key);
        showNotification('API key saved', 'success');
    }
}

// --- Init ---

document.addEventListener('DOMContentLoaded', () => {
    // Handle chat enter key
    const chatInput = document.getElementById('chat-input');
    if (chatInput) {
        chatInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') sendQuestion();
        });
    }

    // Connect WebSocket if available
    try { connectWebSocket(); } catch (e) { console.log('WebSocket not available'); }
});
