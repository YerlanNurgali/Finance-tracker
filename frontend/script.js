const API_URL = window.location.origin;
const LANGUAGE_KEY = "finance_tracker_language";
const LOCAL_DATA_KEY = "finance_tracker_data_v2";
const EXPENSE_CATEGORIES = ["Еда", "Транспорт", "Дом", "Развлечения", "Здоровье", "Другое"];
const INCOME_CATEGORIES = ["Зарплата", "Фриланс", "Бизнес", "Инвестиции", "Другое"];
const LOCALES = { kk: "kk-KZ", en: "en-US", ru: "ru-RU" };

let editingOperationId = null;
let currentPeriod = "all";
let currentDashboard = null;

const i18n = window.FinanceTrackerI18n;
const $ = (id) => document.getElementById(id);

function getStoredLanguage() {
    return i18n.SUPPORTED_LANGUAGES.includes(localStorage.getItem(LANGUAGE_KEY))
        ? localStorage.getItem(LANGUAGE_KEY)
        : i18n.DEFAULT_LANGUAGE;
}

function formatMoney(amount) {
    return `${new Intl.NumberFormat(LOCALES[i18n.getLanguage()]).format(Number(amount) || 0)} ₸`;
}

function formatDate(dateString) {
    const match = String(dateString).match(/^(\d{2})\.(\d{2})\.(\d{4})(?:,?\s+)(\d{2}):(\d{2})(?::(\d{2}))?$/);
    if (!match) return dateString;
    const date = new Date(Number(match[3]), Number(match[2]) - 1, Number(match[1]), Number(match[4]), Number(match[5]), Number(match[6] || 0));
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
    updateOperationTypeOptions();
    if (currentDashboard) renderDashboard(currentDashboard);
}

function updateOperationTypeOptions() {
    const select = $("operation-type");
    if (!select) return;
    const selected = select.value || "Доход";
    select.replaceChildren(
        new Option(i18n.translateOperationType("Доход"), "Доход"),
        new Option(i18n.translateOperationType("Расход"), "Расход")
    );
    select.value = selected;
}

function updateCategoryOptions(selectedValue = "") {
    const select = $("operation-category");
    const type = $("operation-type")?.value || "Доход";
    if (!select) return;
    const categories = type === "Доход" ? INCOME_CATEGORIES : EXPENSE_CATEGORIES;
    const values = categories.includes(selectedValue) || !selectedValue ? categories : [selectedValue, ...categories];
    select.replaceChildren(...values.map((value) => new Option(i18n.translateCategory(value), value)));
    select.value = selectedValue && values.includes(selectedValue) ? selectedValue : categories[0];
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

function renderCategories(categories) {
    const container = $("categories-list");
    container.replaceChildren();
    const entries = Object.entries(categories || {});
    if (!entries.length) {
        container.textContent = i18n.t("noCategoryExpenses");
        return;
    }
    const total = entries.reduce((sum, [, amount]) => sum + Number(amount), 0);
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
    const max = Math.max(...entries.map(([, value]) => Number(value)));
    const width = canvas.width / entries.length;
    entries.forEach(([name, value], index) => {
        const height = Number(value) / max * 150;
        context.fillStyle = "#2563eb";
        context.fillRect(index * width + 12, 180 - height, width - 24, height);
        context.fillStyle = "#374151";
        context.font = "12px sans-serif";
        context.fillText(i18n.translateCategory(name).slice(0, 12), index * width + 12, 198);
    });
}

function renderOperations(operations) {
    const container = $("operations-list");
    container.replaceChildren();
    if (!operations.length) {
        container.textContent = i18n.t("noOperations");
        return;
    }
    operations.forEach((operation) => {
        const element = document.createElement("div");
        element.className = "operation";
        const info = document.createElement("div");
        info.className = "operation-info";
        const type = document.createElement("span");
        type.className = "operation-type";
        type.textContent = i18n.translateOperationType(operation.type);
        const date = document.createElement("span");
        date.className = "operation-date";
        date.textContent = formatDate(operation.date);
        const category = document.createElement("span");
        category.className = "operation-category";
        category.textContent = operation.category ? i18n.t("categoryLabel", { category: i18n.translateCategory(operation.category) }) : "";
        info.append(type, date, category);

        const actions = document.createElement("div");
        actions.className = "operation-actions";
        const amount = document.createElement("strong");
        amount.className = `operation-amount ${operation.type === "Доход" ? "operation-income" : "operation-expense"}`;
        amount.textContent = `${operation.type === "Доход" ? "+" : "-"}${formatMoney(operation.amount)}`;
        const editButton = document.createElement("button");
        editButton.type = "button";
        editButton.className = "edit-operation-btn";
        editButton.textContent = "✏️";
        editButton.setAttribute("aria-label", i18n.t("editOperation"));
        editButton.onclick = () => editOperation(operation);
        const deleteButton = document.createElement("button");
        deleteButton.type = "button";
        deleteButton.className = "delete-operation-btn";
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
    $("total-income").textContent = formatMoney(data.total_income);
    $("total-expense").textContent = formatMoney(data.total_expense);
    const periodNames = { all: "allTime", today: "today", week: "week", month: "month" };
    $("period-summary-content").innerHTML = [
        `<p><strong>${i18n.t(periodNames[currentPeriod])}</strong></p>`,
        `<p>${i18n.t("periodIncome", { amount: formatMoney(data.total_income) })}</p>`,
        `<p>${i18n.t("periodExpense", { amount: formatMoney(data.total_expense) })}</p>`,
        `<p>${i18n.t("periodBalance", { amount: formatMoney(data.total_income - data.total_expense) })}</p>`,
        `<p>${i18n.t("operationCount", { count: data.operations.length })}</p>`
    ].join("");
    renderCategories(data.categories);
    renderChart(data.categories);
    renderOperations(data.operations);
}

async function loadDashboard() {
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
        } else {
            $("operations-list").textContent = error.detail
                ? i18n.translateApiError(error.detail, error.status)
                : i18n.t("loadError");
        }
    }
}

function resetForm() {
    editingOperationId = null;
    $("operation-form-title").textContent = i18n.t("newOperation");
    $("save-operation-btn").textContent = i18n.t("save");
    $("operation-form").classList.add("hidden");
    $("operation-type").value = "Доход";
    $("operation-amount").value = "";
    updateCategoryOptions();
}

function editOperation(operation) {
    editingOperationId = operation.id;
    $("operation-form-title").textContent = i18n.t("editOperation");
    $("save-operation-btn").textContent = i18n.t("saveChanges");
    $("operation-type").value = operation.type;
    updateCategoryOptions(operation.category || "");
    $("operation-amount").value = operation.amount;
    $("operation-form").classList.remove("hidden");
}

async function deleteOperation(id) {
    if (!confirm(i18n.t("deleteConfirmation"))) return;
    try {
        await request(`/operations/${id}`, { method: "DELETE" });
        await loadDashboard();
    } catch (error) {
        alert(i18n.translateApiError(error.detail, error.status));
    }
}

function changeLanguage(language) {
    i18n.setLanguage(language);
    localStorage.setItem(LANGUAGE_KEY, i18n.getLanguage());
    applyLanguage();
}

document.addEventListener("DOMContentLoaded", () => {
    i18n.setLanguage(getStoredLanguage());
    document.querySelectorAll(".language-button").forEach((button) => {
        button.onclick = () => changeLanguage(button.dataset.language);
    });
    $("add-operation-btn").onclick = () => {
        resetForm();
        $("operation-form").classList.remove("hidden");
    };
    $("cancel-operation-btn").onclick = resetForm;
    $("operation-type").onchange = () => updateCategoryOptions();
    $("save-operation-btn").onclick = async () => {
        const type = $("operation-type").value;
        const payload = {
            date: new Date().toLocaleString("ru-RU", { hour12: false }),
            operation_type: type,
            amount: Number($("operation-amount").value),
            category: $("operation-category").value || null
        };
        try {
            await request(editingOperationId ? `/operations/${editingOperationId}` : "/operations", {
                method: editingOperationId ? "PUT" : "POST",
                body: JSON.stringify(payload)
            });
            resetForm();
            await loadDashboard();
        } catch (error) {
            alert(i18n.translateApiError(error.detail, error.status));
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
    $("export-csv-btn").onclick = () => { window.location.href = "/export/csv"; };
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("/service-worker.js");
    applyLanguage();
    loadDashboard();
});
