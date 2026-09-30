const API_URL = window.location.origin;
let editingOperationId = null;
let currentPeriod = "all";
const LOCAL_DATA_KEY = "finance_tracker_data_v2";
const EXPENSE_CATEGORIES = ["Еда", "Транспорт", "Дом", "Развлечения", "Здоровье", "Другое"];
const INCOME_CATEGORIES = ["Зарплата", "Фриланс", "Бизнес", "Инвестиции", "Другое"];

const $ = (id) => document.getElementById(id);
const formatMoney = (amount) => `${new Intl.NumberFormat("ru-RU").format(Number(amount) || 0)} ₸`;
const localData = () => { try { return JSON.parse(localStorage.getItem(LOCAL_DATA_KEY)); } catch (_) { return null; } };
const saveLocalData = (data) => localStorage.setItem(LOCAL_DATA_KEY, JSON.stringify(data));

async function request(path, options = {}) {
    const response = await fetch(`${API_URL}${path}`, { headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options });
    if (!response.ok) { let message = "Ошибка запроса"; try { message = (await response.json()).detail || message; } catch (_) {} throw new Error(message); }
    return response.json();
}

function renderCategories(categories) {
    const container = $("categories-list"); container.replaceChildren();
    const entries = Object.entries(categories || {});
    if (!entries.length) { container.textContent = "Расходов по категориям пока нет."; return; }
    const total = entries.reduce((sum, [, amount]) => sum + Number(amount), 0);
    entries.forEach(([name, amount]) => {
        const item = document.createElement("div"); item.className = "category-item";
        item.innerHTML = `<span class="category-name"></span><span class="category-amount"></span>`;
        item.querySelector(".category-name").textContent = name;
        item.querySelector(".category-amount").textContent = `-${formatMoney(amount)} (${Math.round(amount / total * 100)}%)`;
        container.appendChild(item);
    });
}

function renderChart(categories) {
    const canvas = $("expenses-chart"); if (!canvas || !canvas.getContext) return;
    const context = canvas.getContext("2d"); context.clearRect(0, 0, canvas.width, canvas.height);
    const entries = Object.entries(categories || {}); if (!entries.length) return;
    const max = Math.max(...entries.map(([, value]) => Number(value))); const width = canvas.width / entries.length;
    entries.forEach(([name, value], index) => { const height = Number(value) / max * 150; context.fillStyle = "#2563eb"; context.fillRect(index * width + 12, 180 - height, width - 24, height); context.fillStyle = "#374151"; context.font = "12px sans-serif"; context.fillText(name.slice(0, 12), index * width + 12, 198); });
}

function renderOperations(operations) {
    const container = $("operations-list"); container.replaceChildren();
    if (!operations.length) { container.textContent = "Операций пока нет."; return; }
    operations.forEach((operation) => {
        const element = document.createElement("div"); element.className = "operation";
        const info = document.createElement("div"); info.className = "operation-info";
        info.innerHTML = `<span class="operation-type"></span><span class="operation-date"></span><span class="operation-category"></span>`;
        info.querySelector(".operation-type").textContent = operation.type;
        info.querySelector(".operation-date").textContent = operation.date;
        info.querySelector(".operation-category").textContent = operation.category ? `Категория: ${operation.category}` : "";
        const actions = document.createElement("div"); actions.className = "operation-actions";
        actions.innerHTML = `<strong class="operation-amount"></strong><button type="button" class="edit-operation-btn" aria-label="Редактировать">✏️</button><button type="button" class="delete-operation-btn" aria-label="Удалить">🗑️</button>`;
        const amount = actions.querySelector(".operation-amount"); amount.textContent = `${operation.type === "Доход" ? "+" : "-"}${formatMoney(operation.amount)}`; amount.classList.add(operation.type === "Доход" ? "operation-income" : "operation-expense");
        actions.querySelector(".edit-operation-btn").onclick = () => editOperation(operation);
        actions.querySelector(".delete-operation-btn").onclick = () => deleteOperation(operation.id);
        element.append(info, actions); container.appendChild(element);
    });
}

function renderDashboard(data) {
    $("balance").textContent = formatMoney(data.balance); $("total-income").textContent = formatMoney(data.total_income); $("total-expense").textContent = formatMoney(data.total_expense);
    $("period-summary-content").innerHTML = `<p><strong>${({all:"За всё время", today:"Сегодня", week:"За неделю", month:"За месяц"})[currentPeriod]}</strong></p><p>Доходы: ${formatMoney(data.total_income)}</p><p>Расходы: ${formatMoney(data.total_expense)}</p><p>Баланс: ${formatMoney(data.total_income - data.total_expense)}</p><p>Операций: ${data.operations.length}</p>`;
    renderCategories(data.categories); renderChart(data.categories); renderOperations(data.operations);
}

async function loadDashboard() {
    try { const [balance, statistics, operations] = await Promise.all([request("/balance"), request(`/statistics?period=${currentPeriod}`), request(`/operations?period=${currentPeriod}`)]); const data = { balance: balance.balance, ...statistics, operations }; saveLocalData(data); renderDashboard(data); }
    catch (error) { const data = localData(); if (data) renderDashboard(data); else $("operations-list").textContent = `Не удалось загрузить данные: ${error.message}`; }
}

function resetForm() { editingOperationId = null; $("operation-form-title").textContent = "Новая операция"; $("save-operation-btn").textContent = "Сохранить"; $("operation-form").classList.add("hidden"); $("operation-amount").value = ""; }
function editOperation(operation) { editingOperationId = operation.id; $("operation-form-title").textContent = "Редактировать операцию"; $("save-operation-btn").textContent = "Сохранить изменения"; $("operation-type").value = operation.type; updateCategoryOptions(); $("operation-amount").value = operation.amount; $("operation-category").value = operation.category || ""; $("operation-form").classList.remove("hidden"); }
async function deleteOperation(id) { if (!confirm("Удалить операцию?")) return; try { await request(`/operations/${id}`, { method: "DELETE" }); await loadDashboard(); } catch (error) { alert(error.message); } }

document.addEventListener("DOMContentLoaded", () => {
    loadDashboard(); $("add-operation-btn").onclick = () => { resetForm(); $("operation-form").classList.remove("hidden"); updateCategoryOptions(); }; $("cancel-operation-btn").onclick = resetForm;
    $("operation-type").onchange = updateCategoryOptions;
    $("save-operation-btn").onclick = async () => { const type = $("operation-type").value; const payload = { date: new Date().toLocaleString("ru-RU", { hour12: false }), operation_type: type, amount: Number($("operation-amount").value), category: $("operation-category").value || null }; try { await request(editingOperationId ? `/operations/${editingOperationId}` : "/operations", { method: editingOperationId ? "PUT" : "POST", body: JSON.stringify(payload) }); resetForm(); await loadDashboard(); } catch (error) { alert(error.message); } };
    document.querySelectorAll(".filter-btn").forEach((button) => button.onclick = () => { document.querySelectorAll(".filter-btn").forEach((item) => item.classList.remove("active")); button.classList.add("active"); currentPeriod = button.dataset.period; loadDashboard(); }); $("export-csv-btn").onclick = () => { window.location.href = "/export/csv"; };
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("/service-worker.js");
});

function updateCategoryOptions() {
    const type = $("operation-type").value;
    const selected = $("operation-category").value;
    $("operation-category").replaceChildren(...(type === "Доход" ? INCOME_CATEGORIES : EXPENSE_CATEGORIES).map((category) => new Option(category, category)));
    if ([...$("operation-category").options].some((option) => option.value === selected)) $("operation-category").value = selected;
}
