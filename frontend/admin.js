// Admin Dashboard JS Logic
const tg = window.Telegram?.WebApp;
if (tg) {
    tg.ready();
    tg.expand();
}

// Global State
let allRequests = [];
let selectedEmails = new Set();
let activeFilter = 'New';
let searchQuery = '';
let currentOpenEmail = null;

// DOM Elements
const statTotalEl = document.getElementById('stat-total');
const statPendingEl = document.getElementById('stat-pending');
const statApprovedEl = document.getElementById('stat-approved');
const statDeniedEl = document.getElementById('stat-denied');
const requestsListEl = document.getElementById('requests-list');
const listCountLabel = document.getElementById('list-count-label');
const searchInputEl = document.getElementById('search-input');
const selectAllCheckbox = document.getElementById('select-all-checkbox');
const bulkBar = document.getElementById('bulk-bar');
const bulkCountEl = document.getElementById('bulk-count');
const drawerOverlay = document.getElementById('drawer-overlay');
const drawer = document.getElementById('drawer');
const drawerCloseBtn = document.getElementById('drawer-close-btn');

// Detail Modal Elements
const detailNameEl = document.getElementById('detail-name');
const detailEmailEl = document.getElementById('detail-email');
const detailCountryEl = document.getElementById('detail-country');
const detailTelegramEl = document.getElementById('detail-telegram');
const detailStatusEl = document.getElementById('detail-status');
const detailNotesEl = document.getElementById('detail-notes');
const saveNotesBtn = document.getElementById('save-notes-btn');
const historyListEl = document.getElementById('history-list');

// Headers helper
function getHeaders() {
    const headers = {
        'Content-Type': 'application/json'
    };
    if (tg?.initData) {
        headers['Authorization'] = tg.initData;
    } else {
        // Fallback for direct browser local debug/testing
        headers['Authorization'] = 'MOCK_TOKEN';
    }
    return headers;
}

// API Calls
async function fetchStats() {
    try {
        const response = await fetch('/api/admin/stats', { headers: getHeaders() });
        if (!response.ok) throw new Error('Stats fetch failed');
        const stats = await response.json();
        
        statTotalEl.textContent = stats.total;
        statPendingEl.textContent = stats.pending;
        statApprovedEl.textContent = stats.approved;
        statDeniedEl.textContent = stats.denied;
    } catch (err) {
        console.error(err);
    }
}

async function fetchRequests() {
    try {
        showLoadingState();
        let url = `/api/admin/requests?status_filter=${activeFilter}`;
        if (searchQuery) {
            url += `&search=${encodeURIComponent(searchQuery)}`;
        }
        
        const response = await fetch(url, { headers: getHeaders() });
        if (!response.ok) throw new Error('Requests fetch failed');
        
        allRequests = await response.json();
        renderTable();
    } catch (err) {
        console.error(err);
        requestsListEl.innerHTML = `
            <div style="text-align: center; padding: 40px; color: #ef4444;">
                ❌ Failed to load requests. Please try refreshing.
            </div>
        `;
    }
}

async function runAction(emails, actionName) {
    try {
        tg?.showScanQrPopup && tg.showProgress && tg.showProgress();
        const response = await fetch('/api/admin/action', {
            method: 'POST',
            headers: getHeaders(),
            body: JSON.stringify({ emails, action: actionName })
        });
        
        const result = await response.json();
        if (result.status === 'success') {
            tg?.HapticFeedback?.notificationOccurred('success');
            // Reset selection
            selectedEmails.clear();
            selectAllCheckbox.checked = false;
            updateBulkBar();
            
            // Refresh
            await fetchStats();
            await fetchRequests();
            
            if (currentOpenEmail && emails.includes(currentOpenEmail)) {
                // If the currently open user details is one of the modified ones, update details status badge
                document.getElementById('detail-status').innerHTML = getStatusBadge(actionName === 'Approve' ? 'Approved' : actionName === 'Deny' ? 'Denied' : 'Blacklisted');
            }
        } else {
            alert(`Failed: ${result.failed} actions failed.`);
        }
    } catch (err) {
        console.error(err);
        alert('Error performing action.');
    } finally {
        tg?.showScanQrPopup && tg.closeProgress && tg.closeProgress();
    }
}

// UI Rendering
function showLoadingState() {
    requestsListEl.innerHTML = `
        <div style="text-align: center; padding: 40px; color: var(--tg-theme-hint-color);">
            <div class="spinner" style="margin: 0 auto 10px;"></div>
            Loading registrant data...
        </div>
    `;
}

function getStatusBadge(status) {
    const normalized = status ? status.toLowerCase() : 'pending';
    if (normalized === 'approved') return '<span class="badge badge-approved">Approved</span>';
    if (normalized === 'denied') return '<span class="badge badge-denied">Denied</span>';
    if (normalized === 'blacklisted') return '<span class="badge badge-blacklisted">Blacklisted</span>';
    return '<span class="badge badge-pending">Pending</span>';
}

function renderTable() {
    listCountLabel.textContent = `Showing ${allRequests.length} item${allRequests.length !== 1 ? 's' : ''}`;
    
    if (allRequests.length === 0) {
        requestsListEl.innerHTML = `
            <div style="text-align: center; padding: 40px; color: var(--tg-theme-hint-color);">
                No registrants found matching the filter or search term.
            </div>
        `;
        return;
    }
    
    requestsListEl.innerHTML = '';
    allRequests.forEach(req => {
        const card = document.createElement('div');
        card.className = 'request-card';
        card.dataset.email = req.registered_email;
        
        // Checkbox container
        const cbContainer = document.createElement('div');
        cbContainer.className = 'card-checkbox';
        cbContainer.onclick = (e) => e.stopPropagation(); // prevent drawer on checkbox click
        
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = selectedEmails.has(req.registered_email);
        checkbox.onchange = () => toggleSelectEmail(req.registered_email, checkbox.checked);
        cbContainer.appendChild(checkbox);
        card.appendChild(cbContainer);
        
        // Main info container
        const mainInfo = document.createElement('div');
        mainInfo.className = 'card-main-info';
        
        const topRow = document.createElement('div');
        topRow.className = 'card-row-top';
        
        const nameSpan = document.createElement('span');
        nameSpan.className = 'card-name';
        nameSpan.textContent = req.zoom_name || 'Manual Profile';
        topRow.appendChild(nameSpan);
        
        if (req.country) {
            const regionSpan = document.createElement('span');
            regionSpan.className = 'card-region';
            regionSpan.textContent = req.country;
            topRow.appendChild(regionSpan);
        }
        mainInfo.appendChild(topRow);
        
        const emailDiv = document.createElement('div');
        emailDiv.className = 'card-email';
        emailDiv.textContent = req.registered_email;
        mainInfo.appendChild(emailDiv);
        
        const metaDiv = document.createElement('div');
        metaDiv.className = 'card-meta';
        
        metaDiv.innerHTML = getStatusBadge(req.global_status);
        
        if (req.created_at) {
            const timeSpan = document.createElement('span');
            timeSpan.className = 'card-time';
            const dateStr = new Date(req.created_at).toLocaleDateString(undefined, {month: 'short', day: 'numeric'});
            timeSpan.textContent = `· ${dateStr}`;
            metaDiv.appendChild(timeSpan);
        }
        mainInfo.appendChild(metaDiv);
        card.appendChild(mainInfo);
        
        // Quick Action Buttons
        const actionsDiv = document.createElement('div');
        actionsDiv.className = 'card-actions';
        actionsDiv.onclick = (e) => e.stopPropagation();
        
        if (req.global_status !== 'Approved') {
            const approveBtn = document.createElement('button');
            approveBtn.className = 'action-icon-btn';
            approveBtn.innerHTML = '🟢';
            approveBtn.title = 'Approve';
            approveBtn.onclick = () => runAction([req.registered_email], 'Approve');
            actionsDiv.appendChild(approveBtn);
        }
        if (req.global_status !== 'Denied') {
            const denyBtn = document.createElement('button');
            denyBtn.className = 'action-icon-btn';
            denyBtn.innerHTML = '🔴';
            denyBtn.title = 'Deny';
            denyBtn.onclick = () => runAction([req.registered_email], 'Deny');
            actionsDiv.appendChild(denyBtn);
        }
        card.appendChild(actionsDiv);
        
        // Click on the card opens detail drawer
        card.onclick = () => openDrawer(req);
        
        requestsListEl.appendChild(card);
    });
}

// Checkboxes Selection Control
function toggleSelectEmail(email, isChecked) {
    if (isChecked) {
        selectedEmails.add(email);
    } else {
        selectedEmails.delete(email);
    }
    updateBulkBar();
}

function toggleSelectAll(isChecked) {
    allRequests.forEach(req => {
        const card = document.querySelector(`.request-card[data-email="${req.registered_email}"]`);
        if (card) {
            const checkbox = card.querySelector('.card-checkbox input[type="checkbox"]');
            if (checkbox) {
                checkbox.checked = isChecked;
                toggleSelectEmail(req.registered_email, isChecked);
            }
        }
    });
}

function updateBulkBar() {
    const count = selectedEmails.size;
    if (count > 0) {
        bulkCountEl.textContent = `${count} user${count > 1 ? 's' : ''} selected`;
        bulkBar.classList.add('active');
    } else {
        bulkBar.classList.remove('active');
    }
}

// Side Drawer Detail Controls
async function openDrawer(user) {
    currentOpenEmail = user.registered_email;
    detailNameEl.textContent = user.zoom_name || 'Manual Profile';
    detailEmailEl.textContent = user.registered_email;
    detailCountryEl.textContent = user.country ? `${user.country}` : 'Not Specified';
    
    const tgUsername = user.telegram_username ? `@${user.telegram_username}` : 'No Telegram account';
    const tgIdStr = user.telegram_id ? ` (ID: ${user.telegram_id})` : '';
    detailTelegramEl.textContent = `${tgUsername}${tgIdStr}`;
    
    detailStatusEl.innerHTML = getStatusBadge(user.global_status);
    detailNotesEl.value = user.behavior_notes || '';
    
    drawerOverlay.classList.add('active');
    drawer.classList.add('active');
    
    // Fetch History
    historyListEl.innerHTML = '<p style="font-size: 12px; color: var(--tg-theme-hint-color);">Loading submissions...</p>';
    try {
        const response = await fetch(`/api/admin/history?email=${encodeURIComponent(user.registered_email)}`, { headers: getHeaders() });
        const history = await response.json();
        
        if (history.length === 0) {
            historyListEl.innerHTML = '<p style="font-size: 12px; color: var(--tg-theme-hint-color);">No submission history logged.</p>';
            return;
        }
        
        historyListEl.innerHTML = '';
        history.forEach(item => {
            const hDiv = document.createElement('div');
            hDiv.className = 'history-item';
            
            const dateStr = new Date(item.action_timestamp).toLocaleString();
            hDiv.innerHTML = `
                <div class="history-header">
                    <span style="font-weight: 600;">Meeting ID: ${item.meeting_id || 'Unknown'}</span>
                    <span class="badge ${item.action_taken === 'Approved' ? 'badge-approved' : item.action_taken === 'Denied' ? 'badge-denied' : 'badge-pending'}">${item.action_taken}</span>
                </div>
                <div style="color: var(--tg-theme-hint-color); font-size: 11px;">Zoom Name: ${item.submitted_zoom_name}</div>
                <div style="color: var(--tg-theme-hint-color); font-size: 10px; margin-top: 2px;">${dateStr}</div>
            `;
            historyListEl.appendChild(hDiv);
        });
    } catch (err) {
        historyListEl.innerHTML = '<p style="font-size: 12px; color: #ef4444;">Failed to load history.</p>';
    }
}

function closeDrawer() {
    drawerOverlay.classList.remove('active');
    drawer.classList.remove('active');
    currentOpenEmail = null;
}

// Save Notes Action
async function saveNotes() {
    if (!currentOpenEmail) return;
    try {
        saveNotesBtn.disabled = true;
        saveNotesBtn.textContent = 'Saving...';
        
        const response = await fetch('/api/admin/notes', {
            method: 'POST',
            headers: getHeaders(),
            body: JSON.stringify({ email: currentOpenEmail, notes: detailNotesEl.value })
        });
        
        const result = await response.json();
        if (result.status === 'success') {
            tg?.HapticFeedback?.notificationOccurred('success');
            // Update local memory
            const userIdx = allRequests.findIndex(r => r.registered_email === currentOpenEmail);
            if (userIdx !== -1) {
                allRequests[userIdx].behavior_notes = detailNotesEl.value;
            }
            alert('Notes saved successfully!');
        } else {
            alert('Failed to save notes.');
        }
    } catch (err) {
        console.error(err);
        alert('Error saving notes.');
    } finally {
        saveNotesBtn.disabled = false;
        saveNotesBtn.textContent = 'Save Notes';
    }
}

// Event Listeners
document.querySelectorAll('.filter-pills .pill').forEach(pill => {
    pill.onclick = (e) => {
        document.querySelector('.filter-pills .pill.active').classList.remove('active');
        pill.classList.add('active');
        activeFilter = pill.dataset.filter;
        
        // Reset selections on filter change
        selectedEmails.clear();
        selectAllCheckbox.checked = false;
        updateBulkBar();
        
        fetchRequests();
    };
});

searchInputEl.oninput = (e) => {
    searchQuery = e.target.value.trim();
    // Fetch requests on input with debounce-like behavior if desired, or directly
    fetchRequests();
};

selectAllCheckbox.onchange = (e) => {
    toggleSelectAll(e.target.checked);
};

// Drawer controls
drawerCloseBtn.onclick = closeDrawer;
drawerOverlay.onclick = closeDrawer;
saveNotesBtn.onclick = saveNotes;

// Bulk action triggers
document.getElementById('bulk-btn-cancel').onclick = () => {
    selectedEmails.clear();
    selectAllCheckbox.checked = false;
    // Uncheck all card checkboxes
    document.querySelectorAll('.card-checkbox input[type="checkbox"]').forEach(cb => cb.checked = false);
    updateBulkBar();
};

document.getElementById('bulk-btn-approve').onclick = () => {
    if (selectedEmails.size === 0) return;
    const emailsList = Array.from(selectedEmails);
    if (confirm(`Are you sure you want to APPROVE all ${emailsList.length} selected users?`)) {
        runAction(emailsList, 'Approve');
    }
};

document.getElementById('bulk-btn-deny').onclick = () => {
    if (selectedEmails.size === 0) return;
    const emailsList = Array.from(selectedEmails);
    if (confirm(`Are you sure you want to DENY all ${emailsList.length} selected users?`)) {
        runAction(emailsList, 'Deny');
    }
};

// Initial Execution
fetchStats();
fetchRequests();
