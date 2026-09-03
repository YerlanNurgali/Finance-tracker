const API_URL = window.location.origin;

let editingOperationId = null;
let currentPeriod = "all";

const LOCAL_DATA_KEY = "finance_tracker_data";

function saveLocalData(data) {
    localStorage.setItem(
        LOCAL_DATA_KEY,
        JSON.stringify(data)
    );
}

function getLocalData() {
    const data = localStorage.getItem(LOCAL_DATA_KEY);

    if (!data) {
        return null;
    }

    return JSON.parse(data);
}

const saveOperationBtn =
    document.getElementById("save-operation-btn");

const operationFormTitle =
    document.getElementById("operation-form-title");

async function loadBalance() {
    const response = await fetch(`${API_URL}/balance`);

    if (!response.ok) {
        throw new Error("Не удалось загрузить баланс");
    }

    const data = await response.json();

    document.getElementById("balance").textContent =
        formatMoney(data.balance);
}


async function loadStatistics() {
    const response = await fetch(`${API_URL}/statistics`);

    if (!response.ok) {
        throw new Error("Не удалось загрузить статистику");
    }

    const data = await response.json();

    document.getElementById("total-income").textContent =
        formatMoney(data.total_income);

    document.getElementById("total-expense").textContent =
        formatMoney(data.total_expense);

    const categoriesContainer =
        document.getElementById("categories-list");

    categoriesContainer.innerHTML = "";

    const categories = data.categories;

    renderExpensesChart(categories);

    if (Object.keys(categories).length === 0) {

        categoriesContainer.innerHTML = `
            <p class="empty-message">
                Расходов по категориям пока нет.
            </p>
        `;

    } else {

        Object.entries(categories).forEach(([category, amount]) => {

            const element = document.createElement("div");

            element.className = "category-item";

            element.innerHTML = `
                <span class="category-name">
                    ${category}
                </span>

                <span class="category-amount">
                    -${formatMoney(amount)}
                </span>
            `;

        categoriesContainer.appendChild(element);
    });
}
}


async function loadOperations() {

    const response = await fetch(
        `${API_URL}/operations?period=${currentPeriod}`
    );

    if (!response.ok) {
        throw new Error("Не удалось загрузить операции");
    }

    const operations = await response.json();

    console.log("OPERATIONS:", operations);

    const container = document.getElementById("operations-list");

    console.log("LOAD OPERATIONS: container найден");

    container.innerHTML = "";

    if (operations.length === 0) {
        container.innerHTML = `
            <p class="empty-message">
                Операций пока нет.
            </p>
        `;

        return;
    }

    operations.forEach(operation => {

        console.log("OPERATION:", operation);
        
        const element = document.createElement("div");

        element.className = "operation";

        const sign = operation.type === "Доход" ? "+" : "-";

        const amountClass =
            operation.type === "Доход"
                ? "operation-income"
                : "operation-expense";

        element.innerHTML = `
            <div class="operation-info">

                <span class="operation-type">
                    ${operation.type}
                </span>

                <span class="operation-date">
                    ${operation.date}
                </span>

                ${
                    operation.category
                        ? `<span class="operation-category">
                            Категория: ${operation.category}
                           </span>`
                        : ""
                }

            </div>

            <div class="operation-actions">

                <div class="operation-amount ${amountClass}">
                    ${sign}${formatMoney(operation.amount)}
                </div>

                <button class="edit-operation-btn">
                    ✏️
                </button>

                <button class="delete-operation-btn">
                    🗑️
                </button>

            </div>
        `;

        const editButton =
            element.querySelector(".edit-operation-btn");

        editButton.addEventListener("click", () => {
            editOperation(operation);
        });

        const deleteButton =
            element.querySelector(".delete-operation-btn");

        deleteButton.addEventListener("click", () => {
            deleteOperation(operation.id);
        });

        container.appendChild(element);
    });
}


function formatMoney(amount) {
    return new Intl.NumberFormat("ru-RU").format(amount) + " ₸";
}

async function loadDashboardFromData(data) {

    document.getElementById("balance").textContent =
        formatMoney(data.balance);

    document.getElementById("total-income").textContent =
        formatMoney(data.total_income);

    document.getElementById("total-expense").textContent =
        formatMoney(data.total_expense);

    const categoriesContainer =
        document.getElementById("categories-list");

    categoriesContainer.innerHTML = "";

    const categories = data.categories || {};

    renderExpensesChart(categories);

    if (Object.keys(categories).length === 0) {

        categoriesContainer.innerHTML = `
            <p class="empty-message">
                Расходов по категориям пока нет.
            </p>
        `;

    } else {

        Object.entries(categories).forEach(([category, amount]) => {

            const element = document.createElement("div");

            element.className = "category-item";

            element.innerHTML = `
                <span class="category-name">
                    ${category}
                </span>

                <span class="category-amount">
                    -${formatMoney(amount)}
                </span>
            `;

            categoriesContainer.appendChild(element);
        });
    }

    const container =
        document.getElementById("operations-list");

    container.innerHTML = "";

    const operations = data.operations || [];

    if (operations.length === 0) {

        container.innerHTML = `
            <p class="empty-message">
                Операций пока нет.
            </p>
        `;

        return;
    }

    operations.forEach(operation => {

        const element = document.createElement("div");

        element.className = "operation";

        const sign =
            operation.type === "Доход" ? "+" : "-";

        const amountClass =
            operation.type === "Доход"
                ? "operation-income"
                : "operation-expense";

        element.innerHTML = `
            <div class="operation-info">

                <span class="operation-type">
                    ${operation.type}
                </span>

                <span class="operation-date">
                    ${operation.date}
                </span>

                ${
                    operation.category
                        ? `<span class="operation-category">
                            Категория: ${operation.category}
                           </span>`
                        : ""
                }

            </div>

            <div class="operation-actions">

                <div class="operation-amount ${amountClass}">
                    ${sign}${formatMoney(operation.amount)}
                </div>

                <button class="edit-operation-btn">
                    ✏️
                </button>

                <button class="delete-operation-btn">
                    🗑️
                </button>

            </div>
        `;

        const editButton =
            element.querySelector(".edit-operation-btn");

        editButton.addEventListener("click", () => {
            editOperation(operation);
        });

        const deleteButton =
            element.querySelector(".delete-operation-btn");

        deleteButton.addEventListener("click", () => {
            deleteOperation(operation.id);
        });

        container.appendChild(element);
    });
}

async function loadDashboard() {

    try {

        const [balanceResponse, statisticsResponse, operationsResponse] =
            await Promise.all([
                fetch(`${API_URL}/balance`),
                fetch(`${API_URL}/statistics`),
                fetch(`${API_URL}/operations?period=${currentPeriod}`)
            ]);

        if (
            !balanceResponse.ok ||
            !statisticsResponse.ok ||
            !operationsResponse.ok
        ) {
            throw new Error("Не удалось загрузить данные");
        }

        const balance = await balanceResponse.json();
        const statistics = await statisticsResponse.json();
        const operations = await operationsResponse.json();

        const dashboardData = {
            balance: balance.balance,
            total_income: statistics.total_income,
            total_expense: statistics.total_expense,
            categories: statistics.categories,
            operations: operations
        };

        saveLocalData(dashboardData);

        console.log("Данные сохранены локально:", dashboardData);

        await loadDashboardFromData(dashboardData);

    } catch (error) {

        console.warn(
            "API недоступен. Загружаем локальные данные."
        );

        const localData = getLocalData();

        if (localData) {
            await loadDashboardFromData(localData);
        } else {
            document.getElementById("operations-list").innerHTML = `
                <p class="empty-message">
                    Нет сохранённых данных.
                </p>
            `;
        }
    }
}


async function deleteOperation(operationId) {

    const confirmed = confirm(
        "Вы действительно хотите удалить эту операцию?"
    );

    if (!confirmed) {
        return;
    }

    try {

        const response = await fetch(
            `${API_URL}/operations/${operationId}`,
            {
                method: "DELETE"
            }
        );

        if (!response.ok) {
            throw new Error("Не удалось удалить операцию");
        }

        await loadDashboard();

    } catch (error) {

        console.error(error);

        alert("Ошибка при удалении операции");
    }
}


function editOperation(operation) {

    editingOperationId = operation.id;

    document.getElementById("operation-form-title").textContent =
        "Редактировать операцию";

    document.getElementById("save-operation-btn").textContent =
        "Сохранить изменения";
    
    const operationForm =
        document.getElementById("operation-form");

    const operationType =
        document.getElementById("operation-type");

    const operationAmount =
        document.getElementById("operation-amount");

    const operationCategory =
        document.getElementById("operation-category");

    operationType.value = operation.type;
    operationAmount.value = operation.amount;

    if (operation.category) {
        operationCategory.value = operation.category;
    }

    operationForm.classList.remove("hidden");
}

document.addEventListener("DOMContentLoaded", () => {

    loadDashboard();

    const addOperationBtn =
        document.getElementById("add-operation-btn");

    const operationForm =
        document.getElementById("operation-form");

    const cancelOperationBtn =
        document.getElementById("cancel-operation-btn");


    addOperationBtn.addEventListener("click", () => {
        operationForm.classList.remove("hidden");
    });


    cancelOperationBtn.addEventListener("click", () => {
        operationForm.classList.add("hidden");

        editingOperationId = null;

        document.getElementById("operation-amount").value = "";

        document.getElementById("operation-form-title").textContent =
            "Новая операция";

        document.getElementById("save-operation-btn").textContent =
            "Сохранить";
    });


    saveOperationBtn.addEventListener("click", async () => {

        const type =
            document.getElementById("operation-type").value;

        const amount =
            Number(document.getElementById("operation-amount").value);

        const category =
            document.getElementById("operation-category").value;

        const operation = {
            date: new Date().toLocaleString("ru-RU"),
            operation_type: type,
            amount: amount,
            category: type === "Расход" ? category : null
        };

        try {

            let response;

            if (editingOperationId !== null) {

                response = await fetch(
                    `${API_URL}/operations/${editingOperationId}`,
                    {
                        method: "PUT",
                        headers: {
                            "Content-Type": "application/json"
                        },
                        body: JSON.stringify(operation)
                    }
                );

            } else {

                response = await fetch(
                    `${API_URL}/operations`,
                    {
                        method: "POST",
                        headers: {
                            "Content-Type": "application/json"
                        },
                        body: JSON.stringify(operation)
                    }
                );
            }

            if (!response.ok) {
                throw new Error("Не удалось добавить операцию");
            }

            operationForm.classList.add("hidden");

            editingOperationId = null;

            document.getElementById("operation-amount").value = "";

            document.getElementById("operation-category").value = "Еда";

            document.getElementById("operation-type").value = "Доход";

            document.getElementById("operation-form-title").textContent =
                "Новая операция";

            document.getElementById("save-operation-btn").textContent =
                "Сохранить";

            await loadDashboard();

        } catch (error) {

            console.error(error);

            alert("Ошибка при добавлении операции");
        }
    });

    const filterButtons =
    document.querySelectorAll(".filter-btn");

filterButtons.forEach(button => {

    button.addEventListener("click", async () => {

        currentPeriod = button.dataset.period;

        filterButtons.forEach(btn => {
            btn.classList.remove("active");
        });

        button.classList.add("active");

        await loadOperations();
    });

});

});

let expensesChart = null;

function renderExpensesChart(categories) {

    const canvas = document.getElementById("expenses-chart");

    if (!canvas) {
        return;
    }

    const labels = Object.keys(categories);
    const values = Object.values(categories);

    if (expensesChart) {
        expensesChart.destroy();
    }

    expensesChart = new Chart(canvas, {
        type: "doughnut",

        data: {
            labels: labels,

            datasets: [{
                data: values
            }]
        },

        options: {
            responsive: true,

            plugins: {
                legend: {
                    position: "bottom"
                }
            }
        }
    });
}

if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
        navigator.serviceWorker
            .register("/service-worker.js")
            .then(registration => {
                console.log(
                    "Service Worker зарегистрирован:",
                    registration.scope
                );
            })
            .catch(error => {
                console.error(
                    "Ошибка регистрации Service Worker:",
                    error
                );
            });
    });
}