const assert = require("node:assert/strict");
const test = require("node:test");
const i18n = require("../frontend/i18n.js");

test("all translations expose the same keys", () => {
    const translations = i18n.getTranslations();
    const keys = Object.keys(translations.ru).sort();
    for (const language of i18n.SUPPORTED_LANGUAGES) {
        assert.deepEqual(Object.keys(translations[language]).sort(), keys);
    }
});

test("canonical operation types and categories are localized", () => {
    i18n.setLanguage("kk");
    assert.equal(i18n.translateOperationType("Доход"), "Кіріс");
    assert.equal(i18n.translateOperationType("Расход"), "Шығыс");
    assert.equal(i18n.translateCategory("Зарплата"), "Жалақы");
    assert.equal(i18n.translateCategory("Еда"), "Тамақ");
    assert.equal(i18n.translateCategory("Транспорт"), "Көлік");

    i18n.setLanguage("en");
    assert.equal(i18n.translateOperationType("Доход"), "Income");
    assert.equal(i18n.translateCategory("Зарплата"), "Salary");
});

test("expenses chart label is localized", () => {
    i18n.setLanguage("ru");
    assert.equal(i18n.t("expensesChart"), "График расходов по категориям");
    i18n.setLanguage("en");
    assert.equal(i18n.t("expensesChart"), "Expenses by category chart");
    i18n.setLanguage("kk");
    assert.equal(i18n.t("expensesChart"), "Санаттар бойынша шығыстар графигі");
});

test("unknown canonical values are preserved", () => {
    i18n.setLanguage("en");
    assert.equal(i18n.translateOperationType("Новый тип"), "Новый тип");
    assert.equal(i18n.translateCategory("Новая категория"), "Новая категория");
});

test("API errors are localized and invalid language falls back to Russian", () => {
    i18n.setLanguage("en");
    assert.equal(i18n.translateApiError("Сумма должна быть больше нуля"), "Amount must be greater than zero");
    assert.equal(i18n.translateApiError("unknown", 500), "Server error");
    assert.equal(i18n.setLanguage("unknown"), "ru");
    assert.equal(i18n.getLanguage(), "ru");
});
