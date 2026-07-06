// Initialize Telegram WebApp SDK
const tg = window.Telegram.WebApp;

// Expand the app to take up full available height in Telegram
tg.expand();

// Theme variables (Telegram provides these automatically)
document.documentElement.style.setProperty('--tg-theme-bg-color', tg.themeParams.bg_color || '#ffffff');
document.documentElement.style.setProperty('--tg-theme-secondary-bg-color', tg.themeParams.secondary_bg_color || '#f4f4f5');
document.documentElement.style.setProperty('--tg-theme-text-color', tg.themeParams.text_color || '#1f2937');
document.documentElement.style.setProperty('--tg-theme-hint-color', tg.themeParams.hint_color || '#9ca3af');
document.documentElement.style.setProperty('--tg-theme-link-color', tg.themeParams.link_color || '#2481cc');
document.documentElement.style.setProperty('--tg-theme-button-color', tg.themeParams.button_color || '#2481cc');
document.documentElement.style.setProperty('--tg-theme-button-text-color', tg.themeParams.button_text_color || '#ffffff');

// Dom Elements
const routerLoadingEl = document.getElementById('router-loading');
const routerBlockedEl = document.getElementById('router-blocked');
const routerWelcomeEl = document.getElementById('router-welcome');
const routerPendingEl = document.getElementById('router-pending');
const welcomeNameEl = document.getElementById('welcome-name');
const pendingNameEl = document.getElementById('pending-name');
const joinBtnLink = document.getElementById('join-btn-link');

const errorEl = document.getElementById('error-container');
const errorMsgEl = document.getElementById('error-message');
const retryBtn = document.getElementById('retry-btn');
const formEl = document.getElementById('registration-form');
const standardQuestionsContainer = document.getElementById('standard-questions-container');
const customQuestionsContainer = document.getElementById('custom-questions-container');
const submitBtn = document.getElementById('submit-btn');
const submitBtnText = submitBtn.querySelector('.btn-text');
const submitBtnSpinner = submitBtn.querySelector('.btn-spinner');
const successOverlay = document.getElementById('success-overlay');
const closeBtn = document.getElementById('close-btn');

const standardLabels = {
    'last_name': 'Last Name',
    'address': 'Address',
    'city': 'City',
    'state': 'State/Province',
    'zip': 'Zip/Postal Code',
    'country': 'Country',
    'phone': 'Phone Number',
    'industry': 'Industry',
    'org': 'Organization/Company',
    'job_title': 'Job Title',
    'purchasing_time_frame': 'Purchasing Time Frame',
    'role_in_purchase_decision': 'Role in Purchase Decision',
    'no_of_employees': 'Number of Employees',
    'comments': 'Comments/Questions'
};

let zoomQuestionsData = null;

function getHeaders() {
    const headers = { 'Content-Type': 'application/json' };
    if (tg?.initData) {
        headers['Authorization'] = tg.initData;
    } else {
        headers['Authorization'] = 'MOCK_TOKEN';
    }
    return headers;
}

// Fetch Zoom registration questions on load
async function loadQuestions() {
    showLoading();
    try {
        const response = await fetch('/api/questions');
        if (!response.ok) {
            throw new Error(`Server returned HTTP ${response.status}`);
        }
        zoomQuestionsData = await response.json();
        renderStandardQuestions();
        renderCustomQuestions();
        showForm();
    } catch (error) {
        console.error('Failed to load questions:', error);
        showError(`Failed to load registration form questions. Please check your network and try again.`);
    }
}

function showLoading() {
    hideAllGatewayStates();
    routerLoadingEl.classList.remove('hidden');
}

function showForm() {
    hideAllGatewayStates();
    formEl.classList.remove('hidden');
}

function showError(msg) {
    hideAllGatewayStates();
    errorEl.classList.remove('hidden');
    errorMsgEl.textContent = msg;
}

function hideAllGatewayStates() {
    routerLoadingEl.classList.add('hidden');
    routerBlockedEl.classList.add('hidden');
    routerWelcomeEl.classList.add('hidden');
    routerPendingEl.classList.add('hidden');
    errorEl.classList.add('hidden');
    formEl.classList.add('hidden');
}

// Dynamically render custom questions fetched from Zoom API
function renderCustomQuestions() {
    customQuestionsContainer.innerHTML = '';
    
    if (!zoomQuestionsData || !zoomQuestionsData.custom_questions) {
        return;
    }
    
    zoomQuestionsData.custom_questions.forEach((q, idx) => {
        const formGroup = document.createElement('div');
        formGroup.className = 'form-group';
        
        const label = document.createElement('label');
        label.innerHTML = `${escapeHtml(q.title)} ${q.required ? '<span class="required">*</span>' : ''}`;
        formGroup.appendChild(label);
        
        let inputField;
        
        // If question has predefined answers, render a dropdown select list
        if (q.answers && q.answers.length > 0) {
            inputField = document.createElement('select');
            inputField.name = `custom_question_${idx}`;
            inputField.dataset.title = q.title;
            if (q.required) inputField.required = true;
            
            // Add placeholder option
            const placeholderOpt = document.createElement('option');
            placeholderOpt.value = '';
            placeholderOpt.textContent = '-- Select an Option --';
            placeholderOpt.disabled = true;
            placeholderOpt.selected = true;
            inputField.appendChild(placeholderOpt);
            
            q.answers.forEach(answer => {
                const opt = document.createElement('option');
                opt.value = answer;
                opt.textContent = answer;
                inputField.appendChild(opt);
            });
        } else {
            // Otherwise, render a text box
            inputField = document.createElement('input');
            inputField.type = 'text';
            inputField.name = `custom_question_${idx}`;
            inputField.dataset.title = q.title;
            inputField.placeholder = 'Your answer...';
            if (q.required) inputField.required = true;
        }
        
        formGroup.appendChild(inputField);
        customQuestionsContainer.appendChild(formGroup);
    });
}

// Dynamically render standard questions required by Zoom (e.g. Country)
function renderStandardQuestions() {
    standardQuestionsContainer.innerHTML = '';
    
    if (!zoomQuestionsData || !zoomQuestionsData.questions) {
        return;
    }
    
    zoomQuestionsData.questions.forEach((q) => {
        const fieldName = q.field_name;
        // Skip fields that are already hardcoded in index.html
        if (fieldName === 'first_name' || fieldName === 'last_name' || fieldName === 'email') {
            return;
        }
        
        const formGroup = document.createElement('div');
        formGroup.className = 'form-group';
        
        const label = document.createElement('label');
        const labelText = standardLabels[fieldName] || fieldName;
        label.innerHTML = `${escapeHtml(labelText)} ${q.required ? '<span class="required">*</span>' : ''}`;
        formGroup.appendChild(label);
        
        let inputField;
        
        // Render country selector as a dropdown for better UX
        if (fieldName === 'country') {
            inputField = document.createElement('select');
            inputField.name = `std_field_${fieldName}`;
            inputField.dataset.standardField = fieldName;
            if (q.required) inputField.required = true;
            
            const placeholderOpt = document.createElement('option');
            placeholderOpt.value = '';
            placeholderOpt.textContent = '-- Select Country --';
            placeholderOpt.disabled = true;
            placeholderOpt.selected = true;
            inputField.appendChild(placeholderOpt);
            
            const countries = [
                { code: 'TH', name: 'Thailand' },
                { code: 'MY', name: 'Malaysia' },
                { code: 'SG', name: 'Singapore' },
                { code: 'ID', name: 'Indonesia' },
                { code: 'VN', name: 'Vietnam' },
                { code: 'PH', name: 'Philippines' },
                { code: 'TW', name: 'Taiwan' },
                { code: 'HK', name: 'Hong Kong' },
                { code: 'CN', name: 'China' },
                { code: 'JP', name: 'Japan' },
                { code: 'KR', name: 'South Korea' },
                { code: 'AU', name: 'Australia' },
                { code: 'US', name: 'United States' },
                { code: 'GB', name: 'United Kingdom' },
                { code: 'CA', name: 'Canada' },
                { code: 'IN', name: 'India' }
            ];
            
            countries.forEach(c => {
                const opt = document.createElement('option');
                opt.value = c.code;
                opt.textContent = c.name;
                inputField.appendChild(opt);
            });
        } else {
            inputField = document.createElement('input');
            inputField.type = 'text';
            inputField.name = `std_field_${fieldName}`;
            inputField.dataset.standardField = fieldName;
            inputField.placeholder = `Enter your ${labelText.toLowerCase()}...`;
            if (q.required) inputField.required = true;
        }
        
        formGroup.appendChild(inputField);
        standardQuestionsContainer.appendChild(formGroup);
    });
}

// Form submission handler
formEl.addEventListener('submit', async (e) => {
    e.preventDefault();
    
    // Set button to loading state
    setSubmitLoading(true);
    
    const formData = new FormData(formEl);
    const firstName = formData.get('first_name').trim();
    const lastName = formData.get('last_name').trim();
    const email = formData.get('email').trim().toLowerCase();
    
    // Gather Standard Fields responses
    const standardFieldsPayload = {};
    const standardInputs = standardQuestionsContainer.querySelectorAll('input, select');
    standardInputs.forEach(input => {
        if (input.value.trim()) {
            standardFieldsPayload[input.dataset.standardField] = input.value.trim();
        }
    });
    
    // Gather Custom Questions responses
    const customQuestionsPayload = [];
    const customInputs = customQuestionsContainer.querySelectorAll('input, select');
    customInputs.forEach(input => {
        customQuestionsPayload.push({
            title: input.dataset.title,
            value: input.value.trim()
        });
    });
    
    // Secure Telegram authentication data string
    const initData = tg.initData || ''; 
    
    // Check if running inside Telegram
    if (!initData) {
        // Fallback for browser testing (if not in Telegram WebApp wrapper)
        alert('Warning: Operating outside Telegram. initData token is empty. This submission will fail verification on production.');
    }
    
    const payload = {
        initData: initData,
        first_name: firstName,
        last_name: lastName,
        email: email,
        custom_questions: customQuestionsPayload,
        standard_fields: standardFieldsPayload
    };
    
    try {
        const response = await fetch('/api/register', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(payload)
        });
        
        const resData = await response.json();
        
        if (!response.ok) {
            throw new Error(resData.detail || `Server error: HTTP ${response.status}`);
        }
        
        // Success: Show Success Overlay
        successOverlay.classList.remove('hidden');
    } catch (error) {
        console.error('Registration failed:', error);
        alert(`Registration Error: ${error.message}`);
    } finally {
        setSubmitLoading(false);
    }
});

function setSubmitLoading(isLoading) {
    if (isLoading) {
        submitBtn.disabled = true;
        submitBtnText.textContent = 'Submitting...';
        submitBtnSpinner.classList.remove('hidden');
    } else {
        submitBtn.disabled = false;
        submitBtnText.textContent = 'Submit Registration';
        submitBtnSpinner.classList.add('hidden');
    }
}

// Close Mini App
closeBtn.addEventListener('click', () => {
    tg.close();
});

retryBtn.addEventListener('click', initGateway);

async function initGateway() {
    showGatewayLoading();
    try {
        const response = await fetch('/api/auth/verify', { headers: getHeaders() });
        if (!response.ok) {
            throw new Error(`Auth check returned HTTP ${response.status}`);
        }
        
        const data = await response.json();
        routeUser(data);
    } catch (error) {
        console.error('Gateway Error:', error);
        showGatewayError('Failed to verify session. Please check your network and try again.');
    }
}

function routeUser(data) {
    hideAllGatewayStates();
    
    if (data.role === 'admin') {
        window.location.href = 'admin.html';
        return;
    }
    
    if (data.role === 'blacklisted') {
        routerBlockedEl.classList.remove('hidden');
        return;
    }
    
    if (data.role === 'active_user') {
        welcomeNameEl.textContent = data.name || 'User';
        joinBtnLink.href = data.join_url || '#';
        routerWelcomeEl.classList.remove('hidden');
        tg.HapticFeedback?.notificationOccurred('success');
        return;
    }
    
    if (data.role === 'pending') {
        pendingNameEl.textContent = data.name || 'User';
        routerPendingEl.classList.remove('hidden');
        return;
    }
    
    // Guest/new user -> fetch dynamic form questions
    loadQuestions();
}

function showGatewayLoading() {
    hideAllGatewayStates();
    routerLoadingEl.classList.remove('hidden');
}

function showGatewayError(msg) {
    hideAllGatewayStates();
    errorEl.classList.remove('hidden');
    errorMsgEl.textContent = msg;
}

// Helper to escape HTML tags
function escapeHtml(str) {
    return str
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

// Run initial gateway router load
initGateway();
