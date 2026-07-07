// Admin Dashboard JS Logic
const tg = window.Telegram?.WebApp;
if (tg) {
    tg.ready();
    tg.expand();
}

// Global State
let allRequests = [];
let allDirectoryUsers = [];
let selectedEmails = new Set();
let activeFilter = 'New';
let searchQuery = '';
let directorySearchQuery = '';
let currentOpenEmail = null;
let activeTab = 'queue';

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
const drawerAvatarEl = document.getElementById('drawer-avatar');

// Detail Modal Elements
const detailNameEl = document.getElementById('detail-name');
const detailEmailEl = document.getElementById('detail-email');
const detailCountryEl = document.getElementById('detail-country');
const detailTelegramEl = document.getElementById('detail-telegram');
const detailStatusSelect = document.getElementById('detail-status-select');
const detailNotesEl = document.getElementById('detail-notes');
const saveNotesBtn = document.getElementById('save-notes-btn');
const historyListEl = document.getElementById('history-list');

const syncZoomBtn = document.getElementById('sync-zoom-btn');
const syncBtnText = document.getElementById('sync-btn-text');
const addMetaBtn = document.getElementById('add-meta-btn');
const metadataListEl = document.getElementById('metadata-list');
const addHistoryBtn = document.getElementById('add-history-btn');

// Bottom Nav & Tabs Elements
const usersDirectoryListEl = document.getElementById('users-directory-list');
const globalSearchInputEl = document.getElementById('global-search-input');
const exportCsvBtn = document.getElementById('export-csv-btn');
const lastSyncStatusEl = document.getElementById('last-sync-status');

// Settings Input Elements
const zoomMeetingIdInput = document.getElementById('zoom-meeting-id-input');
const zoomAccountIdInput = document.getElementById('zoom-account-id-input');
const zoomClientIdInput = document.getElementById('zoom-client-id-input');
const zoomClientSecretInput = document.getElementById('zoom-client-secret-input');
const zoomRegistrationLinkInput = document.getElementById('zoom-registration-link-input');
const zoomSyncIntervalInput = document.getElementById('zoom-sync-interval-input');
const saveSettingsBtn = document.getElementById('save-settings-btn');
const saveIntervalBtn = document.getElementById('save-interval-btn');

// Admin Team Elements
const adminTeamList = document.getElementById('admin-team-list');
const addAdminIdInput = document.getElementById('add-admin-id-input');
const addAdminUsernameInput = document.getElementById('add-admin-username-input');
const addAdminBtn = document.getElementById('add-admin-btn');

// Telegram Entity Resolver Elements
const tgResolveQueryInput = document.getElementById('tg-resolve-query-input');
const tgResolveBtn = document.getElementById('tg-resolve-btn');
const tgResolveResult = document.getElementById('tg-resolve-result');

// Advanced Directory Filters
const directoryFilterStatus = document.getElementById('directory-filter-status');
const directoryFilterOrigin = document.getElementById('directory-filter-origin');

// Dialog Elements
const dialogOverlay = document.getElementById('dialog-overlay');
const dialog = document.getElementById('dialog');
const dialogTitle = document.getElementById('dialog-title');
const dialogCloseBtn = document.getElementById('dialog-close-btn');
const dialogBody = document.getElementById('dialog-body');
const dialogCancelBtn = document.getElementById('dialog-cancel-btn');
const dialogSaveBtn = document.getElementById('dialog-save-btn');

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
        
        if (lastSyncStatusEl && stats.last_sync) {
            lastSyncStatusEl.textContent = `Last synced: ${stats.last_sync}`;
        }
        
        // Update filter pills with their dynamic counts
        const pillNew = document.querySelector('.filter-pills .pill[data-filter="New"]');
        const pillOnHold = document.querySelector('.filter-pills .pill[data-filter="OnHold"]');
        const pillPending = document.querySelector('.filter-pills .pill[data-filter="Pending"]');
        const pillApproved = document.querySelector('.filter-pills .pill[data-filter="Approved"]');
        const pillDenied = document.querySelector('.filter-pills .pill[data-filter="Denied"]');
        const pillAll = document.querySelector('.filter-pills .pill[data-filter="All"]');
        
        if (pillNew) pillNew.textContent = `🆕 New (≤3d) (${stats.new || 0})`;
        if (pillOnHold) pillOnHold.textContent = `⏳ On Hold (>3d) (${stats.on_hold || 0})`;
        if (pillPending) pillPending.textContent = `🟡 All Pending (${stats.pending || 0})`;
        if (pillApproved) pillApproved.textContent = `🟢 Approved (${stats.approved || 0})`;
        if (pillDenied) pillDenied.textContent = `🔴 Denied (${stats.denied || 0})`;
        if (pillAll) pillAll.textContent = `📋 All (${stats.total || 0})`;
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
            if (activeTab === 'users') {
                await fetchDirectory();
            } else {
                await fetchRequests();
            }
            
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

function getRelativeTimeAndDate(dateString) {
    if (!dateString) return '';
    const date = new Date(dateString);
    const now = new Date();
    const diffMs = now - date;
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMins / 60);
    const diffDays = Math.floor(diffHours / 24);
    
    let relativeStr = '';
    if (diffMins < 1) {
        relativeStr = 'just now';
    } else if (diffMins < 60) {
        relativeStr = `${diffMins}m ago`;
    } else if (diffHours < 24) {
        relativeStr = `${diffHours}h ago`;
    } else if (diffDays < 30) {
        relativeStr = `${diffDays}d ago`;
    } else {
        relativeStr = date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    }
    
    const absDateStr = date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    return `· ${relativeStr} (${absDateStr})`;
}

function renderRequestsList(requestsArray, targetElement, isDirectoryView) {
    if (requestsArray.length === 0) {
        targetElement.innerHTML = `
            <div style="text-align: center; padding: 40px; color: var(--tg-theme-hint-color);">
                No profiles found.
            </div>
        `;
        return;
    }
    
    targetElement.innerHTML = '';
    requestsArray.forEach(req => {
        const card = document.createElement('div');
        card.className = 'request-card';
        card.dataset.email = req.registered_email;
        
        if (!isDirectoryView) {
            // Checkbox container for bulk actions
            const cbContainer = document.createElement('div');
            cbContainer.className = 'card-checkbox';
            cbContainer.onclick = (e) => e.stopPropagation();
            
            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.checked = selectedEmails.has(req.registered_email);
            checkbox.onchange = () => toggleSelectEmail(req.registered_email, checkbox.checked);
            cbContainer.appendChild(checkbox);
            card.appendChild(cbContainer);
        }
        
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
        metaDiv.style = 'display: flex; align-items: center; gap: 6px; flex-wrap: wrap;';
        
        const hasTg = req.telegram_id && req.telegram_id !== 0;
        const sourceBadge = hasTg 
            ? '<span class="badge" style="background: rgba(36,129,204,0.12); color: #2481cc; border: 1px solid rgba(36,129,204,0.2); font-size: 10px; padding: 2px 6px; border-radius: 4px; font-weight: 500;">📱 Telegram Linked</span>' 
            : '<span class="badge" style="background: rgba(245,158,11,0.12); color: #f59e0b; border: 1px solid rgba(245,158,11,0.2); font-size: 10px; padding: 2px 6px; border-radius: 4px; font-weight: 500;">🌐 Zoom Web</span>';
            
        metaDiv.innerHTML = getStatusBadge(req.global_status) + sourceBadge;
        
        if (req.created_at) {
            const timeSpan = document.createElement('span');
            timeSpan.className = 'card-time';
            timeSpan.textContent = getRelativeTimeAndDate(req.created_at);
            metaDiv.appendChild(timeSpan);
        }
        mainInfo.appendChild(metaDiv);
        const avatarImg = document.createElement('div');
        avatarImg.className = 'card-avatar';
        avatarImg.style = 'width: 36px; height: 36px; border-radius: 50%; background: var(--tg-theme-secondary-bg-color, rgba(255,255,255,0.05)); display: flex; align-items: center; justify-content: center; font-size: 14px; font-weight: 600; margin-right: 10px; flex-shrink: 0; overflow: hidden; border: 1px solid rgba(255,255,255,0.08);';
        
        if (req.photo_url) {
            const img = document.createElement('img');
            img.src = req.photo_url;
            img.style = 'width: 100%; height: 100%; object-fit: cover;';
            avatarImg.appendChild(img);
        } else {
            avatarImg.textContent = (req.zoom_name || 'M')[0].toUpperCase();
        }
        card.appendChild(avatarImg);
        card.appendChild(mainInfo);
        
        if (!isDirectoryView) {
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
        } else {
            // Navigation arrow for directory view
            const arrowDiv = document.createElement('div');
            arrowDiv.style = 'color: var(--tg-theme-hint-color); font-size: 16px; display: flex; align-items: center; margin-left: auto; padding: 0 4px;';
            arrowDiv.innerHTML = '❯';
            card.appendChild(arrowDiv);
        }
        
        // Shared Drawer open click handler
        card.onclick = () => openDrawer(req);
        
        targetElement.appendChild(card);
    });
}

function renderTable() {
    listCountLabel.textContent = `Showing ${allRequests.length} item${allRequests.length !== 1 ? 's' : ''}`;
    renderRequestsList(allRequests, requestsListEl, false);
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
    
    drawerAvatarEl.innerHTML = '';
    if (user.photo_url) {
        const img = document.createElement('img');
        img.src = user.photo_url;
        img.style = 'width: 100%; height: 100%; object-fit: cover;';
        drawerAvatarEl.appendChild(img);
    } else {
        drawerAvatarEl.textContent = (user.zoom_name || 'M')[0].toUpperCase();
    }
    
    const tgUsername = user.telegram_username ? `@${user.telegram_username}` : 'No Telegram account';
    const tgIdStr = user.telegram_id ? ` (ID: ${user.telegram_id})` : '';
    detailTelegramEl.textContent = `${tgUsername}${tgIdStr}`;
    
    detailStatusSelect.value = user.global_status || 'Pending';
    detailStatusSelect.onchange = async () => {
        const newStatus = detailStatusSelect.value;
        if (confirm(`Are you sure you want to change the status of ${user.registered_email} to ${newStatus}?`)) {
            await runAction([user.registered_email], newStatus);
            // Refresh local request status value in current list
            const currentList = activeTab === 'users' ? allDirectoryUsers : allRequests;
            const req = currentList.find(r => r.registered_email === user.registered_email);
            if (req) req.global_status = newStatus;
        } else {
            // Reset to previous status
            detailStatusSelect.value = user.global_status || 'Pending';
        }
    };
    
    // Parse and render Metadata list
    let metadata = [];
    try {
        metadata = user.metadata ? JSON.parse(user.metadata) : [];
    } catch (e) {
        console.error("Error parsing user metadata:", e);
    }
    renderMetadataList(user.registered_email, metadata);
    
    // Setup add metadata button listener
    addMetaBtn.onclick = () => openMetadataDialog(user.registered_email, metadata);
    
    detailNotesEl.value = user.behavior_notes || '';
    
    drawerOverlay.classList.add('active');
    drawer.classList.add('active');
    
    // Setup add history entry listener
    addHistoryBtn.onclick = () => openHistoryDialog(user.registered_email);
    
    // Fetch History List
    await fetchHistoryList(user);
}

async function fetchHistoryList(user) {
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
            hDiv.style = 'position: relative; padding: 10px; border: 1px solid rgba(255, 255, 255, 0.05); border-radius: 8px; margin-bottom: 8px; box-sizing: border-box;';
            
            const dateStr = new Date(item.action_timestamp).toLocaleString();
            hDiv.innerHTML = `
                <div class="history-header" style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                    <span style="font-weight: 600; font-size: 13px;">Meeting ID: ${item.meeting_id || 'Unknown'}</span>
                    <span class="badge ${item.action_taken === 'Approved' ? 'badge-approved' : item.action_taken === 'Denied' ? 'badge-denied' : 'badge-pending'}">${item.action_taken}</span>
                </div>
                <div style="color: var(--tg-theme-hint-color); font-size: 11px;">Zoom Name: ${item.submitted_zoom_name || 'Unknown'}</div>
                <div style="color: var(--tg-theme-hint-color); font-size: 11px;">Telegram: @${item.submitted_telegram_username || 'Unknown'}</div>
                <div style="color: var(--tg-theme-hint-color); font-size: 10px; margin-top: 4px;">${dateStr}</div>
                
                <div style="margin-top: 8px; display: flex; gap: 8px; justify-content: flex-end;">
                    <button class="action-icon-btn history-edit-btn" style="padding: 2px; font-size: 11px;" title="Edit Entry">✏️ Edit</button>
                    <button class="action-icon-btn history-del-btn" style="padding: 2px; font-size: 11px;" title="Delete Entry">🗑️ Delete</button>
                </div>
            `;
            
            hDiv.querySelector('.history-edit-btn').onclick = () => openHistoryDialog(user.registered_email, item);
            hDiv.querySelector('.history-del-btn').onclick = () => deleteHistoryEntry(user.registered_email, item.id);
            
            historyListEl.appendChild(hDiv);
        });
    } catch (err) {
        historyListEl.innerHTML = '<p style="font-size: 12px; color: #ef4444;">Failed to load history.</p>';
    }
}

function renderMetadataList(email, metadata) {
    metadataListEl.innerHTML = '';
    
    let metadataArray = [];
    if (Array.isArray(metadata)) {
        metadataArray = metadata;
    } else if (typeof metadata === 'object' && metadata !== null) {
        metadataArray = Object.keys(metadata).map(key => ({ title: key, value: metadata[key] }));
    }
    
    if (metadataArray.length === 0) {
        metadataListEl.innerHTML = '<p style="font-size: 12px; color: var(--tg-theme-hint-color); margin: 0;">No metadata linked.</p>';
        return;
    }
    
    metadataArray.forEach((item, index) => {
        const row = document.createElement('div');
        row.style = 'display: flex; justify-content: space-between; align-items: flex-start; padding: 6px 8px; background: rgba(255,255,255,0.02); border-radius: 6px; font-size: 12px; border: 1px solid rgba(255,255,255,0.04); margin-bottom: 4px; box-sizing: border-box;';
        
        const content = document.createElement('div');
        content.style = 'display: flex; flex-direction: column; gap: 2px; flex-grow: 1; margin-right: 8px; word-break: break-all;';
        
        const titleSpan = document.createElement('span');
        titleSpan.style = 'font-weight: 500; color: var(--tg-theme-hint-color);';
        titleSpan.textContent = item.title;
        content.appendChild(titleSpan);
        
        const valSpan = document.createElement('span');
        valSpan.style = 'color: var(--tg-theme-text-color);';
        valSpan.textContent = item.value;
        content.appendChild(valSpan);
        
        row.appendChild(content);
        
        const actions = document.createElement('div');
        actions.style = 'display: flex; gap: 6px; align-self: center;';
        
        const editBtn = document.createElement('button');
        editBtn.className = 'action-icon-btn';
        editBtn.innerHTML = '✏️';
        editBtn.style = 'padding: 2px; font-size: 11px; cursor: pointer;';
        editBtn.onclick = () => openMetadataDialog(email, metadataArray, index);
        actions.appendChild(editBtn);
        
        const delBtn = document.createElement('button');
        delBtn.className = 'action-icon-btn';
        delBtn.innerHTML = '🗑️';
        delBtn.style = 'padding: 2px; font-size: 11px; cursor: pointer;';
        delBtn.onclick = () => deleteMetadataItem(email, metadataArray, index);
        actions.appendChild(delBtn);
        
        row.appendChild(actions);
        metadataListEl.appendChild(row);
    });
}

async function saveMetadata(email, metadataArray) {
    try {
        const response = await fetch('/api/admin/metadata', {
            method: 'PUT',
            headers: getHeaders(),
            body: JSON.stringify({ email, metadata: metadataArray })
        });
        if (response.ok) {
            const req = allRequests.find(r => r.registered_email === email);
            if (req) {
                req.metadata = JSON.stringify(metadataArray);
            }
            renderMetadataList(email, metadataArray);
            
            // Reload list to fetch any background Telegram ID resolution
            if (activeTab === 'users') {
                await fetchDirectory();
            } else {
                await fetchRequests();
            }
            
            // Refresh drawer Telegram detail element
            const currentList = activeTab === 'users' ? allDirectoryUsers : allRequests;
            const updatedUser = currentList.find(r => r.registered_email === email);
            if (updatedUser) {
                const tgUsername = updatedUser.telegram_username ? `@${updatedUser.telegram_username}` : 'No Telegram account';
                const tgIdStr = updatedUser.telegram_id ? ` (ID: ${updatedUser.telegram_id})` : '';
                detailTelegramEl.textContent = `${tgUsername}${tgIdStr}`;
            }
        } else {
            alert("Failed to save metadata updates.");
        }
    } catch (err) {
        console.error(err);
        alert("Error saving metadata.");
    }
}

function deleteMetadataItem(email, metadataArray, index) {
    if (confirm(`Remove metadata field "${metadataArray[index].title}"?`)) {
        const updated = [...metadataArray];
        updated.splice(index, 1);
        saveMetadata(email, updated);
    }
}

function openMetadataDialog(email, metadataArray, editIndex = -1) {
    const isEdit = editIndex !== -1;
    dialogTitle.textContent = isEdit ? 'Edit Metadata' : 'Add Metadata';
    
    dialogBody.innerHTML = `
        <div class="form-group" style="display: flex; flex-direction: column; gap: 4px; width: 100%; box-sizing: border-box;">
            <label style="font-size: 12px; font-weight: 500; color: var(--tg-theme-hint-color);">Key / Field Name</label>
            <input type="text" id="dialog-meta-key" placeholder="e.g. Telegram Username" style="padding: 10px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.1); background: var(--tg-theme-secondary-bg-color); color: var(--tg-theme-text-color); outline: none;" value="${isEdit ? escapeHtml(metadataArray[editIndex].title) : ''}" ${isEdit ? 'disabled' : ''}>
        </div>
        <div class="form-group" style="display: flex; flex-direction: column; gap: 4px; width: 100%; box-sizing: border-box;">
            <label style="font-size: 12px; font-weight: 500; color: var(--tg-theme-hint-color);">Value</label>
            <input type="text" id="dialog-meta-value" placeholder="e.g. @username" style="padding: 10px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.1); background: var(--tg-theme-secondary-bg-color); color: var(--tg-theme-text-color); outline: none;" value="${isEdit ? escapeHtml(metadataArray[editIndex].value) : ''}">
        </div>
    `;
    
    openDialog();
    
    dialogSaveBtn.onclick = () => {
        const keyInput = document.getElementById('dialog-meta-key');
        const valInput = document.getElementById('dialog-meta-value');
        const key = keyInput.value.trim();
        const val = valInput.value.trim();
        
        if (!key || !val) {
            alert("Both Key and Value are required.");
            return;
        }
        
        const updated = [...metadataArray];
        if (isEdit) {
            updated[editIndex].value = val;
        } else {
            if (updated.some(item => item.title.toLowerCase() === key.toLowerCase())) {
                alert(`Field "${key}" already exists. Edit the existing one instead.`);
                return;
            }
            updated.push({ title: key, value: val });
        }
        
        saveMetadata(email, updated);
        closeDialog();
    };
}

async function deleteHistoryEntry(email, id) {
    if (confirm("Are you sure you want to delete this historical submission entry? This will not affect the user's Zoom status.")) {
        try {
            const response = await fetch(`/api/admin/history/${id}`, {
                method: 'DELETE',
                headers: getHeaders()
            });
            if (response.ok) {
                const req = allRequests.find(r => r.registered_email === email);
                if (req) await fetchHistoryList(req);
            } else {
                alert("Failed to delete history entry.");
            }
        } catch (err) {
            console.error(err);
            alert("Error deleting history.");
        }
    }
}

function openHistoryDialog(email, item = null) {
    const isEdit = item !== null;
    dialogTitle.textContent = isEdit ? 'Edit Submission Entry' : 'Add Submission Entry';
    
    const timestampStr = isEdit ? item.action_timestamp : new Date().toISOString();
    
    dialogBody.innerHTML = `
        <div class="form-group" style="display: flex; flex-direction: column; gap: 4px; width: 100%; box-sizing: border-box;">
            <label style="font-size: 12px; font-weight: 500; color: var(--tg-theme-hint-color);">Meeting ID</label>
            <input type="text" id="dialog-hist-meeting" placeholder="e.g. 89456729013" style="padding: 10px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.1); background: var(--tg-theme-secondary-bg-color); color: var(--tg-theme-text-color); outline: none;" value="${isEdit ? escapeHtml(item.meeting_id) : ''}">
        </div>
        <div class="form-group" style="display: flex; flex-direction: column; gap: 4px; width: 100%; box-sizing: border-box;">
            <label style="font-size: 12px; font-weight: 500; color: var(--tg-theme-hint-color);">Zoom Name</label>
            <input type="text" id="dialog-hist-name" placeholder="e.g. John Doe" style="padding: 10px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.1); background: var(--tg-theme-secondary-bg-color); color: var(--tg-theme-text-color); outline: none;" value="${isEdit ? escapeHtml(item.submitted_zoom_name) : ''}">
        </div>
        <div class="form-group" style="display: flex; flex-direction: column; gap: 4px; width: 100%; box-sizing: border-box;">
            <label style="font-size: 12px; font-weight: 500; color: var(--tg-theme-hint-color);">Telegram Username</label>
            <input type="text" id="dialog-hist-tg" placeholder="e.g. username" style="padding: 10px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.1); background: var(--tg-theme-secondary-bg-color); color: var(--tg-theme-text-color); outline: none;" value="${isEdit ? escapeHtml(item.submitted_telegram_username) : 'Unknown'}">
        </div>
        <div class="form-group" style="display: flex; flex-direction: column; gap: 4px; width: 100%; box-sizing: border-box;">
            <label style="font-size: 12px; font-weight: 500; color: var(--tg-theme-hint-color);">Action Taken / Status</label>
            <select id="dialog-hist-action" style="padding: 10px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.1); background: var(--tg-theme-secondary-bg-color); color: var(--tg-theme-text-color); outline: none;">
                <option value="Approved" ${isEdit && item.action_taken === 'Approved' ? 'selected' : ''}>Approved</option>
                <option value="Denied" ${isEdit && item.action_taken === 'Denied' ? 'selected' : ''}>Denied</option>
                <option value="Pending" ${isEdit && item.action_taken === 'Pending' ? 'selected' : ''}>Pending</option>
            </select>
        </div>
        <div class="form-group" style="display: flex; flex-direction: column; gap: 4px; width: 100%; box-sizing: border-box;">
            <label style="font-size: 12px; font-weight: 500; color: var(--tg-theme-hint-color);">Timestamp (ISO/UTC)</label>
            <input type="text" id="dialog-hist-time" style="padding: 10px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.1); background: var(--tg-theme-secondary-bg-color); color: var(--tg-theme-text-color); outline: none;" value="${escapeHtml(timestampStr)}">
        </div>
    `;
    
    openDialog();
    
    dialogSaveBtn.onclick = async () => {
        const meeting = document.getElementById('dialog-hist-meeting').value.trim();
        const zoomName = document.getElementById('dialog-hist-name').value.trim();
        const tgName = document.getElementById('dialog-hist-tg').value.trim();
        const actionVal = document.getElementById('dialog-hist-action').value;
        const timeVal = document.getElementById('dialog-hist-time').value.trim();
        
        if (!meeting || !zoomName || !tgName || !timeVal) {
            alert("All fields are required.");
            return;
        }
        
        const payload = {
            submitted_zoom_name: zoomName,
            submitted_telegram_username: tgName,
            meeting_id: meeting,
            action_taken: actionVal,
            action_timestamp: timeVal
        };
        
        try {
            let response;
            if (isEdit) {
                payload.id = item.id;
                response = await fetch('/api/admin/history', {
                    method: 'PUT',
                    headers: getHeaders(),
                    body: JSON.stringify(payload)
                });
            } else {
                payload.email = email;
                response = await fetch('/api/admin/history', {
                    method: 'POST',
                    headers: getHeaders(),
                    body: JSON.stringify(payload)
                });
            }
            
            if (response.ok) {
                const req = allRequests.find(r => r.registered_email === email);
                if (req) await fetchHistoryList(req);
                closeDialog();
            } else {
                alert("Failed to save history entry.");
            }
        } catch (err) {
            console.error(err);
            alert("Error saving history entry.");
        }
    };
}

function openDialog() {
    dialogOverlay.classList.add('active');
    dialog.classList.add('active');
}

function closeDialog() {
    dialogOverlay.classList.remove('active');
    dialog.classList.remove('active');
}

function escapeHtml(str) {
    if (!str) return '';
    return str
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
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

let searchDebounceTimeout = null;
searchInputEl.oninput = (e) => {
    searchQuery = e.target.value.trim();
    clearTimeout(searchDebounceTimeout);
    searchDebounceTimeout = setTimeout(() => {
        fetchRequests();
    }, 300);
};

selectAllCheckbox.onchange = (e) => {
    toggleSelectAll(e.target.checked);
};

// Drawer controls
drawerCloseBtn.onclick = closeDrawer;
drawerOverlay.onclick = closeDrawer;
saveNotesBtn.onclick = saveNotes;

// Dialog controls
dialogCloseBtn.onclick = closeDialog;
dialogCancelBtn.onclick = closeDialog;
dialogOverlay.onclick = closeDialog;

// Sync Zoom Control
syncZoomBtn.onclick = async () => {
    if (syncZoomBtn.disabled) return;
    try {
        setSyncLoading(true);
        const response = await fetch('/api/admin/sync', {
            method: 'POST',
            headers: getHeaders()
        });
        const result = await response.json();
        
        if (response.ok) {
            alert(result.message || "Zoom sync completed successfully!");
            await fetchStats();
            await fetchRequests();
        } else {
            alert(`Sync Failed: ${result.detail || "Unknown error"}`);
        }
    } catch (err) {
        console.error(err);
        alert("Error syncing from Zoom API.");
    } finally {
        setSyncLoading(false);
    }
};

function setSyncLoading(isLoading) {
    if (isLoading) {
        syncZoomBtn.disabled = true;
        syncBtnText.textContent = 'Syncing...';
        syncZoomBtn.style.opacity = '0.6';
        tg?.showProgress && tg.showProgress();
    } else {
        syncZoomBtn.disabled = false;
        syncBtnText.textContent = 'Sync Zoom';
        syncZoomBtn.style.opacity = '1';
        tg?.closeProgress && tg.closeProgress();
    }
}

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

// Tab Switching Navigation Logic
document.querySelectorAll('.bottom-nav .nav-item').forEach(item => {
    item.onclick = async () => {
        const targetTab = item.dataset.tab;
        if (targetTab === activeTab) return;
        
        // Update Active Nav Tab UI
        document.querySelector('.bottom-nav .nav-item.active').classList.remove('active');
        item.classList.add('active');
        
        // Toggle Visible Tab Sections
        document.querySelectorAll('.tab-pane').forEach(pane => pane.classList.add('hidden'));
        document.getElementById(`tab-${targetTab}`).classList.remove('hidden');
        
        activeTab = targetTab;
        
        // Telegram Haptic Feedback
        tg?.HapticFeedback?.impactOccurred('light');
        
        // Lazy Load data depending on tab
        if (activeTab === 'queue') {
            await fetchStats();
            await fetchRequests();
        } else if (activeTab === 'users') {
            await fetchDirectory();
        } else if (activeTab === 'tools') {
            await fetchSettings();
            await fetchAdminTeam();
            await fetchStats();
        }
    };
});

// Users Tab fuzzy search debounce
let directorySearchTimeout = null;
globalSearchInputEl.oninput = (e) => {
    directorySearchQuery = e.target.value.trim();
    clearTimeout(directorySearchTimeout);
    directorySearchTimeout = setTimeout(() => {
        fetchDirectory();
    }, 300);
};

// Export CSV handler
exportCsvBtn.onclick = () => {
    exportToCSV();
};

// Settings CRUD handlers
async function fetchSettings() {
    try {
        const response = await fetch('/api/admin/settings', { headers: getHeaders() });
        if (response.ok) {
            const data = await response.json();
            zoomMeetingIdInput.value = data.zoom_meeting_id || '';
            zoomAccountIdInput.value = data.zoom_account_id || '';
            zoomClientIdInput.value = data.zoom_client_id || '';
            zoomClientSecretInput.value = data.zoom_client_secret || '';
            zoomRegistrationLinkInput.value = data.zoom_registration_link || '';
            zoomSyncIntervalInput.value = data.zoom_sync_interval || '10 minutes';
        }
    } catch (err) {
        console.error("Failed to load settings:", err);
    }
}

saveSettingsBtn.onclick = async () => {
    if (saveSettingsBtn.disabled) return;
    try {
        saveSettingsBtn.disabled = true;
        saveSettingsBtn.textContent = 'Saving...';
        
        const payload = {
            zoom_meeting_id: zoomMeetingIdInput.value.trim(),
            zoom_account_id: zoomAccountIdInput.value.trim(),
            zoom_client_id: zoomClientIdInput.value.trim(),
            zoom_client_secret: zoomClientSecretInput.value.trim(),
            zoom_registration_link: zoomRegistrationLinkInput.value.trim(),
            zoom_sync_interval: zoomSyncIntervalInput.value.trim()
        };
        
        const response = await fetch('/api/admin/settings', {
            method: 'PUT',
            headers: getHeaders(),
            body: JSON.stringify(payload)
        });
        
        if (response.ok) {
            alert("Settings updated successfully!");
            tg?.HapticFeedback?.notificationOccurred('success');
            await fetchStats();
        } else {
            const err = await response.json();
            alert("Failed to save settings: " + (err.detail || "Unknown error"));
        }
    } catch (err) {
        console.error(err);
        alert("Error saving settings.");
    } finally {
        saveSettingsBtn.disabled = false;
        saveSettingsBtn.textContent = 'Save Credentials';
    }
};

async function fetchDirectory() {
    try {
        usersDirectoryListEl.innerHTML = `
            <div style="text-align: center; padding: 40px; color: var(--tg-theme-hint-color);">
                <div class="spinner" style="margin: 0 auto 10px;"></div>
                Searching directory...
            </div>
        `;
        let url = '/api/admin/requests?status_filter=All';
        if (directorySearchQuery) {
            url += `&search=${encodeURIComponent(directorySearchQuery)}`;
        }
        const response = await fetch(url, { headers: getHeaders() });
        if (!response.ok) throw new Error('Directory fetch failed');
        
        allDirectoryUsers = await response.json();
        renderFilteredDirectory();
    } catch (err) {
        console.error(err);
        usersDirectoryListEl.innerHTML = `
            <div style="text-align: center; padding: 40px; color: #ef4444;">
                ❌ Failed to load directory.
            </div>
        `;
    }
}

function renderFilteredDirectory() {
    const statusVal = directoryFilterStatus.value;
    const originVal = directoryFilterOrigin.value;
    
    let filtered = allDirectoryUsers;
    
    if (statusVal !== 'All') {
        filtered = filtered.filter(u => (u.global_status || 'Pending').toLowerCase() === statusVal.toLowerCase());
    }
    
    if (originVal !== 'All') {
        filtered = filtered.filter(u => {
            const hasTg = u.telegram_id && u.telegram_id !== 0;
            if (originVal === 'Linked') return hasTg;
            if (originVal === 'Zoom') return !hasTg;
            return true;
        });
    }
    
    renderRequestsList(filtered, usersDirectoryListEl, true);
}

// Bind Advanced Directory Filter Select Handlers
directoryFilterStatus.onchange = renderFilteredDirectory;
directoryFilterOrigin.onchange = renderFilteredDirectory;

// Sync Interval Save handler
saveIntervalBtn.onclick = async () => {
    if (saveIntervalBtn.disabled) return;
    try {
        saveIntervalBtn.disabled = true;
        saveIntervalBtn.textContent = 'Saving...';
        
        const getRes = await fetch('/api/admin/settings', { headers: getHeaders() });
        if (!getRes.ok) throw new Error("Failed to load existing settings");
        const current = await getRes.json();
        
        const payload = {
            zoom_meeting_id: current.zoom_meeting_id || '',
            zoom_account_id: current.zoom_account_id || '',
            zoom_client_id: current.zoom_client_id || '',
            zoom_client_secret: current.zoom_client_secret || '',
            zoom_registration_link: current.zoom_registration_link || '',
            zoom_sync_interval: zoomSyncIntervalInput.value.trim()
        };
        
        const response = await fetch('/api/admin/settings', {
            method: 'PUT',
            headers: getHeaders(),
            body: JSON.stringify(payload)
        });
        
        if (response.ok) {
            alert("Synchronization interval updated successfully!");
            tg?.HapticFeedback?.notificationOccurred('success');
        } else {
            const err = await response.json();
            alert("Failed to save sync interval: " + (err.detail || "Unknown error"));
        }
    } catch (err) {
        console.error(err);
        alert("Error saving sync interval.");
    } finally {
        saveIntervalBtn.disabled = false;
        saveIntervalBtn.textContent = 'Save';
    }
};

// Admin Team CRUD handlers
async function fetchAdminTeam() {
    try {
        adminTeamList.innerHTML = `
            <div style="text-align: center; padding: 10px; color: var(--tg-theme-hint-color);">
                <div class="spinner" style="width: 16px; height: 16px; margin: 0 auto 5px;"></div>
                Loading team...
            </div>
        `;
        const response = await fetch('/api/admin/team', { headers: getHeaders() });
        if (!response.ok) throw new Error("Failed to load admin team list");
        const team = await response.json();
        renderAdminTeam(team);
    } catch (err) {
        console.error(err);
        adminTeamList.innerHTML = `<div style="color: #ef4444; font-size: 12px; text-align: center;">❌ Failed to load team list</div>`;
    }
}

function renderAdminTeam(teamArray) {
    adminTeamList.innerHTML = '';
    teamArray.forEach(admin => {
        const row = document.createElement('div');
        row.style = 'display: flex; align-items: center; justify-content: space-between; padding: 8px 12px; background: rgba(255,255,255,0.03); border-radius: 8px; font-size: 13px; margin-bottom: 6px; box-sizing: border-box;';
        
        const details = document.createElement('div');
        details.style = 'display: flex; flex-direction: column; gap: 2px;';
        
        const nameSpan = document.createElement('span');
        nameSpan.style = 'font-weight: 500; color: var(--tg-theme-text-color); text-align: left;';
        nameSpan.textContent = admin.username + (admin.is_owner ? ' 👑' : '');
        
        const idSpan = document.createElement('span');
        idSpan.style = 'font-size: 10px; color: var(--tg-theme-hint-color); text-align: left;';
        idSpan.textContent = `ID: ${admin.telegram_id}`;
        
        details.appendChild(nameSpan);
        details.appendChild(idSpan);
        row.appendChild(details);
        
        if (!admin.is_owner) {
            const revokeBtn = document.createElement('button');
            revokeBtn.className = 'btn btn-secondary';
            revokeBtn.style = 'margin: 0; padding: 4px 8px; font-size: 11px; height: auto; line-height: normal; background: rgba(239,68,68,0.15); color: #ef4444; border: 1px solid rgba(239,68,68,0.25); width: auto;';
            revokeBtn.textContent = 'Revoke';
            revokeBtn.onclick = async () => {
                if (confirm(`Are you sure you want to revoke admin permissions from ${admin.username}?`)) {
                    try {
                        const res = await fetch(`/api/admin/team/${admin.telegram_id}`, {
                            method: 'DELETE',
                            headers: getHeaders()
                        });
                        if (res.ok) {
                            alert("Administrator revoked successfully!");
                            fetchAdminTeam();
                        } else {
                            const err = await res.json();
                            alert("Failed: " + (err.detail || "Unknown error"));
                        }
                    } catch (e) {
                        console.error(e);
                        alert("Error revoking admin.");
                    }
                }
            };
            row.appendChild(revokeBtn);
        }
        adminTeamList.appendChild(row);
    });
}

addAdminBtn.onclick = async () => {
    const tgId = parseInt(addAdminIdInput.value.trim());
    const username = addAdminUsernameInput.value.trim();
    if (isNaN(tgId)) {
        alert("Please enter a valid numeric Telegram User ID.");
        return;
    }
    if (addAdminBtn.disabled) return;
    try {
        addAdminBtn.disabled = true;
        addAdminBtn.textContent = 'Adding...';
        
        const response = await fetch('/api/admin/team', {
            method: 'POST',
            headers: getHeaders(),
            body: JSON.stringify({ telegram_id: tgId, username: username || null })
        });
        
        if (response.ok) {
            alert("Administrator added successfully!");
            addAdminIdInput.value = '';
            addAdminUsernameInput.value = '';
            fetchAdminTeam();
        } else {
            const err = await response.json();
            alert("Failed: " + (err.detail || "Unknown error"));
        }
    } catch (err) {
        console.error(err);
        alert("Error adding administrator.");
    } finally {
        addAdminBtn.disabled = false;
        addAdminBtn.textContent = '👤 Add Administrator';
    }
};

tgResolveBtn.onclick = async () => {
    const query = tgResolveQueryInput.value.trim();
    if (!query) {
        alert("Please enter a username or numeric ID to resolve.");
        return;
    }
    if (tgResolveBtn.disabled) return;
    try {
        tgResolveBtn.disabled = true;
        tgResolveBtn.textContent = 'Resolving...';
        tgResolveResult.classList.add('hidden');
        
        const response = await fetch('/api/admin/resolve-telegram', {
            method: 'POST',
            headers: getHeaders(),
            body: JSON.stringify({ query: query })
        });
        
        if (response.ok) {
            const data = await response.json();
            const user = data.resolved;
            tgResolveResult.innerHTML = `
                <div style="color: #4ade80; font-weight: 600; margin-bottom: 4px;">✅ Entity Resolved Successfully</div>
                <div style="text-align: left;">👤 <b>Name:</b> ${escapeHtml(user.name)}</div>
                <div style="text-align: left;">💬 <b>Username:</b> @${escapeHtml(user.username || 'None')}</div>
                <div style="text-align: left;">🆔 <b>Telegram ID:</b> <code>${user.telegram_id}</code></div>
            `;
            tgResolveResult.classList.remove('hidden');
            tg?.HapticFeedback?.notificationOccurred('success');
        } else {
            const err = await response.json();
            tgResolveResult.innerHTML = `
                <div style="color: #ef4444; font-weight: 600; margin-bottom: 2px;">❌ Resolution Failed</div>
                <div style="color: var(--tg-theme-hint-color); font-size: 11px; text-align: left;">${escapeHtml(err.detail || "Entity not found")}</div>
            `;
            tgResolveResult.classList.remove('hidden');
            tg?.HapticFeedback?.notificationOccurred('error');
        }
    } catch (err) {
        console.error(err);
        alert("Error resolving Telegram entity.");
    } finally {
        tgResolveBtn.disabled = false;
        tgResolveBtn.textContent = 'Resolve';
    }
};

function exportToCSV() {
    const dataToExport = activeTab === 'users' ? allDirectoryUsers : allRequests;
    if (dataToExport.length === 0) {
        alert("No data available to export.");
        return;
    }
    
    // Header row
    const headers = ['Email', 'Telegram ID', 'Global Status', 'Registration Date', 'Country', 'Zoom Name', 'Telegram Username', 'Behavior Notes'];
    const rows = [headers];
    
    dataToExport.forEach(user => {
        rows.push([
            user.registered_email || '',
            user.telegram_id || '',
            user.global_status || '',
            user.created_at || '',
            user.country || '',
            user.zoom_name || '',
            user.telegram_username || '',
            (user.behavior_notes || '').replace(/"/g, '""') // escape quotes
        ]);
    });
    
    const csvContent = "data:text/csv;charset=utf-8," 
        + rows.map(e => e.map(val => `"${val}"`).join(",")).join("\n");
        
    const encodedUri = encodeURI(csvContent);
    const link = document.createElement("a");
    link.setAttribute("href", encodedUri);
    const filename = `registrants_export_${activeTab}_${new Date().toISOString().split('T')[0]}.csv`;
    link.setAttribute("download", filename);
    document.body.appendChild(link);
    
    link.click();
    document.body.removeChild(link);
    
    tg?.HapticFeedback?.notificationOccurred('success');
}

// Initial Execution
fetchStats();
fetchRequests();
