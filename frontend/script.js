const API_URL = window.location.origin;
const LANGUAGE_KEY = "finance_tracker_language";
const THEME_KEY = "finance_tracker_theme";
const LOCAL_DATA_KEY = "finance_tracker_data_v2";
const EXPENSE_CATEGORIES = ["Еда", "Транспорт", "Дом", "Развлечения", "Здоровье", "Другое"];
const INCOME_CATEGORIES = ["Зарплата", "Фриланс", "Бизнес", "Инвестиции", "Другое"];
const LOCALES = { kk: "kk-KZ", en: "en-US", ru: "ru-RU" };
const VALID_THEMES = ["light", "dark"];

const CURRENCY_SYMBOLS = { KZT: "₸", USD: "$", EUR: "€", RUB: "₽" };
let currentCurrency = "KZT";

let editingOperationId = null;
// While an operation is edited: its stored date text and what the date field showed for it.
// If the field is left alone, the stored text is sent back untouched.
let editingOperationDate = null;
let editingBudgetId = null;
let currentPeriod = "all";
let currentDashboard = null;
// The signed-in user's own categories ({ id, kind, name }); the built-in ones are the constants above.
let userCategories = [];
let notificationTimer = null;

const i18n = window.FinanceTrackerI18n;
const $ = (id) => document.getElementById(id);

function getStoredLanguage() {
    const language = localStorage.getItem(LANGUAGE_KEY);
    return i18n.SUPPORTED_LANGUAGES.includes(language) ? language : i18n.DEFAULT_LANGUAGE;
}

function getStoredTheme() {
    const theme = localStorage.getItem(THEME_KEY);
    return VALID_THEMES.includes(theme) ? theme : "light";
}

function formatMoney(amount) {
    const symbol = CURRENCY_SYMBOLS[currentCurrency] || "₸";
    const formatted = new Intl.NumberFormat(LOCALES[i18n.getLanguage()]).format(Number(amount) || 0);
    return `${formatted} ${symbol}`;
}

function updateSymbol(symbol) {
    document.querySelectorAll(".currency-symbol").forEach((el) => {
        el.textContent = symbol;
    });
}

function formatDate(dateString) {
    const match = String(dateString).match(/^\d{2}\.\d{2}\.\d{4}(?:,?\s+)\d{2}:\d{2}(?::\d{2})?$/);
    if (!match) return dateString;
    const [datePart, timePart] = String(dateString).split(/,?\s+/);
    const [day, month, year] = datePart.split(".").map(Number);
    const [hours, minutes, seconds = 0] = timePart.split(":").map(Number);
    const date = new Date(year, month - 1, day, hours, minutes, seconds);
    return new Intl.DateTimeFormat(LOCALES[i18n.getLanguage()], { dateStyle: "medium", timeStyle: "short" }).format(date);
}

// The snapshot belongs to the account that saved it: another user of the same
// browser must never see it, so it is tagged with the email and checked on read.
function localData() {
    try {
        const stored = JSON.parse(localStorage.getItem(LOCAL_DATA_KEY));
        const owner = localStorage.getItem(USER_EMAIL_KEY);
        if (stored && owner && stored.owner === owner && stored.dashboard) return stored.dashboard;
    } catch (_) {
        // A broken snapshot is treated like a missing one.
    }
    clearLocalData();
    return null;
}

function saveLocalData(data) {
    const owner = localStorage.getItem(USER_EMAIL_KEY);
    if (!owner) return;
    try {
        localStorage.setItem(LOCAL_DATA_KEY, JSON.stringify({ owner, dashboard: data }));
    } catch (_) {
        // Storage can be full or disabled; the snapshot is only an offline fallback.
    }
}

function clearLocalData() {
    try {
        localStorage.removeItem(LOCAL_DATA_KEY);
    } catch (_) {
        // Nothing to clear when storage is disabled.
    }
}

function applyTheme(theme = getStoredTheme()) {
    const selectedTheme = VALID_THEMES.includes(theme) ? theme : "light";
    document.documentElement.dataset.theme = selectedTheme;
    document.documentElement.style.colorScheme = selectedTheme;
    localStorage.setItem(THEME_KEY, selectedTheme);
    const darkMode = selectedTheme === "dark";
    $("theme-icon").textContent = darkMode ? "☀️" : "🌙";
    $("theme-label").textContent = i18n.t(darkMode ? "lightTheme" : "darkTheme");
    $("theme-toggle").setAttribute("aria-pressed", String(darkMode));
}

function applyLanguage() {
    document.documentElement.lang = i18n.getLanguage();
    document.querySelectorAll("[data-i18n]").forEach((element) => {
        element.textContent = i18n.t(element.dataset.i18n);
    });
    document.querySelectorAll("[data-i18n-placeholder]").forEach((element) => {
        element.placeholder = i18n.t(element.dataset.i18nPlaceholder);
    });
    document.querySelectorAll("[data-i18n-aria-label]").forEach((element) => {
        element.setAttribute("aria-label", i18n.t(element.dataset.i18nAriaLabel));
    });
    document.querySelectorAll(".language-button").forEach((button) => {
        const active = button.dataset.language === i18n.getLanguage();
        button.classList.toggle("active", active);
        button.setAttribute("aria-pressed", String(active));
    });
    updateCategoryOptions($("operation-category")?.value);
    updateTypeFilterOptions();
    updateCategoryFilterOptions();
    renderMyCategories();
    renderUserBadge();
    updateAuthSubmitLabel();
    applyTheme(document.documentElement.dataset.theme || getStoredTheme());
    if (currentDashboard) renderDashboard(currentDashboard);
    refreshImportLanguage();
}

function updateCategoryOptions(selectedValue = "") {
    const select = $("operation-category");
    const type = getFormType();
    if (!select) return;
    const categories = categoryNames(type);
    const values = categories.includes(selectedValue) || !selectedValue ? categories : [selectedValue, ...categories];
    select.replaceChildren(...values.map((value) => new Option(i18n.translateCategory(value), value)));
    select.value = selectedValue && values.includes(selectedValue) ? selectedValue : categories[0];
}

function getFormType() {
    return document.querySelector(".type-tab.active")?.dataset.operationType || "Доход";
}

function setFormType(type) {
    document.querySelectorAll(".type-tab").forEach((button) => {
        button.classList.toggle("active", button.dataset.operationType === type);
    });
    updateCategoryOptions();
}

function updateTypeFilterOptions() {
    const select = $("operation-type-filter");
    if (!select) return;
    const selected = select.value;
    select.replaceChildren(
        new Option(i18n.t("allTypes"), ""),
        new Option(i18n.translateOperationType("Доход"), "Доход"),
        new Option(i18n.translateOperationType("Расход"), "Расход")
    );
    select.value = selected;
}

function updateCategoryFilterOptions() {
    const select = $("operation-category-filter");
    if (!select) return;
    const selected = select.value;
    const categories = [...new Set([...INCOME_CATEGORIES, ...EXPENSE_CATEGORIES, ...userCategories.map((category) => category.name)])];
    select.replaceChildren(
        new Option(i18n.t("allCategories"), ""),
        ...categories.map((value) => new Option(i18n.translateCategory(value), value))
    );
    select.value = selected;
}

const TOKEN_KEY = "finance_tracker_token";
const USER_EMAIL_KEY = "finance_tracker_email";
let authMode = "login";

// The sign-in dialog's submit button reads "Sign in" or "Create account" by mode, in the current language.
function updateAuthSubmitLabel() {
    if ($("auth-submit-btn")) $("auth-submit-btn").textContent = i18n.t(authMode === "login" ? "signIn" : "createAccount");
}

function showAuthModal() {
    $("auth-modal").hidden = false;
    $("auth-email").focus();
}

function hideAuthModal() {
    $("auth-modal").hidden = true;
    $("auth-error").textContent = "";
    $("auth-password").value = "";
}

function renderUserBadge() {
    const area = $("user-auth-area");
    const email = localStorage.getItem(USER_EMAIL_KEY);
    const token = localStorage.getItem(TOKEN_KEY);
    if (!area) return;
    if (token && email) {
        area.replaceChildren();
        const badge = document.createElement("div");
        badge.className = "user-badge";
        const label = document.createElement("span");
        label.textContent = `👤 ${email}`;
        const logoutButton = document.createElement("button");
        logoutButton.type = "button";
        logoutButton.id = "logout-btn";
        logoutButton.textContent = i18n.t("signOut");
        badge.append(label, logoutButton);
        area.appendChild(badge);
        logoutButton.onclick = () => {
            clearSession();
            renderUserBadge();
            showAuthModal();
        };
    } else {
        area.replaceChildren();
        const loginBtn = document.createElement("button");
        loginBtn.className = "secondary-button";
        loginBtn.style.padding = "6px 14px";
        loginBtn.textContent = i18n.t("signInOrRegister");
        loginBtn.onclick = () => showAuthModal();
        area.appendChild(loginBtn);
    }
}

// Incremented whenever the session ends, so responses to requests started by
// the previous account are dropped instead of being drawn for the next one.
let sessionGeneration = 0;

// The settings form may only be saved once it shows this account's settings.
let settingsReady = false;
const DEFAULT_SETTINGS = { language: "RU", theme: "light", currency: "KZT" };

function setSettingsReady(ready) {
    settingsReady = ready;
    if ($("save-settings-btn")) $("save-settings-btn").disabled = !ready;
}

function clearSession() {
    sessionGeneration += 1;
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_EMAIL_KEY);
    clearLocalData();
    currentDashboard = null;
    userCategories = [];
    currentCurrency = DEFAULT_SETTINGS.currency;
    updateSymbol(CURRENCY_SYMBOLS[currentCurrency]);
    setSettingsReady(false);
    clearDashboardView();
    refreshCategoryControls();  // the previous user's categories leave every list
}

// Removes the previous user's figures from the page, which stays visible
// behind the sign-in dialog.
function clearDashboardView() {
    ["operations-list", "period-summary-content", "categories-list", "calendar-grid",
        "budgets-list", "goals-list", "settings-email", "import-sample", "import-preview-table",
        "import-errors", "import-report-errors", "import-summary", "import-report", "import-preview-more",
        "my-categories-list"
    ].forEach((id) => $(id)?.replaceChildren());
    ["balance", "summary-balance", "total-income", "total-expense"].forEach((id) => {
        if ($(id)) $(id).textContent = formatMoney(0);
    });
    if ($("calendar-details")) $("calendar-details").textContent = i18n.t("noOperationsOnDate");
    ["expenses-chart", "trend-chart"].forEach((id) => {
        const canvas = $(id);
        const context = canvas && canvas.getContext ? canvas.getContext("2d") : null;
        if (context) context.clearRect(0, 0, canvas.width, canvas.height);
    });
    // Unsent input of the previous user: passwords, half-filled forms, the email
    // in the sign-in form.
    ["operation-search", "operation-type-filter", "operation-category-filter", "operation-amount",
        "current-password", "new-password", "delete-password", "budget-amount", "goal-title",
        "goal-target", "goal-deadline", "auth-email", "auth-password", "import-income-values", "import-expense-values",
        "new-category-name"
    ].forEach((id) => {
        if ($(id)) $(id).value = "";
    });
    if ($("goal-current")) $("goal-current").value = "0";
    if ($("auth-error")) $("auth-error").textContent = "";
    for (const [field, value] of Object.entries(DEFAULT_SETTINGS)) {
        if ($(`settings-${field}`)) $(`settings-${field}`).value = value;
    }
    if ($("settings-alerts-inapp")) $("settings-alerts-inapp").checked = true;
    editingOperationId = null;
    editingOperationDate = null;
    ["operation-form", "budget-form", "goal-form"].forEach((id) => $(id)?.classList.add("hidden"));
    // Column choices are built from the previous file's CSV headers.
    Object.values(IMPORT_COLUMN_FIELDS).forEach((id) => $(id)?.replaceChildren());
    if ($("import-modal")) {
        $("import-modal").hidden = true;
        setImportBusy(false);  // a request cut short by sign-out must not leave it locked
        if ($("import-error")) $("import-error").textContent = "";
        if ($("import-file")) $("import-file").value = "";
    }
    Object.assign(importState, { file: null, columns: [], sample: [], currency: "", preview: null, report: null, step: "file" });
}

// 401 answers that only say "the password typed into this form is wrong". The
// session itself is still valid, so they must not sign the user out.
const CREDENTIAL_ERRORS = ["Неверный текущий пароль", "Неверный пароль", "Неверный email или пароль"];

async function request(path, options = {}) {
    return (await requestResponse(path, options)).json();
}

// Like request(), but hands back the response itself, for files.
async function requestResponse(path, options = {}) {
    const generation = sessionGeneration;
    const token = localStorage.getItem(TOKEN_KEY);
    const headers = {
        "Content-Type": "application/json",
        ...(token ? { "Authorization": `Bearer ${token}` } : {}),
        ...(options.headers || {})
    };
    const response = await fetch(`${API_URL}${path}`, { ...options, headers });
    if (!response.ok) {
        let detail = "";
        try {
            detail = (await response.json()).detail || "";
        } catch (_) {
            // The status code still provides a localized fallback.
        }
        const error = new Error(detail || i18n.translateApiError("", response.status));
        error.status = response.status;
        error.detail = detail;
        if (response.status === 401 && !CREDENTIAL_ERRORS.includes(detail)) {
            // Missing, expired or revoked token. A late answer to a request of an
            // earlier session must not end the session that replaced it.
            error.sessionEnded = true;
            if (generation === sessionGeneration) {
                clearSession();
                renderUserBadge();
                showAuthModal();
            }
        }
        throw error;
    }
    return response;
}

// A second click while the request is still running would save the same
// operation, budget or goal twice (or fetch an export twice), so the button
// waits for the first one.
function oncePerClick(button, handler) {
    let running = false;
    return async (...args) => {
        if (running) return;
        running = true;
        button.disabled = true;
        try {
            return await handler(...args);
        } finally {
            running = false;
            button.disabled = false;
        }
    };
}

const EXPORT_EXTENSIONS = { csv: "csv", excel: "xlsx", pdf: "pdf" };

// A plain link cannot send the Authorization header, so the file is fetched
// with it and then saved from memory.
async function downloadExport(format) {
    const generation = sessionGeneration;
    const period = currentPeriod;
    try {
        const response = await requestResponse(`/export/${format}?period=${encodeURIComponent(period)}`);
        const blob = await response.blob();
        if (!isCurrentSession(generation)) return;
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = `finance_export_${period}.${EXPORT_EXTENSIONS[format]}`;
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
        if (isCurrentSession(generation)) showNotification(i18n.translateApiError(error.detail, error.status), "error");
    }
}

function isCurrentSession(generation) {
    return generation === sessionGeneration;
}

const ALERT_KEYS = { warning: "budgetAlertWarning", limit: "budgetAlertLimit", exceeded: "budgetAlertExceeded" };

// The toast for the budget alerts a save returned, in the interface language, as
// { text, type } or null. When the server sends `alerts` it has already applied the
// account's switch, so an empty list means nothing to show; only a server that
// predates `alerts` is asked for its single Russian `notification` sentence.
function budgetAlertNotice(response) {
    if (!response) return null;
    if (Array.isArray(response.alerts)) {
        const alerts = response.alerts.filter((alert) => ALERT_KEYS[alert.level]);
        if (alerts.length === 0) return null;
        const text = alerts.map((alert) => i18n.t(ALERT_KEYS[alert.level], {
            category: i18n.translateCategory(alert.category),
            percent: alert.percent,
            spent: formatMoney(alert.spent),
            amount: formatMoney(alert.amount)
        })).join(" • ");
        return { text, type: alerts.some((alert) => alert.level === "exceeded") ? "error" : "warning" };
    }
    return response.notification ? { text: response.notification, type: "warning" } : null;
}

function showNotification(message, type = "success") {
    const notification = $("notification");
    $("notification-message").textContent = message;
    notification.className = `notification ${type}`;
    notification.hidden = false;
    clearTimeout(notificationTimer);
    notificationTimer = setTimeout(() => { notification.hidden = true; }, 4200);
}

function getFilteredOperations(operations) {
    const type = $("operation-type-filter")?.value || "";
    const category = $("operation-category-filter")?.value || "";
    const query = ($("operation-search")?.value || "").trim().toLocaleLowerCase();
    return operations.filter((operation) => {
        const searchable = [operation.type, operation.category].filter(Boolean).join(" ").toLocaleLowerCase();
        return (!type || operation.type === type)
            && (!category || operation.category === category)
            && (!query || searchable.includes(query)
                || i18n.translateOperationType(operation.type).toLocaleLowerCase().includes(query)
                || i18n.translateCategory(operation.category || "").toLocaleLowerCase().includes(query));
    });
}

function renderCategories(categories) {
    const container = $("categories-list");
    container.replaceChildren();
    const entries = Object.entries(categories || {});
    if (!entries.length) {
        container.append(createState(i18n.t("noCategoryExpenses"), "empty-state"));
        return;
    }
    const total = entries.reduce((sum, [, amount]) => sum + Number(amount), 0) || 1;
    entries.forEach(([name, amount]) => {
        const item = document.createElement("div");
        item.className = "category-item";
        const nameElement = document.createElement("span");
        nameElement.className = "category-name";
        nameElement.textContent = i18n.translateCategory(name);
        const amountElement = document.createElement("span");
        amountElement.className = "category-amount";
        amountElement.textContent = `-${formatMoney(amount)} (${Math.round(Number(amount) / total * 100)}%)`;
        item.append(nameElement, amountElement);
        container.appendChild(item);
    });
}

function renderChart(categories) {
    const canvas = $("expenses-chart");
    if (!canvas || !canvas.getContext) return;
    const context = canvas.getContext("2d");
    context.clearRect(0, 0, canvas.width, canvas.height);
    const entries = Object.entries(categories || {});
    if (!entries.length) return;
    const styles = getComputedStyle(document.documentElement);
    const accent = styles.getPropertyValue("--accent").trim() || "#3b82f6";
    const text = styles.getPropertyValue("--text-secondary").trim() || "#64748b";
    const max = Math.max(...entries.map(([, value]) => Number(value)), 1);
    const width = canvas.width / entries.length;
    entries.forEach(([name, value], index) => {
        const height = Number(value) / max * 150;
        context.fillStyle = accent;
        context.fillRect(index * width + 12, 180 - height, Math.max(width - 24, 4), height);
        context.fillStyle = text;
        context.font = "12px sans-serif";
        context.fillText(i18n.translateCategory(name).slice(0, 12), index * width + 12, 198);
    });
}

function renderTrendChart(monthlyTrend) {
    const canvas = $("trend-chart");
    if (!canvas || !canvas.getContext) return;

    // Size the canvas before any drawing so the empty state and the chart share
    // the same dimensions. Setting width/height also clears the bitmap, so a
    // repeat call never stacks drawings on top of the previous one.
    const W = canvas.offsetWidth || 600;
    const H = 220;
    canvas.width = W;
    canvas.height = H;
    const ctx = canvas.getContext("2d");

    const entries = Object.entries(monthlyTrend || {}).sort(([a], [b]) => a.localeCompare(b));
    if (entries.length < 2) {
        const styles = getComputedStyle(document.documentElement);
        const text = styles.getPropertyValue("--text-secondary").trim() || "#64748b";
        ctx.fillStyle = text;
        ctx.font = "14px sans-serif";
        ctx.textAlign = "center";
        ctx.fillText(i18n.t("notEnoughData") || "Недостаточно данных", W / 2, H / 2);
        return;
    }
    const styles = getComputedStyle(document.documentElement);
    const incomeColor = styles.getPropertyValue("--income").trim() || "#22c55e";
    const expenseColor = styles.getPropertyValue("--expense").trim() || "#ef4444";
    const textColor = styles.getPropertyValue("--text-secondary").trim() || "#64748b";
    const gridColor = styles.getPropertyValue("--border").trim() || "#e2e8f0";

    const PAD = { top: 16, right: 24, bottom: 40, left: 64 };
    const chartW = W - PAD.left - PAD.right;
    const chartH = H - PAD.top - PAD.bottom;

    const allValues = entries.flatMap(([, v]) => [v.income, v.expense]);
    const maxVal = Math.max(...allValues, 1);

    const xStep = chartW / (entries.length - 1);

    function toX(i) { return PAD.left + i * xStep; }
    function toY(val) { return PAD.top + chartH - (val / maxVal * chartH); }

    // Grid lines
    [0, 0.25, 0.5, 0.75, 1].forEach(ratio => {
        const y = PAD.top + chartH - ratio * chartH;
        ctx.beginPath();
        ctx.strokeStyle = gridColor;
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 4]);
        ctx.moveTo(PAD.left, y);
        ctx.lineTo(W - PAD.right, y);
        ctx.stroke();
        ctx.setLineDash([]);
        const label = Math.round(maxVal * ratio);
        ctx.fillStyle = textColor;
        ctx.font = "11px sans-serif";
        ctx.textAlign = "right";
        ctx.fillText(label >= 1000 ? `${Math.round(label / 1000)}k` : label, PAD.left - 6, y + 4);
    });

    // Income line
    ctx.beginPath();
    ctx.strokeStyle = incomeColor;
    ctx.lineWidth = 2.5;
    ctx.lineJoin = "round";
    entries.forEach(([, v], i) => {
        i === 0 ? ctx.moveTo(toX(i), toY(v.income)) : ctx.lineTo(toX(i), toY(v.income));
    });
    ctx.stroke();

    // Expense line
    ctx.beginPath();
    ctx.strokeStyle = expenseColor;
    ctx.lineWidth = 2.5;
    ctx.lineJoin = "round";
    entries.forEach(([, v], i) => {
        i === 0 ? ctx.moveTo(toX(i), toY(v.expense)) : ctx.lineTo(toX(i), toY(v.expense));
    });
    ctx.stroke();

    // Dots + x-labels
    entries.forEach(([month, v], i) => {
        [{ val: v.income, color: incomeColor }, { val: v.expense, color: expenseColor }].forEach(({ val, color }) => {
            ctx.beginPath();
            ctx.fillStyle = color;
            ctx.arc(toX(i), toY(val), 4, 0, Math.PI * 2);
            ctx.fill();
        });
        ctx.fillStyle = textColor;
        ctx.font = "11px sans-serif";
        ctx.textAlign = "center";
        ctx.fillText(month.slice(5), toX(i), H - PAD.bottom + 16); // show MM only
    });

    // Legend
    ctx.font = "12px sans-serif";
    ctx.textAlign = "left";
    [{ color: incomeColor, label: i18n.t("income") || "Доход" },
     { color: expenseColor, label: i18n.t("expense") || "Расход" }].forEach(({ color, label }, idx) => {
        const lx = PAD.left + idx * 100;
        const ly = H - 4;
        ctx.fillStyle = color;
        ctx.fillRect(lx, ly - 9, 14, 9);
        ctx.fillStyle = textColor;
        ctx.fillText(label, lx + 18, ly);
    });
}

function createState(message, className = "empty-state") {
    const state = document.createElement("p");
    state.className = className;
    state.textContent = message;
    return state;
}

function renderOperations(operations) {
    const container = $("operations-list");
    container.replaceChildren();
    const filtered = getFilteredOperations(operations);
    if (!filtered.length) {
        const key = operations.length ? "noSearchResults" : "noOperations";
        container.append(createState(i18n.t(key), "empty-state"));
        return;
    }
    filtered.forEach((operation) => {
        const element = document.createElement("article");
        element.className = "operation";
        const info = document.createElement("div");
        info.className = "operation-info";
        const type = document.createElement("span");
        type.className = `operation-type ${operation.type === "Доход" ? "type-income" : "type-expense"}`;
        type.textContent = i18n.translateOperationType(operation.type);
        const category = document.createElement("span");
        category.className = "operation-category";
        category.textContent = operation.category ? i18n.t("categoryLabel", { category: i18n.translateCategory(operation.category) }) : "";
        const date = document.createElement("span");
        date.className = "operation-date";
        date.textContent = formatDate(operation.date);
        info.append(type, category, date);

        const actions = document.createElement("div");
        actions.className = "operation-actions";
        const amount = document.createElement("strong");
        amount.className = `operation-amount ${operation.type === "Доход" ? "operation-income" : "operation-expense"}`;
        amount.textContent = `${operation.type === "Доход" ? "+" : "−"}${formatMoney(operation.amount)}`;
        const editButton = document.createElement("button");
        editButton.type = "button";
        editButton.className = "icon-button edit-operation-btn";
        editButton.textContent = "✏️";
        editButton.setAttribute("aria-label", i18n.t("editOperation"));
        editButton.onclick = () => editOperation(operation);
        const deleteButton = document.createElement("button");
        deleteButton.type = "button";
        deleteButton.className = "icon-button delete-operation-btn";
        deleteButton.textContent = "🗑️";
        deleteButton.setAttribute("aria-label", i18n.t("deleteOperation"));
        deleteButton.onclick = () => deleteOperation(operation.id);
        actions.append(amount, editButton, deleteButton);
        element.append(info, actions);
        container.appendChild(element);
    });
}

function renderDashboard(data) {
    $("balance").textContent = formatMoney(data.balance);
    $("summary-balance").textContent = formatMoney(data.balance);
    $("total-income").textContent = formatMoney(data.total_income);
    $("total-expense").textContent = formatMoney(data.total_expense);
    const periodNames = { all: "allTime", today: "today", week: "week", month: "month" };
    $("period-summary-content").innerHTML = [
        `<p class="summary-period"><strong>${i18n.t(periodNames[currentPeriod])}</strong></p>`,
        `<p>${i18n.t("periodIncome", { amount: formatMoney(data.total_income) })}</p>`,
        `<p>${i18n.t("periodExpense", { amount: formatMoney(data.total_expense) })}</p>`,
        `<p>${i18n.t("periodBalance", { amount: formatMoney(data.total_income - data.total_expense) })}</p>`,
        `<p>${i18n.t("savingsRate")}: ${data.savings_rate}%</p>`,
        `<p class="muted">${i18n.t("operationCount", { count: data.operations.length })}</p>`
    ].join("");
    renderCategories(data.categories);
    renderChart(data.categories);
    renderTrendChart(data.monthly_trend);
    renderOperations(data.operations);
    renderCalendar(data.operations);
    updateCategoryFilterOptions();
}

function renderCalendar(operations) {
    const grid = $("calendar-grid");
    if (!grid) return;
    grid.replaceChildren();

    const now = new Date();
    const year = now.getFullYear();
    const month = now.getMonth();

    const firstDay = new Date(year, month, 1);
    const lastDay = new Date(year, month + 1, 0);
    const daysInMonth = lastDay.getDate();
    const startDayOfWeek = (firstDay.getDay() + 6) % 7; // Monday = 0

    // 1 January 2024 was a Monday: seven days from it give the weekday names, Monday first,
    // in the interface language.
    const weekdayFormat = new Intl.DateTimeFormat(LOCALES[i18n.getLanguage()], { weekday: "short" });
    const daysOfWeek = Array.from({ length: 7 }, (_, index) => weekdayFormat.format(new Date(2024, 0, 1 + index)));
    daysOfWeek.forEach(d => {
        const header = document.createElement("div");
        header.className = "calendar-day-header";
        header.textContent = d;
        grid.appendChild(header);
    });

    for (let i = 0; i < startDayOfWeek; i++) {
        const emptyCell = document.createElement("div");
        emptyCell.className = "calendar-cell empty";
        grid.appendChild(emptyCell);
    }

    const opsByDate = {};
    operations.forEach(op => {
        const dateMatch = String(op.date).match(/^(\d{2})\.(\d{2})\.(\d{4})/);
        if (dateMatch) {
            const dayKey = `${dateMatch[1]}.${dateMatch[2]}.${dateMatch[3]}`;
            if (!opsByDate[dayKey]) opsByDate[dayKey] = [];
            opsByDate[dayKey].push(op);
        }
    });

    for (let day = 1; day <= daysInMonth; day++) {
        const dayStr = String(day).padStart(2, "0");
        const monthStr = String(month + 1).padStart(2, "0");
        const dateKey = `${dayStr}.${monthStr}.${year}`;
        const dayOps = opsByDate[dateKey] || [];

        const cell = document.createElement("div");
        cell.className = `calendar-cell ${dayOps.length > 0 ? "has-ops" : ""}`;
        cell.innerHTML = `
            <span class="calendar-cell-date">${day}</span>
            ${dayOps.length > 0 ? '<span class="calendar-cell-dot"></span>' : ''}
        `;
        cell.onclick = () => {
            const details = $("calendar-details");
            details.hidden = false;
            if (dayOps.length === 0) {
                details.textContent = `${dateKey}: ${i18n.t("noOperationsOnDate")}`;
            } else {
                const heading = document.createElement("strong");
                heading.textContent = `${dateKey}:`;
                details.replaceChildren(heading);
                dayOps.forEach(op => {
                    details.append(document.createElement("br"), `${i18n.translateOperationType(op.type)}: ${formatMoney(op.amount)} (${op.category || '-'})`);
                });
            }
        };
        grid.appendChild(cell);
    }
}


async function loadBudgets() {
    const generation = sessionGeneration;
    try {
        const budgets = await request("/budgets");
        if (generation === sessionGeneration) renderBudgets(budgets);
    } catch (error) {
        if (generation === sessionGeneration) $("budgets-list").replaceChildren(createState(i18n.t("loadError"), "error-state"));
    }
}

function createBlock(className, ...children) {
    const block = document.createElement("div");
    block.className = className;
    block.append(...children);
    return block;
}

function createText(tag, text) {
    const element = document.createElement(tag);
    element.textContent = text;
    return element;
}

function createIconButton(label, onClick) {
    const button = document.createElement("button");
    button.className = "icon-button";
    button.type = "button";
    button.setAttribute("aria-label", label);
    button.textContent = "×";
    button.onclick = onClick;
    return button;
}

function renderBudgets(budgets) {
    const container = $("budgets-list");
    container.replaceChildren();
    if (!budgets || budgets.length === 0) {
        container.replaceChildren(createState(i18n.t("noBudgets"), "empty-state"));
        return;
    }
    budgets.forEach(budget => {
        const item = document.createElement("div");
        item.className = "budget-item";
        const header = createBlock("budget-header",
            createText("span", i18n.translateCategory(budget.category)),
            createIconButton(i18n.t("deleteBudget"), () => deleteBudget(budget.id)));
        const fill = createBlock(`budget-progress-fill ${budget.exceeded ? 'exceeded' : (budget.level >= 1 ? 'warning' : '')}`);
        fill.style.width = `${Math.min(100, Number(budget.percent) || 0)}%`;
        const footer = createBlock("budget-footer",
            createText("span", i18n.t("spentOf", { spent: formatMoney(budget.spent), amount: formatMoney(budget.amount) })),
            createText("span", `${budget.percent}%`));
        item.append(header, createBlock("budget-progress-bg", fill), footer);
        container.appendChild(item);
    });
}

async function deleteBudget(id) {
    const generation = sessionGeneration;
    try {
        await request(`/budgets/${id}`, { method: "DELETE" });
        if (!isCurrentSession(generation)) return;
        await loadBudgets();
        showNotification(i18n.t("deletedSuccess"));
    } catch (error) {
        if (isCurrentSession(generation)) showNotification(i18n.translateApiError(error.detail, error.status), "error");
    }
}

function updateBudgetCategoryOptions() {
    const select = $("budget-category");
    if (!select) return;
    select.replaceChildren(...categoryNames("Расход").map((value) => new Option(i18n.translateCategory(value), value)));
}

// --- The user's own categories ---
// Built-in names first, then the user's own of that kind (type is "Доход" or "Расход").
function categoryNames(type) {
    const builtIn = type === "Доход" ? INCOME_CATEGORIES : EXPENSE_CATEGORIES;
    const own = userCategories.filter((category) => category.kind === type).map((category) => category.name);
    return [...new Set([...builtIn, ...own])];
}

// Every control that lists categories follows the current list (and the interface language).
function refreshCategoryControls() {
    updateCategoryOptions($("operation-category")?.value);
    updateCategoryFilterOptions();
    updateBudgetCategoryOptions();
    renderMyCategories();
}

// A failure keeps the list as it is: the built-in categories always work.
async function loadCategories() {
    const generation = sessionGeneration;
    try {
        const data = await request("/categories");
        if (!isCurrentSession(generation)) return;
        userCategories = (Array.isArray(data.categories) ? data.categories : []).filter((category) => !category.builtin);
        refreshCategoryControls();
    } catch (_) {
        // Older server or a temporary error: nothing to show about it.
    }
}

function renderMyCategories() {
    const container = $("my-categories-list");
    if (!container) return;
    container.replaceChildren();
    if (!userCategories.length) {
        container.append(createState(i18n.t("noCustomCategories"), "empty-state"));
        return;
    }
    userCategories.forEach((category) => {
        const item = document.createElement("div");
        item.className = "my-category-item";
        const label = document.createElement("span");
        label.className = "my-category-name";
        label.textContent = category.name;
        const kind = document.createElement("span");
        kind.className = "my-category-kind";
        kind.textContent = i18n.translateOperationType(category.kind);
        label.append(kind);
        const actions = document.createElement("div");
        actions.className = "operation-actions";
        const rename = document.createElement("button");
        rename.type = "button";
        rename.className = "icon-button edit-operation-btn";
        rename.textContent = "✏️";
        rename.setAttribute("aria-label", i18n.t("renameCategory"));
        rename.onclick = () => renameCategory(category);
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "icon-button delete-operation-btn";
        remove.textContent = "🗑️";
        remove.setAttribute("aria-label", i18n.t("deleteCategory"));
        remove.onclick = () => deleteCategory(category);
        actions.append(rename, remove);
        item.append(label, actions);
        container.appendChild(item);
    });
}

async function renameCategory(category) {
    const answer = prompt(i18n.t("renameCategoryPrompt"), category.name);
    const name = answer === null ? "" : answer.trim();
    if (!name || name === category.name) return;
    const generation = sessionGeneration;
    try {
        await request(`/categories/${category.id}`, { method: "PUT", body: JSON.stringify({ name }) });
        if (!isCurrentSession(generation)) return;
        await loadDashboard();  // operations and budgets carry the new name; this reloads the categories too
        showNotification(i18n.t("categoryRenamed"));
    } catch (error) {
        if (isCurrentSession(generation)) showNotification(i18n.translateApiError(error.detail, error.status), "error");
    }
}

// The operations of a deleted category move to "Другое"; the server refuses while a budget uses it.
async function deleteCategory(category) {
    if (!confirm(i18n.t("deleteCategoryConfirm", { name: category.name, target: i18n.translateCategory("Другое") }))) return;
    const generation = sessionGeneration;
    try {
        await request(`/categories/${category.id}`, { method: "DELETE" });
        if (!isCurrentSession(generation)) return;
        await loadDashboard();
        showNotification(i18n.t("categoryDeleted"));
    } catch (error) {
        if (isCurrentSession(generation)) showNotification(i18n.translateApiError(error.detail, error.status), "error");
    }
}

async function loadGoals() {
    const generation = sessionGeneration;
    try {
        const goals = await request("/goals");
        if (generation === sessionGeneration) renderGoals(goals);
    } catch (error) {
        if (generation === sessionGeneration) $("goals-list").replaceChildren(createState(i18n.t("loadError"), "error-state"));
    }
}

function renderGoals(goals) {
    const container = $("goals-list");
    container.replaceChildren();
    if (!goals || goals.length === 0) {
        container.replaceChildren(createState(i18n.t("noGoals"), "empty-state"));
        return;
    }
    goals.forEach(goal => {
        const item = document.createElement("div");
        item.className = "goal-item";
        const header = createBlock("goal-header",
            createText("span", goal.title),
            createIconButton(i18n.t("deleteGoal"), () => deleteGoal(goal.id)));
        const fill = createBlock("goal-progress-fill");
        fill.style.width = `${Number(goal.progress_percent) || 0}%`;
        const footer = createBlock("goal-footer",
            createText("span", `${i18n.t("currentAmount")}: ${formatMoney(goal.current_amount)} / ${formatMoney(goal.target_amount)}`),
            createText("span", `${goal.progress_percent}%`));
        const input = document.createElement("input");
        input.type = "number";
        input.className = "goal-progress-input";
        input.placeholder = i18n.t("updateProgress");
        input.min = "0";
        input.style.width = "100%";
        const saveButton = createText("button", i18n.t("save"));
        saveButton.className = "secondary-button";
        saveButton.type = "button";
        saveButton.onclick = () => updateGoalProgress(goal.id, input.value);
        const update = createBlock("goal-update", input, saveButton);
        Object.assign(update.style, { display: "flex", gap: "8px", marginTop: "8px" });
        item.append(header, createBlock("goal-progress-bg", fill), footer, update);
        container.appendChild(item);
    });
}

async function updateGoalProgress(id, value) {
    const generation = sessionGeneration;
    try {
        await request(`/goals/${id}/progress`, {
            method: "PUT",
            body: JSON.stringify({ current_amount: Number(value) })
        });
        if (!isCurrentSession(generation)) return;
        await loadGoals();
        showNotification(i18n.t("updatedSuccess"));
    } catch (error) {
        if (isCurrentSession(generation)) showNotification(i18n.translateApiError(error.detail, error.status), "error");
    }
}

async function deleteGoal(id) {
    const generation = sessionGeneration;
    try {
        await request(`/goals/${id}`, { method: "DELETE" });
        if (!isCurrentSession(generation)) return;
        await loadGoals();
        showNotification(i18n.t("deletedSuccess"));
    } catch (error) {
        if (isCurrentSession(generation)) showNotification(i18n.translateApiError(error.detail, error.status), "error");
    }
}

async function loadDashboard() {
    $("operations-list").replaceChildren(createState(i18n.t("loadingOperations"), "loading-state"));
    // Budgets and goals load on their own and show their own errors: a failure
    // there must not hide fresh operations and balance behind an old snapshot.
    const secondary = Promise.all([loadBudgets(), loadGoals(), loadCategories()]);
    const generation = sessionGeneration;
    let data;
    try {
        const [balance, statistics, operations] = await Promise.all([
            request("/balance"),
            request(`/statistics?period=${currentPeriod}`),
            request(`/operations?period=${currentPeriod}`)
        ]);
        data = { balance: balance.balance, ...statistics, operations };
    } catch (error) {
        if (generation !== sessionGeneration) return;  // signed out meanwhile
        // Offline or server error: fall back to the last dashboard that rendered.
        const cached = localData();
        if (cached) {
            currentDashboard = cached;
            renderDashboard(cached);
            showNotification(i18n.t("loadError"), "error");
        } else {
            $("operations-list").replaceChildren(createState(i18n.t("loadError"), "error-state"));
            showNotification(i18n.translateApiError(error.detail, error.status), "error");
        }
        await secondary;
        return;
    }
    if (generation !== sessionGeneration) return;  // signed out meanwhile
    currentDashboard = data;
    try {
        renderDashboard(data);
    } catch (error) {
        // Show the failure instead of an endless loading state or stale data.
        console.error("Dashboard render failed", error);
        $("operations-list").replaceChildren(createState(i18n.t("loadError"), "error-state"));
        showNotification(i18n.t("loadError"), "error");
        await secondary;
        return;
    }
    saveLocalData(data);  // only a dashboard that rendered becomes the offline snapshot
    await secondary;
}

// --- Date and time of an operation ---
// Operation dates are stored as text. The frontend and the CSV import write
// "DD.MM.YYYY, HH:MM:SS"; older rows may hold other forms (see budget_alerts.DATE_FORMATS
// on the server). The <input type="datetime-local"> works with "YYYY-MM-DDTHH:MM".
const STORED_DATE_PATTERN = /^(\d{1,2})\.(\d{1,2})\.(\d{4})(?:,?\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?$/;
const ISO_DATE_PATTERN = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?$/;
const INPUT_DATE_PATTERN = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/;
// The stored forms the server accepts as they are (api.OPERATION_DATE_FORMATS).
const SERVER_DATE_PATTERN = /^(\d{1,2}\.\d{1,2}\.\d{4}(, \d{1,2}:\d{2}(:\d{2})?| \d{1,2}:\d{2})?|\d{4}-\d{2}-\d{2}(T\d{2}:\d{2})?)$/;

function pad2(number) {
    return String(number).padStart(2, "0");
}

// The parts of a real calendar date and time of day, or null (31.02, hour 24, ...).
function dateParts(year, month, day, hour = 0, minute = 0, second = 0) {
    const date = new Date(Date.UTC(year, month - 1, day, hour, minute, second));
    const valid = date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day
        && date.getUTCHours() === hour && date.getUTCMinutes() === minute && date.getUTCSeconds() === second;
    return valid ? { year, month, day, hour, minute, second } : null;
}

function parseOperationDate(text) {
    const value = String(text ?? "").trim();
    let match = value.match(STORED_DATE_PATTERN);
    if (match) {
        const [, day, month, year, hour, minute, second] = match;
        return dateParts(Number(year), Number(month), Number(day), Number(hour ?? 0), Number(minute ?? 0), Number(second ?? 0));
    }
    match = value.match(ISO_DATE_PATTERN);
    if (match) {
        const [, year, month, day, hour, minute] = match;
        return dateParts(Number(year), Number(month), Number(day), Number(hour ?? 0), Number(minute ?? 0));
    }
    return null;
}

function toDateTimeInput(parts) {
    return `${String(parts.year).padStart(4, "0")}-${pad2(parts.month)}-${pad2(parts.day)}T${pad2(parts.hour)}:${pad2(parts.minute)}`;
}

// The value for the date field of a stored date, or "" when it cannot be read.
function operationDateToInput(text) {
    const parts = parseOperationDate(text);
    return parts ? toDateTimeInput(parts) : "";
}

// The text to send for a date field value, or null when it is empty or not a real date.
function inputToServerDate(value) {
    const match = String(value ?? "").trim().match(INPUT_DATE_PATTERN);
    if (!match) return null;
    const [, year, month, day, hour, minute, second] = match;
    const parts = dateParts(Number(year), Number(month), Number(day), Number(hour), Number(minute), Number(second ?? 0));
    if (!parts) return null;
    return `${pad2(parts.day)}.${pad2(parts.month)}.${String(parts.year).padStart(4, "0")}, ${pad2(parts.hour)}:${pad2(parts.minute)}:${pad2(parts.second)}`;
}

function currentDateTimeInput() {
    const now = new Date();
    return toDateTimeInput({
        year: now.getFullYear(), month: now.getMonth() + 1, day: now.getDate(),
        hour: now.getHours(), minute: now.getMinutes()
    });
}

function setOperationDate(value) {
    if ($("operation-date")) $("operation-date").value = value;
}

// The date to send when an operation is saved: what the field holds, or the stored text
// itself if this is an edit and the field was not changed. null means the field has no
// usable date. An empty or invalid field is never replaced by the current time.
function operationDateToSend() {
    const field = $("operation-date");
    if (!field) return inputToServerDate(currentDateTimeInput());  // a page cached before the field existed
    const unchanged = editingOperationDate && field.value !== "" && field.value === editingOperationDate.field
        && SERVER_DATE_PATTERN.test(String(editingOperationDate.original).trim());
    return unchanged ? String(editingOperationDate.original).trim() : inputToServerDate(field.value);
}

function resetForm() {
    editingOperationId = null;
    editingOperationDate = null;
    $("operation-form-title").textContent = i18n.t("newOperation");
    $("save-operation-btn").textContent = i18n.t("save");
    $("operation-amount").value = "";
    setOperationDate(currentDateTimeInput());
    setFormType("Доход");
    $("operation-form").classList.add("hidden");
}

function openForm() {
    $("operation-form").classList.remove("hidden");
    $("operation-amount").focus();
}

function editOperation(operation) {
    editingOperationId = operation.id;
    $("operation-form-title").textContent = i18n.t("editOperation");
    $("save-operation-btn").textContent = i18n.t("saveChanges");
    setFormType(operation.type);
    updateCategoryOptions(operation.category || "");
    $("operation-amount").value = operation.amount;
    const field = operationDateToInput(operation.date);
    editingOperationDate = { original: operation.date, field };
    setOperationDate(field);
    openForm();
    if (!field) showNotification(i18n.t("operationDateUnreadable"), "warning");
}

async function deleteOperation(id) {
    if (!confirm(i18n.t("deleteConfirmation"))) return;
    const generation = sessionGeneration;
    try {
        await request(`/operations/${id}`, { method: "DELETE" });
        if (!isCurrentSession(generation)) return;
        await loadDashboard();
        showNotification(i18n.t("deletedSuccess"));
    } catch (error) {
        if (isCurrentSession(generation)) showNotification(i18n.translateApiError(error.detail, error.status), "error");
    }
}

// The interface uses the i18n codes ru / en / kk. The server stores RU / EN / KZ,
// and profiles saved by older versions may hold KK: convert only at that border.
const SERVER_LANGUAGE_CODES = { ru: "RU", en: "EN", kk: "KZ" };

function languageFromServer(code) {
    const value = String(code || "").trim().toLowerCase();
    const language = value === "kz" ? "kk" : value;
    return i18n.SUPPORTED_LANGUAGES.includes(language) ? language : null;
}

function languageForServer(language) {
    return SERVER_LANGUAGE_CODES[language] || SERVER_LANGUAGE_CODES[i18n.DEFAULT_LANGUAGE];
}

// The header switcher: applies the language at once and, once this account's
// settings are known, stores it in the profile so it survives a reload.
async function selectLanguage(language) {
    changeLanguage(language);
    if (!settingsReady) return;
    const code = languageForServer(i18n.getLanguage());
    if ($("settings-language")) $("settings-language").value = code;
    const generation = sessionGeneration;
    try {
        await request("/auth/profile", { method: "PUT", body: JSON.stringify({ language: code }) });
    } catch (error) {
        if (isCurrentSession(generation)) showNotification(i18n.translateApiError(error.detail, error.status), "error");
    }
}

function changeLanguage(language) {
    i18n.setLanguage(language);
    localStorage.setItem(LANGUAGE_KEY, i18n.getLanguage());
    applyLanguage();
}

function toggleTheme() {
    applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
}

// The account's settings (currency above all) must be in place before its
// figures are drawn, so a session always loads them first.
async function startSession() {
    const generation = sessionGeneration;
    setSettingsReady(false);
    await loadUserSettings();
    // An authorisation failure ended the session: there is no dashboard to load.
    if (!isCurrentSession(generation)) return;
    await loadDashboard();
}

// Returns whether this account's settings were applied. On failure nothing is
// applied and saving stays disabled, so the defaults set at sign-out remain.
async function loadUserSettings() {
    const generation = sessionGeneration;
    try {
        const user = await request("/auth/me");
        if (generation !== sessionGeneration) return false;
        if ($("settings-email")) $("settings-email").textContent = user.email;
        if (user.settings) {
            const language = languageFromServer(user.settings.language);
            if ($("settings-language")) $("settings-language").value = languageForServer(language);
            if ($("settings-theme")) $("settings-theme").value = user.settings.theme || "light";
            if ($("settings-currency")) $("settings-currency").value = user.settings.currency || "KZT";
            // On unless the account says otherwise.
            if ($("settings-alerts-inapp")) $("settings-alerts-inapp").checked = user.settings.budget_alerts_inapp !== false;

            // Sync local state with DB settings. Currency goes first: changing the
            // language re-renders the dashboard, which must use this user's currency.
            if (user.settings.currency) {
                currentCurrency = user.settings.currency;
                updateSymbol(CURRENCY_SYMBOLS[currentCurrency] || "₸");
            }
            if (language) changeLanguage(language);
            if (user.settings.theme) applyTheme(user.settings.theme);
        }
        setSettingsReady(true);
        return true;
    } catch (error) {
        if (generation === sessionGeneration && !error.sessionEnded) {
            console.error("Error loading user settings", error);
            showNotification(i18n.t("settingsLoadError"), "error");
        }
        return false;
    }
}

// --- Bank CSV import ---
const IMPORT_MAX_BYTES = 1000000;
const IMPORT_STEPS = ["file", "mapping", "preview", "report"];
const IMPORT_COLUMN_FIELDS = {
    date_column: "import-date-column",
    description_column: "import-description-column",
    category_column: "import-category-column",
    amount_column: "import-amount-column",
    type_column: "import-type-column",
    income_column: "import-income-column",
    expense_column: "import-expense-column",
    currency_column: "import-currency-column",
    reference_column: "import-reference-column"
};
const importState = { file: null, columns: [], sample: [], currency: "", preview: null, report: null, step: "file" };

function showImportStep(step) {
    importState.step = step;
    const current = IMPORT_STEPS.indexOf(step);
    document.querySelectorAll("[data-import-pane]").forEach((pane) => {
        pane.hidden = pane.dataset.importPane !== step;
    });
    document.querySelectorAll("[data-import-step]").forEach((item) => {
        const index = IMPORT_STEPS.indexOf(item.dataset.importStep);
        item.classList.toggle("active", index === current);
        item.classList.toggle("done", index < current);
    });
    $("import-error").textContent = "";
}

function showImportError(error) {
    $("import-error").textContent = typeof error === "string"
        ? error
        : i18n.translateApiError(error.detail, error.status);
}

function setImportBusy(busy) {
    document.querySelectorAll("#import-modal button, #import-modal input, #import-modal select").forEach((element) => {
        if (element.id !== "import-close-btn") element.disabled = busy;
    });
    if (busy) $("import-error").textContent = i18n.t("importLoading");
}

function importColumnLabel(index) {
    return importState.columns[index] || i18n.t("importColumn", { n: index + 1 });
}

function fillCategorySelect(select, categories, selected) {
    select.replaceChildren(...categories.map((category) => new Option(i18n.translateCategory(category), category)));
    select.value = categories.includes(selected) ? selected : "Другое";
}

function fillImportCategorySelects() {
    fillCategorySelect($("import-default-income"), categoryNames("Доход"), $("import-default-income").value);
    fillCategorySelect($("import-default-expense"), categoryNames("Расход"), $("import-default-expense").value);
}

function fillImportColumnSelects(suggested = null) {
    for (const [field, id] of Object.entries(IMPORT_COLUMN_FIELDS)) {
        const select = $(id);
        const previous = select.value;
        const options = [new Option(i18n.t("importNotUsed"), "")];
        importState.columns.forEach((_, index) => options.push(new Option(importColumnLabel(index), String(index))));
        select.replaceChildren(...options);
        const value = suggested ? (field in suggested ? String(suggested[field]) : "") : previous;
        select.value = value !== "" && Number(value) < importState.columns.length ? value : "";
    }
    if (suggested && suggested.amount_mode) $("import-amount-mode").value = suggested.amount_mode;
    updateImportModeFields();
}

function updateImportModeFields() {
    const mode = $("import-amount-mode").value;
    document.querySelectorAll("[data-import-mode]").forEach((field) => {
        field.hidden = !field.dataset.importMode.split(" ").includes(mode);
    });
}

function hasRequiredImportMapping() {
    const mode = $("import-amount-mode").value;
    const filled = (id) => $(id).value !== "";
    if (!filled("import-date-column")) return false;
    if (mode === "debit_credit") return filled("import-income-column") && filled("import-expense-column");
    if (mode === "type_column") return filled("import-amount-column") && filled("import-type-column");
    return filled("import-amount-column");
}

function importQuery(withMapping) {
    const params = new URLSearchParams({
        delimiter: $("import-delimiter").value,
        has_header: String($("import-has-header").checked)
    });
    if (withMapping) {
        params.set("date_format", $("import-date-format").value);
        params.set("amount_mode", $("import-amount-mode").value);
        params.set("decimal_separator", $("import-decimal").value);
        params.set("invert_sign", String($("import-invert-sign").checked));
        params.set("income_values", $("import-income-values").value);
        params.set("expense_values", $("import-expense-values").value);
        params.set("default_income_category", $("import-default-income").value);
        params.set("default_expense_category", $("import-default-expense").value);
        for (const [field, id] of Object.entries(IMPORT_COLUMN_FIELDS)) {
            if ($(id).value !== "") params.set(field, $(id).value);
        }
    }
    return params.toString();
}

function sendImport(path, withMapping) {
    return request(`${path}?${importQuery(withMapping)}`, {
        method: "POST",
        body: importState.file,
        headers: { "Content-Type": "text/csv" }
    });
}

function buildTable(table, headers, rows) {
    const head = document.createElement("thead");
    const headRow = document.createElement("tr");
    headers.forEach((text) => {
        const cell = document.createElement("th");
        cell.textContent = text;
        headRow.append(cell);
    });
    head.append(headRow);
    const body = document.createElement("tbody");
    rows.forEach(({ cells, className = "" }) => {
        const row = document.createElement("tr");
        if (className) row.className = className;
        cells.forEach((value) => {
            const cell = document.createElement("td");
            if (value instanceof Node) cell.append(value);
            else if (value && typeof value === "object") {
                cell.textContent = value.text;
                cell.className = value.className || "";
            } else cell.textContent = value ?? "";
            row.append(cell);
        });
        body.append(row);
    });
    table.replaceChildren(head, body);
}

function renderImportSample() {
    const headers = importState.columns.map((_, index) => importColumnLabel(index));
    buildTable($("import-sample"), headers, importState.sample.map((cells) => ({ cells })));
    $("import-currency-note").textContent = i18n.t("importCurrencyNote", { currency: importState.currency });
}

function renderImportErrors(container, errors, total) {
    container.replaceChildren();
    if (!total) return;
    const title = document.createElement("h4");
    title.textContent = i18n.t("importErrorsTitle");
    const list = document.createElement("ul");
    errors.forEach((error) => {
        const item = document.createElement("li");
        const column = error.column === null || error.column === undefined ? "" : ` (${importColumnLabel(error.column)})`;
        item.textContent = `${i18n.t("importLine", { line: error.line })}: ${i18n.t(`importError_${error.code}`)}${column}`;
        list.append(item);
    });
    container.append(title, list);
    if (total > errors.length) container.append(createState(i18n.t("importShownFirst", { count: errors.length }), "import-hint"));
}

function renderImportPreview() {
    const data = importState.preview;
    const ready = data.valid_rows - data.duplicate_rows;
    $("import-summary").textContent = i18n.t("importSummary", {
        total: data.total_rows, valid: ready, duplicates: data.duplicate_rows, errors: data.error_rows
    });
    buildTable(
        $("import-preview-table"),
        [i18n.t("importDateColumn"), i18n.t("importDescriptionColumn"), i18n.t("importCategoryColumn"), i18n.t("operationType"), i18n.t("importAmountColumn")],
        data.rows.map((row) => {
            const income = row.type === "Доход";
            let description = row.description;
            if (row.duplicate) {
                description = document.createElement("span");
                const badge = document.createElement("span");
                badge.className = "import-badge";
                badge.textContent = i18n.t("importDuplicate");
                description.append(document.createTextNode(`${row.description} `), badge);
            }
            return {
                className: row.duplicate ? "duplicate" : "",
                cells: [
                    formatDate(row.date),
                    description,
                    i18n.translateCategory(row.category),
                    i18n.translateOperationType(row.type),
                    { text: `${income ? "+" : "−"}${formatMoney(row.amount)}`, className: income ? "income" : "expense" }
                ]
            };
        })
    );
    $("import-preview-more").textContent = data.valid_rows > data.rows.length
        ? i18n.t("importShownFirst", { count: data.rows.length })
        : "";
    renderImportErrors($("import-errors"), data.errors, data.error_rows);
    const confirm = $("import-confirm-btn");
    confirm.textContent = ready > 0 ? i18n.t("importConfirm", { count: ready }) : i18n.t("importNothingNew");
    confirm.disabled = ready <= 0;
}

function renderImportReport() {
    const data = importState.report;
    $("import-report").textContent = i18n.t("importReport", {
        imported: data.imported, duplicates: data.duplicate_rows, errors: data.error_rows
    });
    renderImportErrors($("import-report-errors"), data.errors, data.error_rows);
}

function refreshImportLanguage() {
    if (!$("import-modal") || $("import-modal").hidden) return;
    fillImportCategorySelects();
    if (importState.columns.length) {
        fillImportColumnSelects();
        renderImportSample();
    }
    if (importState.preview) renderImportPreview();
    if (importState.report) renderImportReport();
}

async function loadImportStructure(useSuggestions) {
    const generation = sessionGeneration;
    setImportBusy(true);
    try {
        const data = await sendImport("/import/csv/preview", false);
        if (generation !== sessionGeneration) return;  // signed out meanwhile
        importState.columns = data.columns;
        importState.sample = data.sample;
        importState.currency = data.currency;
        setImportBusy(false);
        fillImportColumnSelects(useSuggestions ? data.suggested : null);
        renderImportSample();
        showImportStep("mapping");
    } catch (error) {
        if (generation !== sessionGeneration) return;
        setImportBusy(false);
        showImportError(error);
    }
}

async function onImportFileSelected() {
    const file = $("import-file").files[0];
    if (!file) return;
    if (!/\.csv$/i.test(file.name)) return showImportError(i18n.t("importNotCsv"));
    if (file.size > IMPORT_MAX_BYTES) return showImportError(i18n.t("importFileTooLarge"));
    importState.file = file;
    importState.preview = null;
    importState.report = null;
    await loadImportStructure(true);
}

async function runImportPreview() {
    if (!hasRequiredImportMapping()) return showImportError(i18n.t("importRequiredHint"));
    const generation = sessionGeneration;
    setImportBusy(true);
    try {
        const preview = await sendImport("/import/csv/preview", true);
        if (generation !== sessionGeneration) return;  // signed out meanwhile
        importState.preview = preview;
        setImportBusy(false);
        renderImportPreview();
        showImportStep("preview");
    } catch (error) {
        if (generation !== sessionGeneration) return;
        setImportBusy(false);
        showImportError(error);
    }
}

async function confirmImport() {
    const generation = sessionGeneration;
    setImportBusy(true);
    try {
        const report = await sendImport("/import/csv/commit", true);
        // Signed out meanwhile: the server finished the import for the previous
        // account, but this page now belongs to another session.
        if (generation !== sessionGeneration) return;
        importState.report = report;
        setImportBusy(false);
        renderImportReport();
        showImportStep("report");
    } catch (error) {
        if (generation !== sessionGeneration) return;
        setImportBusy(false);
        renderImportPreview();
        showImportError(error);
        return;
    }
    await loadDashboard();
    const alertNotice = generation === sessionGeneration ? budgetAlertNotice(importState.report) : null;
    if (alertNotice) showNotification(alertNotice.text, alertNotice.type);
}

function openImportModal() {
    Object.assign(importState, { file: null, columns: [], sample: [], currency: "", preview: null, report: null });
    $("import-file").value = "";
    fillImportCategorySelects();
    showImportStep("file");
    $("import-modal").hidden = false;
    $("import-file").focus();
}

function closeImportModal() {
    $("import-modal").hidden = true;
    importState.file = null;
}

function setupImport() {
    // A stale cached page may lack the import markup; never let that stop the app.
    if (!$("import-modal") || !$("import-csv-btn")) return;
    $("import-csv-btn").onclick = openImportModal;
    $("import-close-btn").onclick = closeImportModal;
    $("import-finish-btn").onclick = closeImportModal;
    $("import-file").onchange = onImportFileSelected;
    $("import-delimiter").onchange = () => loadImportStructure(false);
    $("import-has-header").onchange = () => loadImportStructure(true);
    $("import-amount-mode").onchange = updateImportModeFields;
    $("import-preview-btn").onclick = runImportPreview;
    $("import-confirm-btn").onclick = confirmImport;
    document.querySelectorAll("[data-import-back]").forEach((button) => {
        button.onclick = () => showImportStep(button.dataset.importBack);
    });
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && !$("import-modal").hidden) closeImportModal();
    });
}

document.addEventListener("DOMContentLoaded", () => {
    setupImport();

    i18n.setLanguage(getStoredLanguage());
    applyTheme(getStoredTheme());
    setOperationDate(currentDateTimeInput());
    document.querySelectorAll(".language-button").forEach((button) => {
        button.onclick = () => selectLanguage(button.dataset.language);
    });
    $("theme-toggle").onclick = toggleTheme;
    $("notification-close").onclick = () => { $("notification").hidden = true; };
    $("add-operation-btn").onclick = () => { resetForm(); openForm(); };
    $("cancel-operation-btn").onclick = resetForm;
    $("cancel-operation-secondary-btn").onclick = resetForm;
    $("add-budget-btn").onclick = () => {
        updateBudgetCategoryOptions();
        $("budget-form").classList.remove("hidden");
        $("budget-amount").focus();
    };
    $("cancel-budget-btn").onclick = () => {
        $("budget-form").classList.add("hidden");
        $("budget-amount").value = "";
    };
    $("save-budget-btn").onclick = oncePerClick($("save-budget-btn"), async () => {
        const payload = {
            category: $("budget-category").value,
            amount: Number($("budget-amount").value),
            period: "month"
        };
        const generation = sessionGeneration;
        try {
            await request("/budgets", {
                method: "POST",
                body: JSON.stringify(payload)
            });
            if (!isCurrentSession(generation)) return;
            $("budget-form").classList.add("hidden");
            $("budget-amount").value = "";
            await loadBudgets();
            showNotification(i18n.t("addedSuccess"));
        } catch (error) {
            if (isCurrentSession(generation)) showNotification(i18n.translateApiError(error.detail, error.status), "error");
        }
    });

    if ($("add-category-btn")) {
        $("add-category-btn").onclick = oncePerClick($("add-category-btn"), async () => {
            const generation = sessionGeneration;
            const payload = { kind: $("new-category-kind").value, name: $("new-category-name").value.trim() };
            try {
                await request("/categories", { method: "POST", body: JSON.stringify(payload) });
                if (!isCurrentSession(generation)) return;
                $("new-category-name").value = "";
                await loadCategories();
                showNotification(i18n.t("categoryAdded"));
            } catch (error) {
                if (isCurrentSession(generation)) showNotification(i18n.translateApiError(error.detail, error.status), "error");
            }
        });
    }

    $("add-goal-btn").onclick = () => {
        $("goal-form").classList.remove("hidden");
        $("goal-title").focus();
    };
    $("cancel-goal-btn").onclick = () => {
        $("goal-form").classList.add("hidden");
        $("goal-title").value = "";
        $("goal-target").value = "";
        $("goal-current").value = "0";
        $("goal-deadline").value = "";
    };
    $("save-goal-btn").onclick = oncePerClick($("save-goal-btn"), async () => {
        const payload = {
            title: $("goal-title").value.trim(),
            target_amount: Number($("goal-target").value),
            current_amount: Number($("goal-current").value || 0),
            deadline: $("goal-deadline").value.trim() || null
        };
        const generation = sessionGeneration;
        try {
            await request("/goals", {
                method: "POST",
                body: JSON.stringify(payload)
            });
            if (!isCurrentSession(generation)) return;
            $("goal-form").classList.add("hidden");
            $("goal-title").value = "";
            $("goal-target").value = "";
            $("goal-current").value = "0";
            $("goal-deadline").value = "";
            await loadGoals();
            showNotification(i18n.t("addedSuccess"));
        } catch (error) {
            if (isCurrentSession(generation)) showNotification(i18n.translateApiError(error.detail, error.status), "error");
        }
    });
    document.querySelectorAll(".type-tab").forEach((button) => {
        button.onclick = () => setFormType(button.dataset.operationType);
    });
    $("save-operation-btn").onclick = oncePerClick($("save-operation-btn"), async () => {
        const wasEditing = editingOperationId !== null;
        const date = operationDateToSend();
        if (!date) {
            showNotification(i18n.t("operationDateInvalid"), "error");
            return;
        }
        const payload = {
            date,
            operation_type: getFormType(),
            amount: Number($("operation-amount").value),
            category: $("operation-category").value || null
        };
        const generation = sessionGeneration;
        try {
            const res = await request(wasEditing ? `/operations/${editingOperationId}` : "/operations", {
                method: wasEditing ? "PUT" : "POST",
                body: JSON.stringify(payload)
            });
            // Signed out meanwhile: the server kept the operation for the previous
            // account; its budget warning must not reach the next one.
            if (!isCurrentSession(generation)) return;
            resetForm();
            await loadDashboard();
            const alertNotice = budgetAlertNotice(res);
            if (alertNotice) showNotification(alertNotice.text, alertNotice.type);
            else showNotification(i18n.t(wasEditing ? "updatedSuccess" : "addedSuccess"));
        } catch (error) {
            if (isCurrentSession(generation)) showNotification(i18n.translateApiError(error.detail, error.status), "error");
        }
    });
    document.querySelectorAll(".filter-btn").forEach((button) => {
        button.onclick = () => {
            document.querySelectorAll(".filter-btn").forEach((item) => item.classList.remove("active"));
            button.classList.add("active");
            currentPeriod = button.dataset.period;
            loadDashboard();
        };
    });
    [$("operation-type-filter"), $("operation-category-filter")].forEach((select) => {
        select.onchange = () => currentDashboard && renderOperations(currentDashboard.operations);
    });
    $("operation-search").oninput = () => currentDashboard && renderOperations(currentDashboard.operations);
    $("export-csv-btn").onclick = oncePerClick($("export-csv-btn"), () => downloadExport("csv"));
    $("export-excel-btn").onclick = oncePerClick($("export-excel-btn"), () => downloadExport("excel"));
    $("export-pdf-btn").onclick = oncePerClick($("export-pdf-btn"), () => downloadExport("pdf"));
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("/service-worker.js");
    applyLanguage();

    // Auth wiring
    document.querySelectorAll(".auth-tab").forEach((tab) => {
        tab.onclick = () => {
            document.querySelectorAll(".auth-tab").forEach((t) => t.classList.remove("active"));
            tab.classList.add("active");
            authMode = tab.dataset.authMode;
            updateAuthSubmitLabel();
        };
    });

    $("auth-form").onsubmit = async (e) => {
        e.preventDefault();
        const email = $("auth-email").value.trim();
        const password = $("auth-password").value;
        const endpoint = authMode === "login" ? "/auth/login" : "/auth/register";
        try {
            const data = await request(endpoint, {
                method: "POST",
                body: JSON.stringify({ email, password })
            });
            localStorage.setItem(TOKEN_KEY, data.access_token);
            localStorage.setItem(USER_EMAIL_KEY, data.email || email);
            hideAuthModal();
            renderUserBadge();
            showNotification(i18n.t(authMode === "login" ? "loginSuccess" : "accountCreated"));
            startSession();
        } catch (error) {
            // Only known server messages are translated and shown; FastAPI lists invalid fields
            // (e.g. a short password) as objects, which get the generic text for the status.
            $("auth-error").textContent = i18n.translateApiError(typeof error.detail === "string" ? error.detail : "", error.status);
        }
    };

    renderUserBadge();
    setSettingsReady(false);
    if (!localStorage.getItem(TOKEN_KEY)) {
        showAuthModal();
    } else {
        startSession();
    }

    // Settings wiring
    if ($("save-settings-btn")) {
        $("save-settings-btn").onclick = async () => {
            // Until this account's settings have loaded, the form may still hold
            // defaults; saving them would overwrite the stored settings.
            if (!settingsReady) return;
            const generation = sessionGeneration;
            const settings = {
                language: $("settings-language").value,
                theme: $("settings-theme").value,
                currency: $("settings-currency").value
            };
            if ($("settings-alerts-inapp")) settings.budget_alerts_inapp = Boolean($("settings-alerts-inapp").checked);
            try {
                await request("/auth/profile", { method: "PUT", body: JSON.stringify(settings) });
                if (!isCurrentSession(generation)) return;
                currentCurrency = settings.currency;
                updateSymbol(CURRENCY_SYMBOLS[currentCurrency] || "₸");
                changeLanguage(languageFromServer(settings.language) || i18n.getLanguage());
                applyTheme(settings.theme);
                showNotification(i18n.t("addedSuccess")); // Reusing for success
            } catch (error) {
                if (isCurrentSession(generation)) showNotification(i18n.translateApiError(error.detail, error.status), "error");
            }
        };
    }
    if ($("change-password-btn")) {
        $("change-password-btn").onclick = async () => {
            const generation = sessionGeneration;
            try {
                const res = await request("/auth/password", {
                    method: "PUT",
                    body: JSON.stringify({
                        current_password: $("current-password").value,
                        new_password: $("new-password").value
                    })
                });
                if (!isCurrentSession(generation)) return;
                // The old token is revoked by the change; keep this session on the new one.
                if (res.access_token) localStorage.setItem(TOKEN_KEY, res.access_token);
                showNotification(i18n.t("passwordChangedSuccess"));
                $("current-password").value = "";
                $("new-password").value = "";
            } catch (error) {
                if (isCurrentSession(generation)) showNotification(i18n.translateApiError(error.detail, error.status), "error");
            }
        };
    }
    if ($("delete-account-btn")) {
        $("delete-account-btn").onclick = async () => {
            if (!confirm(i18n.t("deleteConfirmation"))) return;
            const generation = sessionGeneration;
            try {
                await request("/auth/delete", {
                    method: "DELETE",
                    body: JSON.stringify({ password: $("delete-password").value })
                });
                // The account is gone either way; only sign out and reload if the
                // page still belongs to it, not to someone who signed in meanwhile.
                if (!isCurrentSession(generation)) return;
                clearSession();
                window.location.reload();
            } catch (error) {
                if (isCurrentSession(generation)) showNotification(i18n.translateApiError(error.detail, error.status), "error");
            }
        };
    }

    document.querySelectorAll(".mobile-nav-item").forEach(item => {
        item.onclick = () => {
            document.querySelectorAll(".mobile-nav-item").forEach(i => i.classList.remove("active"));
            item.classList.add("active");
            const targetTab = item.dataset.tab;
            document.querySelectorAll(".mobile-tab").forEach(tab => {
                tab.hidden = tab.dataset.mobileTab !== targetTab;
            });
            window.scrollTo({ top: 0, behavior: "smooth" });
        };
    });

    if (window.innerWidth <= 820) {
        document.querySelectorAll(".mobile-tab").forEach(tab => {
            tab.hidden = tab.dataset.mobileTab !== "overview";
        });
    }
});
