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

test("every static i18n key used in the frontend exists in all languages", () => {
    const fs = require("node:fs");
    const path = require("node:path");
    const frontend = path.join(__dirname, "..", "frontend");
    const used = new Set();
    for (const file of ["index.html", "script.js"]) {
        const source = fs.readFileSync(path.join(frontend, file), "utf8");
        for (const match of source.matchAll(/data-i18n(?:-[a-z-]+)?=["']([^"'$]+)["']/g)) used.add(match[1]);
        for (const match of source.matchAll(/i18n\.t\(\s*["']([^"']+)["']/g)) used.add(match[1]);
    }
    assert.ok(used.size > 0);

    const translations = i18n.getTranslations();
    for (const language of i18n.SUPPORTED_LANGUAGES) {
        const missing = [...used].filter((key) => !(key in translations[language])).sort();
        assert.deepEqual(missing, [], `missing ${language} translations`);
    }
});

test("the sign-in, sign-out, navigation and placeholder texts are translated into every language", () => {
    const translations = i18n.getTranslations();
    const keys = ["loginTab", "registerTab", "passwordLabel", "signIn", "signOut", "signInOrRegister", "createAccount",
        "loginSuccess", "accountCreated", "userExists", "navigationLabel", "budgetLimitPlaceholder", "goalTitlePlaceholder",
        "goalDeadlinePlaceholder", "importButton"];
    for (const key of keys) {
        for (const language of i18n.SUPPORTED_LANGUAGES) {
            const text = translations[language][key];
            assert.ok(text && text !== key, `${language}.${key} is translated`);
        }
        assert.notEqual(translations.en[key], translations.ru[key], `en.${key} differs from ru`);
        assert.notEqual(translations.kk[key], translations.ru[key], `kk.${key} differs from ru`);
    }
    i18n.setLanguage("en");
    assert.equal(i18n.translateApiError("Пользователь с таким email уже существует", 400), "A user with this email already exists");
    assert.equal(i18n.translateApiError("Неверный email или пароль", 401), "Incorrect email or password");
    i18n.setLanguage("ru");
});

test("the languages are offered in the order KZ | EN | RU", () => {
    const fs = require("node:fs");
    const path = require("node:path");
    assert.deepEqual(i18n.SUPPORTED_LANGUAGES, ["kk", "en", "ru"]);
    const html = fs.readFileSync(path.join(__dirname, "..", "frontend", "index.html"), "utf8");
    assert.deepEqual([...html.matchAll(/class="language-button"[^>]*data-language="(\w+)"/g)].map((match) => match[1]), ["kk", "en", "ru"]);
    const select = html.match(/<select id="settings-language">([\s\S]*?)<\/select>/)[1];
    assert.deepEqual([...select.matchAll(/value="(\w+)"/g)].map((match) => match[1]), ["KZ", "EN", "RU"]);
});

test("no visible text is written in Russian outside the translation files", () => {
    const fs = require("node:fs");
    const path = require("node:path");
    const frontend = path.join(__dirname, "..", "frontend");
    const cyrillic = /[А-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі]/;

    // index.html: Cyrillic text needs a data-i18n on its own element; placeholders and aria-labels
    // need data-i18n-placeholder / data-i18n-aria-label. Two exceptions: the language names, which are
    // written in their own language on purpose, and the sign-in button, whose label script.js sets.
    const html = fs.readFileSync(path.join(frontend, "index.html"), "utf8");
    const problems = [];
    for (const match of html.matchAll(/<([a-z0-9]+)([^>]*)>([^<]*)/g)) {
        const [, tag, attributes, text] = match;
        if (cyrillic.test(text) && !/data-i18n(?!-)/.test(attributes)
            && !(tag === "option" && /value="(KZ|EN|RU)"/.test(attributes)) && !/id="auth-submit-btn"/.test(attributes)) {
            problems.push(`<${tag}> text: ${text.trim().slice(0, 40)}`);
        }
        for (const [name, marker] of [["placeholder", "data-i18n-placeholder"], ["aria-label", "data-i18n-aria-label"]]) {
            const value = attributes.match(new RegExp(`(?:^|\\s)${name}="([^"]*)"`));
            if (value && cyrillic.test(value[1]) && !attributes.includes(marker)) problems.push(`<${tag}> ${name}: ${value[1].slice(0, 40)}`);
        }
    }
    assert.deepEqual(problems, [], "Russian text in index.html without a translation key");

    // script.js: the only Russian strings are the canonical values the API uses (operation types and
    // categories), the server messages that must not sign the user out, and one fallback text.
    const allowed = new Set(["Доход", "Расход", "Другое", "Еда", "Транспорт", "Дом", "Развлечения", "Здоровье", "Зарплата",
        "Фриланс", "Бизнес", "Инвестиции", "Неверный текущий пароль", "Неверный пароль", "Неверный email или пароль",
        "Недостаточно данных"]);
    const source = fs.readFileSync(path.join(frontend, "script.js"), "utf8").split("\n")
        .map((line) => line.replace(/^\s*\/\/.*$/, "").replace(/\s\/\/\s.*$/, "")).join("\n");
    const found = [...source.matchAll(/(["'`])((?:\\.|(?!\1)[^\\\n])*)\1/g)]
        .map((match) => match[2].replace(/\$\{(?:[^{}]|\{[^{}]*\})*\}/g, ""))  // code inside ${...} is not text
        .filter((text) => cyrillic.test(text) && !allowed.has(text));
    assert.deepEqual(found, [], "Russian string literals in script.js outside the allowed canonical values");
});
