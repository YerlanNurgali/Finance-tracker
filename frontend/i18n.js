(function (root, factory) {
    if (typeof module === "object" && module.exports) {
        module.exports = factory();
    } else {
        root.FinanceTrackerI18n = factory();
    }
}(typeof self !== "undefined" ? self : this, function () {
    const SUPPORTED_LANGUAGES = ["kk", "en", "ru"];
    const DEFAULT_LANGUAGE = "ru";

    const translations = {
        ru: {
            appName: "Finance Tracker",
            appDescription: "Управляй своими финансами",
            languageSelector: "Выбор языка",
            expensesChart: "График расходов по категориям",
            currentBalance: "Текущий баланс",
            income: "Доходы",
            expense: "Расходы",
            periodSummary: "Сводка за период",
            allTime: "За всё время",
            today: "Сегодня",
            week: "Неделя",
            month: "Месяц",
            expensesByCategory: "Расходы по категориям",
            operationHistory: "История операций",
            add: "+ Добавить",
            newOperation: "Новая операция",
            editOperation: "Редактировать операцию",
            operationType: "Тип операции",
            amount: "Сумма",
            category: "Категория",
            enterAmount: "Введите сумму",
            save: "Сохранить",
            saveChanges: "Сохранить изменения",
            cancel: "Отмена",
            deleteOperation: "Удалить операцию",
            exportCsv: "Экспорт CSV",
            loading: "Загрузка...",
            loadingCategories: "Загрузка категорий...",
            loadingOperations: "Загрузка операций...",
            noCategoryExpenses: "Расходов по категориям пока нет.",
            noOperations: "Операций пока нет.",
            noSavedData: "Нет сохранённых данных.",
            requestError: "Ошибка запроса",
            loadError: "Не удалось загрузить данные",
            deleteConfirmation: "Удалить операцию?",
            deleteError: "Ошибка при удалении операции",
            categoryLabel: "Категория: {category}",
            incomeType: "Доход",
            expenseType: "Расход",
            operationCount: "Операций: {count}",
            periodIncome: "Доходы: {amount}",
            periodExpense: "Расходы: {amount}",
            periodBalance: "Баланс: {amount}",
            salary: "Зарплата",
            freelance: "Фриланс",
            business: "Бизнес",
            investments: "Инвестиции",
            food: "Еда",
            transport: "Транспорт",
            home: "Дом",
            entertainment: "Развлечения",
            health: "Здоровье",
            other: "Другое",
            positiveAmountRequired: "Сумма должна быть больше нуля",
            expenseCategoryRequired: "Для расхода нужна категория",
            invalidOperationType: "Недопустимый тип операции",
            operationNotFound: "Операция не найдена",
            status400: "Проверьте введённые данные",
            status404: "Операция не найдена",
            status422: "Проверьте введённые данные",
            status500: "Ошибка сервера",
            unknownError: "Произошла неизвестная ошибка"
        },
        en: {
            appName: "Finance Tracker",
            appDescription: "Manage your finances",
            languageSelector: "Language selector",
            expensesChart: "Expenses by category chart",
            currentBalance: "Current balance",
            income: "Income",
            expense: "Expenses",
            periodSummary: "Period summary",
            allTime: "All time",
            today: "Today",
            week: "Week",
            month: "Month",
            expensesByCategory: "Expenses by category",
            operationHistory: "Operation history",
            add: "+ Add",
            newOperation: "New operation",
            editOperation: "Edit operation",
            operationType: "Operation type",
            amount: "Amount",
            category: "Category",
            enterAmount: "Enter amount",
            save: "Save",
            saveChanges: "Save changes",
            cancel: "Cancel",
            deleteOperation: "Delete operation",
            exportCsv: "Export CSV",
            loading: "Loading...",
            loadingCategories: "Loading categories...",
            loadingOperations: "Loading operations...",
            noCategoryExpenses: "No categorized expenses yet.",
            noOperations: "No operations yet.",
            noSavedData: "No saved data.",
            requestError: "Request error",
            loadError: "Failed to load data",
            deleteConfirmation: "Delete this operation?",
            deleteError: "Failed to delete the operation",
            categoryLabel: "Category: {category}",
            incomeType: "Income",
            expenseType: "Expense",
            operationCount: "Operations: {count}",
            periodIncome: "Income: {amount}",
            periodExpense: "Expenses: {amount}",
            periodBalance: "Balance: {amount}",
            salary: "Salary",
            freelance: "Freelance",
            business: "Business",
            investments: "Investments",
            food: "Food",
            transport: "Transport",
            home: "Home",
            entertainment: "Entertainment",
            health: "Health",
            other: "Other",
            positiveAmountRequired: "Amount must be greater than zero",
            expenseCategoryRequired: "A category is required for an expense",
            invalidOperationType: "Invalid operation type",
            operationNotFound: "Operation not found",
            status400: "Please check the entered data",
            status404: "Operation not found",
            status422: "Please check the entered data",
            status500: "Server error",
            unknownError: "An unknown error occurred"
        },
        kk: {
            appName: "Finance Tracker",
            appDescription: "Қаржыңызды басқарыңыз",
            languageSelector: "Тілді таңдау",
            expensesChart: "Санаттар бойынша шығыстар графигі",
            currentBalance: "Ағымдағы баланс",
            income: "Кірістер",
            expense: "Шығыстар",
            periodSummary: "Кезең қорытындысы",
            allTime: "Барлық уақыт",
            today: "Бүгін",
            week: "Апта",
            month: "Ай",
            expensesByCategory: "Санаттар бойынша шығыстар",
            operationHistory: "Операциялар тарихы",
            add: "+ Қосу",
            newOperation: "Жаңа операция",
            editOperation: "Операцияны өңдеу",
            operationType: "Операция түрі",
            amount: "Сома",
            category: "Санат",
            enterAmount: "Соманы енгізіңіз",
            save: "Сақтау",
            saveChanges: "Өзгерістерді сақтау",
            cancel: "Бас тарту",
            deleteOperation: "Операцияны өшіру",
            exportCsv: "CSV экспорттау",
            loading: "Жүктелуде...",
            loadingCategories: "Санаттар жүктелуде...",
            loadingOperations: "Операциялар жүктелуде...",
            noCategoryExpenses: "Санатталған шығыстар әзірге жоқ.",
            noOperations: "Әзірге операциялар жоқ.",
            noSavedData: "Сақталған деректер жоқ.",
            requestError: "Сұрау қатесі",
            loadError: "Деректерді жүктеу мүмкін болмады",
            deleteConfirmation: "Операцияны өшіру керек пе?",
            deleteError: "Операцияны өшіру мүмкін болмады",
            categoryLabel: "Санат: {category}",
            incomeType: "Кіріс",
            expenseType: "Шығыс",
            operationCount: "Операциялар: {count}",
            periodIncome: "Кірістер: {amount}",
            periodExpense: "Шығыстар: {amount}",
            periodBalance: "Баланс: {amount}",
            salary: "Жалақы",
            freelance: "Фриланс",
            business: "Бизнес",
            investments: "Инвестициялар",
            food: "Тамақ",
            transport: "Көлік",
            home: "Үй",
            entertainment: "Ойын-сауық",
            health: "Денсаулық",
            other: "Басқа",
            positiveAmountRequired: "Сома нөлден үлкен болуы керек",
            expenseCategoryRequired: "Шығыс үшін санат қажет",
            invalidOperationType: "Операция түрі жарамсыз",
            operationNotFound: "Операция табылмады",
            status400: "Енгізілген деректерді тексеріңіз",
            status404: "Операция табылмады",
            status422: "Енгізілген деректерді тексеріңіз",
            status500: "Сервер қатесі",
            unknownError: "Белгісіз қате орын алды"
        }
    };

    const operationTypeKeys = {
        "Доход": "incomeType",
        "Расход": "expenseType"
    };

    const categoryKeys = {
        "Зарплата": "salary",
        "Фриланс": "freelance",
        "Бизнес": "business",
        "Инвестиции": "investments",
        "Еда": "food",
        "Транспорт": "transport",
        "Дом": "home",
        "Развлечения": "entertainment",
        "Здоровье": "health",
        "Другое": "other"
    };

    const apiErrorKeys = {
        "Сумма должна быть больше нуля": "positiveAmountRequired",
        "Для расхода нужна категория": "expenseCategoryRequired",
        "Недопустимый тип операции": "invalidOperationType",
        "Операция не найдена": "operationNotFound"
    };

    let currentLanguage = DEFAULT_LANGUAGE;

    function setLanguage(language) {
        currentLanguage = SUPPORTED_LANGUAGES.includes(language) ? language : DEFAULT_LANGUAGE;
        return currentLanguage;
    }

    function getLanguage() {
        return currentLanguage;
    }

    function t(key, values = {}) {
        const value = translations[currentLanguage][key] || translations[DEFAULT_LANGUAGE][key] || key;
        return value.replace(/\{(\w+)\}/g, (_, name) => values[name] === undefined ? `{${name}}` : values[name]);
    }

    function translateOperationType(value) {
        return operationTypeKeys[value] ? t(operationTypeKeys[value]) : value;
    }

    function translateCategory(value) {
        return categoryKeys[value] ? t(categoryKeys[value]) : value;
    }

    function translateApiError(detail, status) {
        if (detail && apiErrorKeys[detail]) return t(apiErrorKeys[detail]);
        const statusKey = `status${status}`;
        return translations[currentLanguage][statusKey] ? t(statusKey) : t("unknownError");
    }

    function getTranslations() {
        return translations;
    }

    return {
        DEFAULT_LANGUAGE,
        SUPPORTED_LANGUAGES,
        apiErrorKeys,
        categoryKeys,
        getLanguage,
        getTranslations,
        setLanguage,
        t,
        translateApiError,
        translateCategory,
        translateOperationType
    };
}));
