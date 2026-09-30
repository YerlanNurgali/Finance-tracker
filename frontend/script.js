const API_URL = window.location.origin;
const LANGUAGE_KEY = "finance_tracker_language";
const THEME_KEY = "finance_tracker_theme";
const LOCAL_DATA_KEY = "finance_tracker_data_v2";
const EXPENSE_CATEGORIES = ["Еда", "Транспорт", "Дом", "Развлечения", "Здоровье", "Другое"];
const INCOME_CATEGORIES = ["Зарплата", "Фриланс", "Бизнес", "Инвестиции", "Другое"];
const LOCALES = { kk: "kk-KZ", en: "en-US", ru: "ru-RU" };
const VALID_THEMES = ["light", "dark"];

let editingOperationId = null;
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
    return `${new Intl.NumberFormat(LOCALES[i18n.getLanguage()]).format(Number(amount) || 0)} ₸`;
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
    localStorage.setItem(LOCAL_DATA_KEY, JSON.stringify(data));
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

async function request(path, options = {}) {
    const response = await fetch(`${API_URL}${path}`, {
        headers: { "Content-Type": "application/json", ...(options.headers || {}) },
        ...options
    });
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
        `<p class="muted">${i18n.t("operationCount", { count: data.operations.length })}</p>`
    ].join("");
    renderCategories(data.categories);
    renderChart(data.categories);
    renderOperations(data.operations);
    updateCategoryFilterOptions();
}

async function loadDashboard() {
    $("operations-list").replaceChildren(createState(i18n.t("loadingOperations"), "loading-state"));
    try {
        const [balance, statistics, operations] = await Promise.all([
            request("/balance"),
            request(`/statistics?period=${currentPeriod}`),
            request(`/operations?period=${currentPeriod}`)
        ]);
        currentDashboard = { balance: balance.balance, ...statistics, operations };
        saveLocalData(currentDashboard);
        renderDashboard(currentDashboard);
    } catch (error) {
        const data = localData();
        if (data) {
            currentDashboard = data;
            renderDashboard(data);
            showNotification(i18n.t("loadError"), "error");
        } else {
            $("operations-list").replaceChildren(createState(i18n.t("loadError"), "error-state"));
            showNotification(i18n.translateApiError(error.detail, error.status), "error");
        }
    }
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

document.addEventListener("DOMContentLoaded", () => {
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
            await request(wasEditing ? `/operations/${editingOperationId}` : "/operations", {
                method: wasEditing ? "PUT" : "POST",
                body: JSON.stringify(payload)
            });
            resetForm();
            await loadDashboard();
            showNotification(i18n.t(wasEditing ? "updatedSuccess" : "addedSuccess"));
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
    $("export-csv-btn").onclick = () => { window.location.href = "/export/csv"; };
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("/service-worker.js");
    applyLanguage();
    loadDashboard();
});
