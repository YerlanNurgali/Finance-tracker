// Dashboard rendering: runs the real frontend/i18n.js and frontend/script.js in
// a node:vm context with a minimal DOM stub and a mocked fetch. No network,
// no database: every API response below is synthetic test data.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const FRONTEND = path.join(__dirname, "..", "frontend");
const SOURCES = ["i18n.js", "script.js"].map((file) => fs.readFileSync(path.join(FRONTEND, file), "utf8"));

const DASHBOARD_IDS = [
    "operations-list", "balance", "summary-balance", "total-income", "total-expense",
    "period-summary-content", "categories-list", "expenses-chart", "trend-chart",
    "calendar-grid", "calendar-details", "operation-type-filter", "operation-category-filter",
    "operation-search", "budgets-list", "goals-list", "notification", "notification-message"
];
const IMPORT_IDS = [
    "import-modal", "import-csv-btn", "import-error", "import-report", "import-report-errors",
    "import-summary", "import-preview-table", "import-preview-more", "import-errors", "import-confirm-btn",
    "import-delimiter", "import-has-header", "import-date-format", "import-amount-mode", "import-decimal",
    "import-invert-sign", "import-income-values", "import-expense-values", "import-default-income",
    "import-default-expense", "import-date-column", "import-description-column", "import-category-column",
    "import-amount-column", "import-type-column", "import-income-column", "import-expense-column",
    "import-currency-column", "import-reference-column"
];

class Element {
    constructor(tag = "div", id = "") {
        this.tagName = tag.toUpperCase();
        this.id = id;
        this.children = [];
        this.ownText = "";
        this.innerHTML = "";
        this.value = "";
        this.hidden = false;
        this.disabled = false;
        this.className = "";
        this.dataset = {};
        this.style = {};
        this.attributes = {};
        this.classList = { add() {}, remove() {}, toggle() {}, contains: () => false };
    }
    get textContent() { return this.ownText + this.children.map((child) => child.textContent).join(" "); }
    set textContent(value) { this.ownText = String(value); this.children = []; }
    replaceChildren(...nodes) { this.ownText = ""; this.children = nodes; }
    append(...nodes) { this.children.push(...nodes.map((node) => (typeof node === "string" ? textNode(node) : node))); }
    appendChild(node) { this.children.push(node); return node; }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    querySelector() { return new Element(); }
    querySelectorAll() { return []; }
    addEventListener() {}
    focus() {}
}

function textNode(text) {
    const node = new Element("#text");
    node.ownText = text;
    return node;
}

function jsonResponse(status, body) {
    return { ok: status >= 200 && status < 300, status, json: async () => body };
}

function createApp({ routes = {}, ids = [...DASHBOARD_IDS, ...IMPORT_IDS], storage = {} } = {}) {
    const elements = new Map(ids.map((id) => [id, new Element("div", id)]));
    const store = new Map(Object.entries(storage));
    const calls = [];
    const context = {
        console,
        Intl,
        URLSearchParams,
        Node: Element,
        Option: class extends Element {
            constructor(text = "", value = "") { super("option"); this.ownText = text; this.value = value; }
        },
        document: {
            getElementById: (id) => elements.get(id) || null,
            createElement: (tag) => new Element(tag),
            createTextNode: textNode,
            querySelector: () => null,
            querySelectorAll: () => [],
            addEventListener() {},
            documentElement: new Element("html")
        },
        localStorage: {
            getItem: (key) => (store.has(key) ? store.get(key) : null),
            setItem: (key, value) => store.set(key, String(value)),
            removeItem: (key) => store.delete(key)
        },
        navigator: {},
        location: { origin: "http://finance.test" },
        getComputedStyle: () => ({ getPropertyValue: () => "" }),
        setTimeout: () => 0,
        clearTimeout() {},
        confirm: () => true,
        fetch: async (url, options = {}) => {
            const { pathname, search } = new URL(url);
            calls.push({ path: pathname + search, method: options.method || "GET" });
            const handler = routes[`${options.method || "GET"} ${pathname}`];
            if (!handler) return jsonResponse(404, { detail: "Not found" });
            const [status, body] = typeof handler === "function" ? handler() : handler;
            return jsonResponse(status, body);
        }
    };
    context.window = context;
    context.self = context;
    vm.createContext(context);
    SOURCES.forEach((source) => vm.runInContext(source, context));
    return { context, elements, store, calls, run: (code) => vm.runInContext(code, context) };
}

const SNAPSHOT_KEY = "finance_tracker_data_v2";
const OLDER = [
    { id: 15, date: "10.10.2026, 09:15:00", type: "Доход", amount: 200000, category: "Зарплата" },
    { id: 16, date: "11.10.2026, 13:40:00", type: "Расход", amount: 3500, category: "Еда" },
    { id: 17, date: "12.10.2026, 18:05:00", type: "Расход", amount: 1200, category: "Транспорт" },
    { id: 18, date: "13.10.2026, 20:30:00", type: "Расход", amount: 8000, category: "Развлечения" },
    { id: 19, date: "14.10.2026, 08:00:00", type: "Расход", amount: 2500, category: "Здоровье" }
];
// As stored by the CSV import: date without a real time, English categories.
const IMPORTED = [
    { id: 20, date: "01.10.2026, 00:00:00", type: "Доход", amount: 150000, category: "Salary" },
    { id: 21, date: "02.10.2026, 00:00:00", type: "Расход", amount: 4500, category: "Food" },
    { id: 22, date: "03.10.2026, 00:00:00", type: "Расход", amount: 12000, category: "Transport" }
];
const ALL = [...OLDER, ...IMPORTED];
const balanceOf = (operations) => operations.reduce((sum, op) => sum + (op.type === "Доход" ? op.amount : -op.amount), 0);

function statisticsOf(operations) {
    const income = operations.filter((op) => op.type === "Доход").reduce((sum, op) => sum + op.amount, 0);
    const expense = operations.filter((op) => op.type === "Расход").reduce((sum, op) => sum + op.amount, 0);
    return {
        total_income: income, total_expense: expense, balance: income - expense, savings_rate: 0,
        operations_count: operations.length, categories: {}, monthly_trend: { "2026-10": { income, expense } }
    };
}

function apiRoutes(operations, overrides = {}) {
    return {
        "GET /balance": [200, { balance: balanceOf(operations) }],
        "GET /statistics": [200, statisticsOf(operations)],
        "GET /operations": [200, operations],
        "GET /budgets": [200, []],
        "GET /goals": [200, []],
        ...overrides
    };
}

const historyItems = (app) => app.elements.get("operations-list").children.filter((node) => node.tagName === "ARTICLE");
const historyText = (app) => app.elements.get("operations-list").textContent;
const snapshot = (app) => JSON.parse(app.store.get(SNAPSHOT_KEY));
const staleSnapshot = () => ({ [SNAPSHOT_KEY]: JSON.stringify({ balance: balanceOf(OLDER), ...statisticsOf(OLDER), operations: OLDER }) });

test("operations from the API, including CSV-imported ones, appear in the history", async () => {
    const app = createApp({ routes: apiRoutes(ALL) });
    await app.run("loadDashboard()");
    assert.equal(historyItems(app).length, 8);
    for (const category of ["Salary", "Food", "Transport", "Зарплата"]) {
        assert.ok(historyText(app).includes(category), `${category} is shown`);
    }
    assert.ok(historyText(app).includes("2026"), "imported dates are formatted, not dropped");
});

test("the balance shown matches the API", async () => {
    const app = createApp({ routes: apiRoutes(ALL) });
    await app.run("loadDashboard()");
    const expected = app.run(`formatMoney(${balanceOf(ALL)})`);
    assert.equal(app.elements.get("balance").textContent, expected);
    assert.equal(app.elements.get("summary-balance").textContent, expected);
});

// Root cause: one failing secondary request made loadDashboard discard every
// fresh response and render the old localStorage snapshot instead.
for (const failing of ["/goals", "/budgets"]) {
    test(`a failing ${failing} does not hide fresh operations and balance`, async () => {
        const app = createApp({
            routes: apiRoutes(ALL, { [`GET ${failing}`]: [500, { detail: "Internal Server Error" }] }),
            storage: staleSnapshot()
        });
        await app.run("loadDashboard()");
        assert.equal(historyItems(app).length, 8);
        assert.equal(app.elements.get("balance").textContent, app.run(`formatMoney(${balanceOf(ALL)})`));
        const list = failing === "/goals" ? "goals-list" : "budgets-list";
        assert.equal(app.elements.get(list).textContent, app.run('i18n.t("loadError")'));
        assert.equal(snapshot(app).operations.length, 8, "the snapshot is refreshed with the new data");
    });
}

test("when the core data cannot load, the last snapshot is shown", async () => {
    const app = createApp({
        routes: apiRoutes(ALL, { "GET /operations": [503, { detail: "Service Unavailable" }] }),
        storage: staleSnapshot()
    });
    await app.run("loadDashboard()");
    assert.equal(historyItems(app).length, 5);
    assert.equal(app.elements.get("notification-message").textContent, app.run('i18n.t("loadError")'));
    assert.equal(app.elements.get("notification").hidden, false);
});

test("without a snapshot a core failure shows an error, not an empty history", async () => {
    const app = createApp({ routes: apiRoutes(ALL, { "GET /balance": [500, {}] }) });
    await app.run("loadDashboard()");
    assert.equal(historyText(app), app.run('i18n.t("loadError")'));
});

test("a dashboard that fails to render shows an error and keeps the offline snapshot", async () => {
    const broken = apiRoutes(ALL, { "GET /operations": [200, null] });
    const app = createApp({ routes: broken, storage: staleSnapshot() });
    app.context.console = { ...console, error() {} };
    await app.run("loadDashboard()");
    assert.equal(historyText(app), app.run('i18n.t("loadError")'), "not stuck on the loading state");
    assert.equal(snapshot(app).operations.length, 5, "the broken data does not become the snapshot");
});

test("confirming an import refreshes history and balance", async () => {
    let committed = false;
    const routes = {
        ...apiRoutes(OLDER),
        "GET /balance": () => [200, { balance: balanceOf(committed ? ALL : OLDER) }],
        "GET /statistics": () => [200, statisticsOf(committed ? ALL : OLDER)],
        "GET /operations": () => [200, committed ? ALL : OLDER],
        "POST /import/csv/commit": () => {
            committed = true;
            return [200, { imported: 3, duplicate_rows: 0, error_rows: 0, errors: [], columns: [] }];
        }
    };
    const app = createApp({ routes });
    await app.run("loadDashboard()");
    assert.equal(historyItems(app).length, 5);

    app.run('importState.file = "date,amount\\n"; importState.preview = { valid_rows: 3, duplicate_rows: 0, rows: [], errors: [], error_rows: 0, total_rows: 3 }');
    await app.run("confirmImport()");
    assert.ok(app.calls.some((call) => call.method === "POST" && call.path.startsWith("/import/csv/commit")));
    assert.equal(historyItems(app).length, 8);
    assert.equal(app.elements.get("balance").textContent, app.run(`formatMoney(${balanceOf(ALL)})`));
});

test("imported operations are not hidden without filters; search and filters still work", async () => {
    const app = createApp({ routes: apiRoutes(ALL) });
    await app.run("loadDashboard()");
    const count = (setup) => {
        app.elements.get("operation-type-filter").value = setup.type || "";
        app.elements.get("operation-category-filter").value = setup.category || "";
        app.elements.get("operation-search").value = setup.search || "";
        return app.run("getFilteredOperations(currentDashboard.operations).length");
    };
    assert.equal(count({}), 8);
    assert.equal(count({ search: "salary" }), 1);
    assert.equal(count({ search: "  FOOD " }), 1);
    assert.equal(count({ type: "Расход" }), 6);
    assert.equal(count({ type: "Доход" }), 2);
    assert.equal(count({ category: "Еда" }), 1);
    assert.equal(count({ type: "Расход", search: "transport" }), 1);
    assert.equal(count({ search: "нет такой" }), 0);
    app.run('i18n.setLanguage("en")');
    assert.equal(count({ search: "income" }), 2, "search matches the translated type");
    app.run('i18n.setLanguage("ru")');

    app.elements.get("operation-search").value = "нет такой";
    app.run("renderOperations(currentDashboard.operations)");
    assert.equal(historyText(app), app.run('i18n.t("noSearchResults")'));
});

test("date formatting never throws and keeps unknown formats readable", () => {
    const app = createApp();
    for (const language of ["ru", "en", "kk"]) {
        app.run(`i18n.setLanguage("${language}")`);
        for (const value of ["01.10.2026, 00:00:00", "15.10.2026 14:30", "15.10.2026, 14:30:59"]) {
            const formatted = app.run(`formatDate(${JSON.stringify(value)})`);
            assert.ok(formatted.includes("2026"), `${language}: ${value} -> ${formatted}`);
            assert.notEqual(formatted, value);
        }
    }
    for (const value of ["2026-10-01", "", "not a date", "31.02.2026, 00:00:00"]) {
        assert.equal(typeof app.run(`formatDate(${JSON.stringify(value)})`), "string");
    }
    assert.doesNotThrow(() => app.run("formatDate(null); formatDate(undefined)"));
});

test("a page without the import markup still initialises", () => {
    const app = createApp({ ids: DASHBOARD_IDS });
    assert.doesNotThrow(() => app.run("setupImport()"));
});
