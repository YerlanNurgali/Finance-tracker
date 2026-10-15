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
let editingBudgetId = null;
let currentPeriod = "all";
let currentDashboard = null;
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

function localData() {
    try {
        return JSON.parse(localStorage.getItem(LOCAL_DATA_KEY));
    } catch (_) {
        return null;
    }
}

function saveLocalData(data) {
    try {
        localStorage.setItem(LOCAL_DATA_KEY, JSON.stringify(data));
    } catch (_) {
        // Storage can be full or disabled; the snapshot is only an offline fallback.
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
    applyTheme(document.documentElement.dataset.theme || getStoredTheme());
    if (currentDashboard) renderDashboard(currentDashboard);
    refreshImportLanguage();
}

function updateCategoryOptions(selectedValue = "") {
    const select = $("operation-category");
    const type = getFormType();
    if (!select) return;
    const categories = type === "Доход" ? INCOME_CATEGORIES : EXPENSE_CATEGORIES;
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
    const categories = [...new Set([...INCOME_CATEGORIES, ...EXPENSE_CATEGORIES])];
    select.replaceChildren(
        new Option(i18n.t("allCategories"), ""),
        ...categories.map((value) => new Option(i18n.translateCategory(value), value))
    );
    select.value = selected;
}

const TOKEN_KEY = "finance_tracker_token";
const USER_EMAIL_KEY = "finance_tracker_email";
let authMode = "login";

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
        badge.innerHTML = `<span>👤 ${email}</span><button type="button" id="logout-btn">Выйти</button>`;
        area.appendChild(badge);
        $("logout-btn").onclick = () => {
            localStorage.removeItem(TOKEN_KEY);
            localStorage.removeItem(USER_EMAIL_KEY);
            renderUserBadge();
            showAuthModal();
        };
    } else {
        area.replaceChildren();
        const loginBtn = document.createElement("button");
        loginBtn.className = "secondary-button";
        loginBtn.style.padding = "6px 14px";
        loginBtn.textContent = "Войти / Регистрация";
        loginBtn.onclick = () => showAuthModal();
        area.appendChild(loginBtn);
    }
}

async function request(path, options = {}) {
    const token = localStorage.getItem(TOKEN_KEY);
    const headers = {
        "Content-Type": "application/json",
        ...(token ? { "Authorization": `Bearer ${token}` } : {}),
        ...(options.headers || {})
    };
    const response = await fetch(`${API_URL}${path}`, { ...options, headers });
    if (response.status === 401) {
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(USER_EMAIL_KEY);
        renderUserBadge();
        showAuthModal();
        throw new Error("Unauthorized");
    }
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
        throw error;
    }
    return response.json();
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

    const daysOfWeek = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
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
                const list = dayOps.map(op => `${i18n.translateOperationType(op.type)}: ${formatMoney(op.amount)} (${op.category || '-'})`).join("<br>");
                details.innerHTML = `<strong>${dateKey}:</strong><br>${list}`;
            }
        };
        grid.appendChild(cell);
    }
}


async function loadBudgets() {
    try {
        const budgets = await request("/budgets");
        renderBudgets(budgets);
    } catch (error) {
        $("budgets-list").replaceChildren(createState(i18n.t("loadError"), "error-state"));
    }
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
        item.innerHTML = `
            <div class="budget-header">
                <span>${i18n.translateCategory(budget.category)}</span>
                <button class="icon-button" type="button" aria-label="${i18n.t("deleteBudget")}">×</button>
            </div>
            <div class="budget-progress-bg">
                <div class="budget-progress-fill ${budget.exceeded ? 'exceeded' : ''}" style="width: ${Math.min(100, budget.percent)}%"></div>
            </div>
            <div class="budget-footer">
                <span>${i18n.t("spentOf", { spent: formatMoney(budget.spent), amount: formatMoney(budget.amount) })}</span>
                <span>${budget.percent}%</span>
            </div>
        `;
        item.querySelector("button").onclick = () => deleteBudget(budget.id);
        container.appendChild(item);
    });
}

async function deleteBudget(id) {
    try {
        await request(`/budgets/${id}`, { method: "DELETE" });
        await loadBudgets();
        showNotification(i18n.t("deletedSuccess"));
    } catch (error) {
        showNotification(i18n.translateApiError(error.detail, error.status), "error");
    }
}

function updateBudgetCategoryOptions() {
    const select = $("budget-category");
    if (!select) return;
    select.replaceChildren(...EXPENSE_CATEGORIES.map((value) => new Option(i18n.translateCategory(value), value)));
}

async function loadGoals() {
    try {
        const goals = await request("/goals");
        renderGoals(goals);
    } catch (error) {
        $("goals-list").replaceChildren(createState(i18n.t("loadError"), "error-state"));
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
        item.innerHTML = `
            <div class="goal-header">
                <span>${goal.title}</span>
                <button class="icon-button" type="button" aria-label="${i18n.t("deleteGoal")}">×</button>
            </div>
            <div class="goal-progress-bg">
                <div class="goal-progress-fill" style="width: ${goal.progress_percent}%"></div>
            </div>
            <div class="goal-footer">
                <span>${i18n.t("currentAmount")}: ${formatMoney(goal.current_amount)} / ${formatMoney(goal.target_amount)}</span>
                <span>${goal.progress_percent}%</span>
            </div>
            <div class="goal-update" style="display: flex; gap: 8px; margin-top: 8px;">
                <input type="number" class="goal-progress-input" placeholder="${i18n.t("updateProgress")}" min="0" style="width: 100%;">
                <button class="secondary-button" type="button">${i18n.t("save")}</button>
            </div>
        `;
        item.querySelector(".icon-button").onclick = () => deleteGoal(goal.id);
        item.querySelector(".secondary-button").onclick = () => updateGoalProgress(goal.id, item.querySelector('.goal-progress-input').value);
        container.appendChild(item);
    });
}

async function updateGoalProgress(id, value) {
    try {
        await request(`/goals/${id}/progress`, {
            method: "PUT",
            body: JSON.stringify({ current_amount: Number(value) })
        });
        await loadGoals();
        showNotification(i18n.t("updatedSuccess"));
    } catch (error) {
        showNotification(i18n.translateApiError(error.detail, error.status), "error");
    }
}

async function deleteGoal(id) {
    try {
        await request(`/goals/${id}`, { method: "DELETE" });
        await loadGoals();
        showNotification(i18n.t("deletedSuccess"));
    } catch (error) {
        showNotification(i18n.translateApiError(error.detail, error.status), "error");
    }
}

async function loadDashboard() {
    $("operations-list").replaceChildren(createState(i18n.t("loadingOperations"), "loading-state"));
    // Budgets and goals load on their own and show their own errors: a failure
    // there must not hide fresh operations and balance behind an old snapshot.
    const secondary = Promise.all([loadBudgets(), loadGoals()]);
    let data;
    try {
        const [balance, statistics, operations] = await Promise.all([
            request("/balance"),
            request(`/statistics?period=${currentPeriod}`),
            request(`/operations?period=${currentPeriod}`)
        ]);
        data = { balance: balance.balance, ...statistics, operations };
    } catch (error) {
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

function resetForm() {
    editingOperationId = null;
    $("operation-form-title").textContent = i18n.t("newOperation");
    $("save-operation-btn").textContent = i18n.t("save");
    $("operation-amount").value = "";
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
    openForm();
}

async function deleteOperation(id) {
    if (!confirm(i18n.t("deleteConfirmation"))) return;
    try {
        await request(`/operations/${id}`, { method: "DELETE" });
        await loadDashboard();
        showNotification(i18n.t("deletedSuccess"));
    } catch (error) {
        showNotification(i18n.translateApiError(error.detail, error.status), "error");
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

async function loadUserSettings() {
    try {
        const user = await request("/auth/me");
        if ($("settings-email")) $("settings-email").textContent = user.email;
        if (user.settings) {
            if ($("settings-language")) $("settings-language").value = user.settings.language || "RU";
            if ($("settings-theme")) $("settings-theme").value = user.settings.theme || "light";
            if ($("settings-currency")) $("settings-currency").value = user.settings.currency || "KZT";

            // Sync local state with DB settings
            if (user.settings.language) changeLanguage(user.settings.language.toLowerCase());
            if (user.settings.theme) applyTheme(user.settings.theme);
            if (user.settings.currency) {
                currentCurrency = user.settings.currency;
                updateSymbol(CURRENCY_SYMBOLS[currentCurrency] || "₸");
            }
        }
    } catch (error) {
        console.error("Error loading user settings", error);
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
    fillCategorySelect($("import-default-income"), INCOME_CATEGORIES, $("import-default-income").value);
    fillCategorySelect($("import-default-expense"), EXPENSE_CATEGORIES, $("import-default-expense").value);
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
    setImportBusy(true);
    try {
        const data = await sendImport("/import/csv/preview", false);
        importState.columns = data.columns;
        importState.sample = data.sample;
        importState.currency = data.currency;
        setImportBusy(false);
        fillImportColumnSelects(useSuggestions ? data.suggested : null);
        renderImportSample();
        showImportStep("mapping");
    } catch (error) {
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
    setImportBusy(true);
    try {
        importState.preview = await sendImport("/import/csv/preview", true);
        setImportBusy(false);
        renderImportPreview();
        showImportStep("preview");
    } catch (error) {
        setImportBusy(false);
        showImportError(error);
    }
}

async function confirmImport() {
    setImportBusy(true);
    try {
        importState.report = await sendImport("/import/csv/commit", true);
        setImportBusy(false);
        renderImportReport();
        showImportStep("report");
    } catch (error) {
        setImportBusy(false);
        renderImportPreview();
        showImportError(error);
        return;
    }
    await loadDashboard();
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
    document.querySelectorAll(".language-button").forEach((button) => {
        button.onclick = () => changeLanguage(button.dataset.language);
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
    $("save-budget-btn").onclick = async () => {
        const payload = {
            category: $("budget-category").value,
            amount: Number($("budget-amount").value),
            period: "month"
        };
        try {
            await request("/budgets", {
                method: "POST",
                body: JSON.stringify(payload)
            });
            $("budget-form").classList.add("hidden");
            $("budget-amount").value = "";
            await loadBudgets();
            showNotification(i18n.t("addedSuccess"));
        } catch (error) {
            showNotification(i18n.translateApiError(error.detail, error.status), "error");
        }
    };

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
    $("save-goal-btn").onclick = async () => {
        const payload = {
            title: $("goal-title").value.trim(),
            target_amount: Number($("goal-target").value),
            current_amount: Number($("goal-current").value || 0),
            deadline: $("goal-deadline").value.trim() || null
        };
        try {
            await request("/goals", {
                method: "POST",
                body: JSON.stringify(payload)
            });
            $("goal-form").classList.add("hidden");
            $("goal-title").value = "";
            $("goal-target").value = "";
            $("goal-current").value = "0";
            $("goal-deadline").value = "";
            await loadGoals();
            showNotification(i18n.t("addedSuccess"));
        } catch (error) {
            showNotification(i18n.translateApiError(error.detail, error.status), "error");
        }
    };
    document.querySelectorAll(".type-tab").forEach((button) => {
        button.onclick = () => setFormType(button.dataset.operationType);
    });
    $("save-operation-btn").onclick = async () => {
        const wasEditing = editingOperationId !== null;
        const payload = {
            date: new Date().toLocaleString("ru-RU", { hour12: false }),
            operation_type: getFormType(),
            amount: Number($("operation-amount").value),
            category: $("operation-category").value || null
        };
        try {
            const res = await request(wasEditing ? `/operations/${editingOperationId}` : "/operations", {
                method: wasEditing ? "PUT" : "POST",
                body: JSON.stringify(payload)
            });
            resetForm();
            await loadDashboard();
            showNotification(res.notification || i18n.t(wasEditing ? "updatedSuccess" : "addedSuccess"));
        } catch (error) {
            showNotification(i18n.translateApiError(error.detail, error.status), "error");
        }
    };
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
    $("export-csv-btn").onclick = () => { window.location.href = `/export/csv?period=${currentPeriod}`; };
    $("export-excel-btn").onclick = () => { window.location.href = `/export/excel?period=${currentPeriod}`; };
    $("export-pdf-btn").onclick = () => { window.location.href = `/export/pdf?period=${currentPeriod}`; };
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("/service-worker.js");
    applyLanguage();

    // Auth wiring
    document.querySelectorAll(".auth-tab").forEach((tab) => {
        tab.onclick = () => {
            document.querySelectorAll(".auth-tab").forEach((t) => t.classList.remove("active"));
            tab.classList.add("active");
            authMode = tab.dataset.authMode;
            $("auth-submit-btn").textContent = authMode === "login" ? "Войти" : "Создать аккаунт";
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
            showNotification(authMode === "login" ? "Успешный вход!" : "Аккаунт успешно создан!");
            loadDashboard();
        } catch (error) {
            $("auth-error").textContent = error.detail || "Ошибка аутентификации";
        }
    };

    renderUserBadge();
    if (!localStorage.getItem(TOKEN_KEY)) {
        showAuthModal();
    } else {
        loadDashboard();
    }

    // Settings wiring
    if ($("save-settings-btn")) {
        $("save-settings-btn").onclick = async () => {
            try {
                await request("/auth/profile", {
                    method: "PUT",
                    body: JSON.stringify({
                        language: $("settings-language").value,
                        theme: $("settings-theme").value,
                        currency: $("settings-currency").value
                    })
                });
                applyLanguage();
                applyTheme($("settings-theme").value);
                currentCurrency = $("settings-currency").value;
                updateSymbol(CURRENCY_SYMBOLS[currentCurrency] || "₸");
                showNotification(i18n.t("addedSuccess")); // Reusing for success
            } catch (error) {
                showNotification(i18n.translateApiError(error.detail, error.status), "error");
            }
        };
    }
    if ($("change-password-btn")) {
        $("change-password-btn").onclick = async () => {
            try {
                await request("/auth/password", {
                    method: "PUT",
                    body: JSON.stringify({
                        current_password: $("current-password").value,
                        new_password: $("new-password").value
                    })
                });
                showNotification(i18n.t("passwordChangedSuccess"));
                $("current-password").value = "";
                $("new-password").value = "";
            } catch (error) {
                showNotification(i18n.translateApiError(error.detail, error.status), "error");
            }
        };
    }
    if ($("delete-account-btn")) {
        $("delete-account-btn").onclick = async () => {
            if (!confirm(i18n.t("deleteConfirmation"))) return;
            try {
                await request("/auth/delete", {
                    method: "DELETE",
                    body: JSON.stringify({ password: $("delete-password").value })
                });
                localStorage.removeItem(TOKEN_KEY);
                window.location.reload();
            } catch (error) {
                showNotification(i18n.translateApiError(error.detail, error.status), "error");
            }
        };
    }

    // Call settings load on login/load
    loadUserSettings();
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
