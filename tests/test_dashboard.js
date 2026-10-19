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
        this.checked = false;
        this.className = "";
        this.dataset = {};
        this.style = {};
        this.attributes = {};
        const classes = new Set();
        this.classList = {
            add: (...names) => names.forEach((name) => classes.add(name)),
            remove: (...names) => names.forEach((name) => classes.delete(name)),
            toggle: (name, force) => ((force ?? !classes.has(name)) ? classes.add(name) : classes.delete(name)),
            contains: (name) => classes.has(name)
        };
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
    click() { this.clicked = true; }
    remove() { this.removed = true; }
}

function textNode(text) {
    const node = new Element("#text");
    node.ownText = text;
    return node;
}

const NO_BODY = Symbol("no JSON body");

function jsonResponse(status, body) {
    return {
        ok: status >= 200 && status < 300,
        status,
        json: async () => {
            if (body === NO_BODY) throw new SyntaxError("Unexpected end of JSON input");
            return body;
        },
        blob: async () => ({ blobOf: body })
    };
}

// autoElements: every getElementById() finds an element, so the page's real
// DOMContentLoaded set-up can run (app.init()) and its own handlers be clicked.
// queries: elements that document.querySelectorAll(selector) returns, so the
// page's own set-up can wire them (e.g. the language buttons).
function createApp({ routes = {}, ids = [...DASHBOARD_IDS, ...IMPORT_IDS], storage = {}, autoElements = false, queries = {} } = {}) {
    const elements = new Map(ids.map((id) => [id, new Element("div", id)]));
    const listeners = {};
    const reloads = [];
    const lookup = (id) => {
        if (!elements.has(id) && autoElements) elements.set(id, new Element("div", id));
        return elements.get(id) || null;
    };
    const store = new Map(Object.entries(storage));
    const calls = [];
    const downloads = [];
    const revoked = [];
    const timers = [];
    const context = {
        console,
        Intl,
        URLSearchParams,
        URL: {
            createObjectURL: (blob) => { downloads.push({ blob }); return `blob:finance.test/${downloads.length}`; },
            revokeObjectURL: (url) => revoked.push(url)
        },
        Node: Element,
        Option: class extends Element {
            constructor(text = "", value = "") { super("option"); this.ownText = text; this.value = value; }
        },
        document: {
            getElementById: lookup,
            createElement: (tag) => new Element(tag),
            createTextNode: textNode,
            querySelector: () => null,
            querySelectorAll: (selector) => queries[selector] || [],
            addEventListener: (type, handler) => { listeners[type] = handler; },
            documentElement: new Element("html"),
            body: new Element("body")
        },
        localStorage: {
            getItem: (key) => (store.has(key) ? store.get(key) : null),
            setItem: (key, value) => store.set(key, String(value)),
            removeItem: (key) => store.delete(key)
        },
        navigator: {},
        location: { origin: "http://finance.test", reload: () => reloads.push(Date.now()) },
        scrollTo() {},
        getComputedStyle: () => ({ getPropertyValue: () => "" }),
        setTimeout: (callback) => timers.push(callback),
        clearTimeout() {},
        confirm: () => true,
        fetch: async (url, options = {}) => {
            const { pathname, search } = new URL(url);
            calls.push({ path: pathname + search, method: options.method || "GET", body: options.body, headers: options.headers });
            const handler = routes[`${options.method || "GET"} ${pathname}`];
            if (!handler) return jsonResponse(404, { detail: "Not found" });
            const [status, body] = typeof handler === "function" ? await handler() : handler;
            return jsonResponse(status, body);
        }
    };
    context.window = context;
    context.self = context;
    vm.createContext(context);
    SOURCES.forEach((source) => vm.runInContext(source, context));
    return {
        context, elements, store, calls, reloads, downloads, revoked, timers,
        el: lookup,
        init: () => listeners.DOMContentLoaded(),
        run: (code) => vm.runInContext(code, context)
    };
}

// Lets pending fetch handlers and awaits settle.
async function settle() {
    for (let i = 0; i < 30; i += 1) await new Promise((resolve) => setImmediate(resolve));
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
const USER = "owner@example.test";
const snapshot = (app) => JSON.parse(app.store.get(SNAPSHOT_KEY)).dashboard;
const signedIn = (email = USER) => ({ finance_tracker_token: "test-token", finance_tracker_email: email });
const staleSnapshot = (owner = USER) => ({
    ...signedIn(),
    [SNAPSHOT_KEY]: JSON.stringify({ owner, dashboard: { balance: balanceOf(OLDER), ...statisticsOf(OLDER), operations: OLDER } })
});

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

// Security regressions: user data must never reach innerHTML, and the offline
// snapshot must never outlive the session or leak to another account.
const PAYLOAD = '<img src=x onerror="alert(1)">';

function allNodes(node) {
    return [node, ...node.children.flatMap(allNodes)];
}

const AUTH_IDS = [...DASHBOARD_IDS, "user-auth-area", "auth-modal", "auth-error", "auth-email", "auth-password"];

function assertTextOnly(root) {
    assert.ok(root.textContent.includes(PAYLOAD), "the payload is shown as plain text");
    for (const node of allNodes(root)) {
        assert.ok(!String(node.innerHTML).includes("<img"), `no markup in innerHTML of ${node.tagName}`);
    }
}

test("HTML in a CSV category is shown in the calendar only as text", () => {
    const app = createApp();
    const today = app.run(`(() => { const d = new Date(); return [d.getDate(), d.getMonth() + 1].map((n) => String(n).padStart(2, "0")).join(".") + "." + d.getFullYear(); })()`);
    const operation = { id: 1, date: `${today}, 00:00:00`, type: "Расход", amount: 100, category: PAYLOAD };
    app.run(`renderCalendar(${JSON.stringify([operation])})`);
    const grid = app.elements.get("calendar-grid");
    const day = Number(today.slice(0, 2));
    const cell = grid.children.filter((node) => String(node.className).startsWith("calendar-cell") && !node.className.includes("empty"))[day - 1];
    cell.onclick();
    assertTextOnly(app.elements.get("calendar-details"));
});

test("HTML in a budget category and a goal title is shown only as text; buttons still work", async () => {
    const app = createApp({ routes: { "DELETE /budgets/7": [200, {}], "GET /budgets": [200, []], "DELETE /goals/9": [200, {}], "PUT /goals/9/progress": [200, {}], "GET /goals": [200, []] } });
    app.run(`renderBudgets(${JSON.stringify([{ id: 7, category: PAYLOAD, amount: 1000, spent: 500, percent: 50, exceeded: false }])})`);
    const budgets = app.elements.get("budgets-list");
    assertTextOnly(budgets);
    const budgetButton = allNodes(budgets).find((node) => node.className === "icon-button");
    await budgetButton.onclick();
    assert.ok(app.calls.some((call) => call.method === "DELETE" && call.path === "/budgets/7"));

    app.run(`renderGoals(${JSON.stringify([{ id: 9, title: PAYLOAD, target_amount: 1000, current_amount: 100, progress_percent: 10 }])})`);
    const goals = app.elements.get("goals-list");
    assertTextOnly(goals);
    const nodes = allNodes(goals);
    nodes.find((node) => node.className === "goal-progress-input").value = "250";
    await nodes.find((node) => node.className === "secondary-button").onclick();
    assert.ok(app.calls.some((call) => call.method === "PUT" && call.path === "/goals/9/progress"));
    await nodes.find((node) => node.className === "icon-button").onclick();
    assert.ok(app.calls.some((call) => call.method === "DELETE" && call.path === "/goals/9"));
});

test("HTML in the email is shown in the user badge only as text", () => {
    const app = createApp({ ids: AUTH_IDS, storage: signedIn(PAYLOAD) });
    app.run("renderUserBadge()");
    assertTextOnly(app.elements.get("user-auth-area"));
});

test("logging out removes the token, email and the financial snapshot", () => {
    const app = createApp({ ids: AUTH_IDS, storage: staleSnapshot() });
    app.run("renderUserBadge()");
    const logout = allNodes(app.elements.get("user-auth-area")).find((node) => node.id === "logout-btn");
    logout.onclick();
    for (const key of [SNAPSHOT_KEY, "finance_tracker_token", "finance_tracker_email"]) {
        assert.equal(app.store.has(key), false, `${key} is removed`);
    }
});

test("a 401 response removes the financial snapshot", async () => {
    const app = createApp({
        ids: AUTH_IDS,
        routes: apiRoutes(ALL, { "GET /balance": [401, { detail: "Unauthorized" }] }),
        storage: staleSnapshot()
    });
    await app.run("loadDashboard()");
    assert.equal(app.store.has(SNAPSHOT_KEY), false);
    assert.equal(historyItems(app).length, 0, "the stale snapshot is not rendered");
});

test("a snapshot saved for another user is never shown", async () => {
    const app = createApp({
        routes: apiRoutes(ALL, { "GET /operations": [503, { detail: "Service Unavailable" }] }),
        storage: { ...staleSnapshot("previous@example.test"), ...signedIn("next@example.test") }
    });
    await app.run("loadDashboard()");
    assert.equal(historyItems(app).length, 0);
    assert.equal(historyText(app), app.run('i18n.t("loadError")'));
    assert.equal(app.store.has(SNAPSHOT_KEY), false, "the foreign snapshot is discarded");
});

test("a legacy snapshot without an owner is never shown", async () => {
    const app = createApp({
        routes: apiRoutes(ALL, { "GET /operations": [503, {}] }),
        storage: { ...signedIn(), [SNAPSHOT_KEY]: JSON.stringify({ balance: 1, ...statisticsOf(OLDER), operations: OLDER }) }
    });
    await app.run("loadDashboard()");
    assert.equal(historyItems(app).length, 0);
});

test("the saved snapshot is tagged with the signed-in user", async () => {
    const app = createApp({ routes: apiRoutes(ALL), storage: signedIn() });
    await app.run("loadDashboard()");
    assert.equal(JSON.parse(app.store.get(SNAPSHOT_KEY)).owner, USER);
});

function textOfPage(app) {
    return [...app.elements.values()].map((element) => element.textContent).join(" ");
}

test("signing out removes the previous user's figures from the page", async () => {
    const budgets = [{ id: 1, category: "Еда", amount: 50000, spent: 4500, percent: 9, exceeded: false }];
    const goals = [{ id: 2, title: "Отпуск мечты", target_amount: 900000, current_amount: 1000, progress_percent: 0.1 }];
    const app = createApp({
        ids: [...AUTH_IDS, ...IMPORT_IDS, "settings-email"],
        routes: apiRoutes(ALL, { "GET /budgets": [200, budgets], "GET /goals": [200, goals] }),
        storage: signedIn()
    });
    await app.run("loadDashboard()");
    app.elements.get("settings-email").textContent = USER;
    app.elements.get("operation-search").value = "salary";
    app.run('importState.preview = { rows: [{ description: "TEST salary" }] }');
    app.elements.get("import-summary").textContent = "Строк в файле: 3";
    const before = textOfPage(app);
    for (const secret of ["Salary", "Отпуск мечты", USER]) assert.ok(before.includes(secret), `${secret} shown before sign-out`);

    app.run("renderUserBadge()");
    allNodes(app.elements.get("user-auth-area")).find((node) => node.id === "logout-btn").onclick();

    // Only this user's values are checked: the category filter keeps its fixed,
    // shared list of standard categories.
    const after = textOfPage(app);
    const userValues = ["Salary", "Food", "Transport", "Отпуск мечты", "Строк в файле", USER,
        app.run(`formatMoney(${balanceOf(ALL)})`), app.run("formatMoney(150000)")];
    for (const secret of userValues) {
        assert.ok(!after.includes(secret), `${secret} is gone after sign-out`);
    }
    for (const id of ["balance", "summary-balance", "total-income", "total-expense"]) {
        assert.equal(app.elements.get(id).textContent, app.run("formatMoney(0)"), id);
    }
    assert.equal(historyItems(app).length, 0);
    assert.equal(app.elements.get("operation-search").value, "");
    assert.equal(app.run("currentDashboard"), null);
    assert.equal(app.run("importState.preview"), null);
    assert.equal(app.elements.get("import-modal").hidden, true);
});

test("a response that arrives after sign-out is never drawn, even for the next account", async () => {
    let account = "first";
    let releaseFirst;
    const firstOperations = new Promise((resolve) => { releaseFirst = resolve; });
    const NEXT = [{ id: 40, date: "05.10.2026, 10:00:00", type: "Доход", amount: 777, category: "Фриланс" }];
    const pick = (first, next) => () => (account === "first" ? first : next);
    const app = createApp({
        ids: [...AUTH_IDS, ...IMPORT_IDS],
        routes: {
            "GET /balance": pick([200, { balance: balanceOf(ALL) }], [200, { balance: balanceOf(NEXT) }]),
            "GET /statistics": pick([200, statisticsOf(ALL)], [200, statisticsOf(NEXT)]),
            "GET /operations": () => (account === "first" ? firstOperations : [200, NEXT]),
            "GET /budgets": [200, []],
            "GET /goals": [200, []]
        },
        storage: signedIn()
    });
    const slowFirstLoad = app.run("loadDashboard()");      // still waiting for /operations
    app.run("clearSession()");                              // the first user signs out
    account = "next";
    app.store.set("finance_tracker_token", "next-token");
    app.store.set("finance_tracker_email", "next@example.test");
    await app.run("loadDashboard()");                       // the next user signs in
    releaseFirst([200, ALL]);                               // the old response finally arrives
    await slowFirstLoad;

    assert.equal(historyItems(app).length, 1);
    assert.ok(historyText(app).includes("Фриланс"));
    assert.ok(!historyText(app).includes("Salary"), "the first user's operations are not drawn");
    assert.equal(app.elements.get("balance").textContent, app.run(`formatMoney(${balanceOf(NEXT)})`));
    const saved = JSON.parse(app.store.get(SNAPSHOT_KEY));
    assert.equal(saved.owner, "next@example.test");
    assert.equal(saved.dashboard.operations.length, 1);
});

// Session switches: settings of the previous account must never be used for the
// next one, and late responses of an ended session must not touch the page.
const SETTINGS_IDS = ["settings-email", "settings-language", "settings-theme", "settings-currency", "theme-icon", "theme-label", "theme-toggle"];
const ACCOUNTS = {
    first: { email: "first@example.test", currency: "USD", language: "EN", operations: ALL },
    next: { email: "next@example.test", currency: "KZT", language: "RU", operations: OLDER }
};

function deferred() {
    let release;
    const promise = new Promise((resolve) => { release = resolve; });
    return { promise, release };
}

function accountRoutes(current, overrides = {}) {
    const of = () => ACCOUNTS[current.account];
    return {
        "GET /auth/me": () => [200, { id: 1, email: of().email, settings: { currency: of().currency, language: of().language, theme: "light" } }],
        "GET /balance": () => [200, { balance: balanceOf(of().operations) }],
        "GET /statistics": () => [200, statisticsOf(of().operations)],
        "GET /operations": () => [200, of().operations],
        "GET /budgets": [200, []],
        "GET /goals": [200, []],
        ...overrides
    };
}

function signIn(app, current, account) {
    current.account = account;
    app.store.set("finance_tracker_token", `${account}-token`);
    app.store.set("finance_tracker_email", ACCOUNTS[account].email);
}

test("after switching accounts the new user's currency and language are used from the first render", async () => {
    const current = { account: "first" };
    const app = createApp({ ids: [...AUTH_IDS, ...IMPORT_IDS, ...SETTINGS_IDS], routes: accountRoutes(current) });
    signIn(app, current, "first");
    await app.run("startSession()");
    assert.equal(app.run("currentCurrency"), "USD");
    assert.ok(app.elements.get("balance").textContent.includes("$"));

    app.run("clearSession()");
    assert.equal(app.run("currentCurrency"), "KZT", "sign-out drops the previous account's currency");

    signIn(app, current, "next");
    const renderedWith = [];
    app.run("const originalRender = renderDashboard; renderDashboard = (data) => { globalThis.__currency.push(currentCurrency); return originalRender(data); }");
    app.context.__currency = renderedWith;
    const firstCall = app.calls.length;
    await app.run("startSession()");

    const paths = app.calls.slice(firstCall).map((call) => call.path);
    assert.ok(paths.indexOf("/auth/me") < paths.indexOf("/balance"), "settings load before the dashboard");
    assert.ok(renderedWith.length > 0 && renderedWith.every((currency) => currency === "KZT"), `rendered with ${renderedWith}`);
    assert.ok(app.elements.get("balance").textContent.includes("₸"));
    assert.equal(app.run("i18n.getLanguage()"), "ru");
    assert.equal(app.elements.get("settings-email").textContent, ACCOUNTS.next.email);
    assert.equal(app.elements.get("settings-currency").value, "KZT");
});

test("a late settings response from the previous session is ignored", async () => {
    const current = { account: "first" };
    const late = deferred();
    const routes = accountRoutes(current, {
        "GET /auth/me": () => (current.account === "first"
            ? late.promise
            : [200, { id: 2, email: ACCOUNTS.next.email, settings: { currency: "KZT", language: "RU", theme: "light" } }])
    });
    const app = createApp({ ids: [...AUTH_IDS, ...IMPORT_IDS, ...SETTINGS_IDS], routes });
    signIn(app, current, "first");
    const slowSettings = app.run("loadUserSettings()");
    app.run("clearSession()");
    signIn(app, current, "next");
    await app.run("startSession()");
    late.release([200, { id: 1, email: ACCOUNTS.first.email, settings: { currency: "USD", language: "EN", theme: "dark" } }]);
    await slowSettings;

    assert.equal(app.run("currentCurrency"), "KZT");
    assert.equal(app.run("i18n.getLanguage()"), "ru");
    assert.equal(app.run("document.documentElement.dataset.theme"), "light", "the previous account's dark theme is not applied");
    assert.equal(app.elements.get("settings-email").textContent, ACCOUNTS.next.email);
    assert.ok(app.elements.get("balance").textContent.includes("₸"));
});

test("a late import confirmation after sign-out changes neither the page nor the new session", async () => {
    const late = deferred();
    const app = createApp({
        ids: [...AUTH_IDS, ...IMPORT_IDS],
        routes: { ...apiRoutes(OLDER), "POST /import/csv/commit": () => late.promise },
        storage: signedIn()
    });
    app.run('importState.file = "date,amount\\n"; importState.preview = { valid_rows: 3, duplicate_rows: 0, rows: [], errors: [], error_rows: 0, total_rows: 3 }');
    const pendingImport = app.run("confirmImport()");
    app.run("clearSession()");
    const callsAtSignOut = app.calls.length;
    late.release([200, { imported: 3, duplicate_rows: 0, error_rows: 0, errors: [], columns: [] }]);
    await pendingImport;

    assert.equal(app.run("importState.report"), null);
    assert.equal(app.run("importState.preview"), null);
    assert.equal(app.run("importState.file"), null);
    assert.equal(app.elements.get("import-report").textContent, "");
    assert.equal(app.elements.get("import-error").textContent, "");
    assert.equal(app.elements.get("import-modal").hidden, true);
    assert.equal(app.calls.length, callsAtSignOut, "no dashboard reload for the ended session");
    assert.equal(historyItems(app).length, 0);
});

test("a late import preview after sign-out is dropped", async () => {
    const late = deferred();
    const app = createApp({
        ids: [...AUTH_IDS, ...IMPORT_IDS],
        routes: { ...apiRoutes(OLDER), "POST /import/csv/preview": () => late.promise },
        storage: signedIn()
    });
    app.elements.get("import-date-column").value = "0";
    app.elements.get("import-amount-column").value = "1";
    app.elements.get("import-amount-mode").value = "signed";
    app.run('importState.file = "date,amount\\n"');
    const pendingPreview = app.run("runImportPreview()");
    app.run("clearSession()");
    late.release([200, { valid_rows: 1, duplicate_rows: 0, error_rows: 0, total_rows: 1, errors: [], columns: [],
        rows: [{ line: 2, date: "01.10.2026, 00:00:00", type: "Доход", amount: 5, category: "Salary", description: "TEST", duplicate: false }] }]);
    await pendingPreview;

    assert.equal(app.run("importState.preview"), null);
    assert.equal(app.elements.get("import-summary").textContent, "");
    assert.equal(app.elements.get("import-preview-table").children.length, 0);
});

// ---------------------------------------------------------------------------
// The tests below run the page's real DOMContentLoaded set-up (autoElements) and
// press its real buttons, then check state the user would see: stored token,
// notifications, form values, CSS classes, currency and the API calls made.

const SESSION_EXPIRED = "Недействительный или просроченный токен";
const NEXT_USER = "next@example.test";
const NEXT_OPERATIONS = [{ id: 40, date: "05.10.2026, 10:00:00", type: "Доход", amount: 777, category: "Фриланс" }];

function sessionRoutes(current, overrides = {}) {
    const mine = () => (current.account === "first" ? ALL : NEXT_OPERATIONS);
    return {
        "GET /auth/me": () => [200, current.account === "first"
            ? { id: 1, email: USER, settings: { language: "RU", theme: "light", currency: "KZT" } }
            : { id: 2, email: NEXT_USER, settings: { language: "RU", theme: "light", currency: "EUR" } }],
        "GET /balance": () => [200, { balance: balanceOf(mine()) }],
        "GET /statistics": () => [200, statisticsOf(mine())],
        "GET /operations": () => [200, mine()],
        "GET /budgets": [200, []],
        "GET /goals": [200, []],
        "POST /auth/login": () => [200, { access_token: "next-token", token_type: "bearer", email: NEXT_USER }],
        ...overrides
    };
}

async function boot(overrides = {}, { storage = signedIn(), current = { account: "first" } } = {}) {
    const app = createApp({ autoElements: true, routes: sessionRoutes(current, overrides), storage });
    app.current = current;
    app.init();
    await settle();
    return app;
}

async function signOut(app) {
    allNodes(app.el("user-auth-area")).find((node) => node.id === "logout-btn").onclick();
    await settle();
}

async function signInNext(app) {
    app.current.account = "next";
    app.el("auth-email").value = NEXT_USER;
    app.el("auth-password").value = "secret12";
    await app.el("auth-form").onsubmit({ preventDefault() {} });
    await settle();
}

const notice = (app) => app.el("notification-message").textContent;

// R4 --------------------------------------------------------------------------

test("a wrong current password keeps the session and shows the server message", async () => {
    const app = await boot({ "PUT /auth/password": [401, { detail: "Неверный текущий пароль" }] });
    app.el("current-password").value = "typo";
    app.el("new-password").value = "new-secret";
    await app.el("change-password-btn").onclick();
    assert.equal(app.store.get("finance_tracker_token"), "test-token", "still signed in");
    assert.ok(app.store.has(SNAPSHOT_KEY), "the snapshot is kept");
    assert.equal(historyItems(app).length, 8, "the dashboard is not cleared");
    assert.equal(notice(app), "Неверный текущий пароль");
    app.run('i18n.setLanguage("en")');
    assert.equal(app.run('i18n.translateApiError("Неверный текущий пароль", 401)'), "The current password is incorrect");
});

test("a wrong password when deleting the account keeps the session", async () => {
    const app = await boot({ "DELETE /auth/delete": [401, { detail: "Неверный пароль" }] });
    app.el("delete-password").value = "typo";
    await app.el("delete-account-btn").onclick();
    assert.equal(app.store.get("finance_tracker_token"), "test-token");
    assert.equal(app.reloads.length, 0, "the page is not reloaded");
    assert.equal(notice(app), "Неверный пароль");
});

test("an expired token ends the session", async () => {
    const app = await boot({ "GET /balance": [401, { detail: SESSION_EXPIRED }] });
    assert.equal(app.store.has("finance_tracker_token"), false);
    assert.equal(app.store.has(SNAPSHOT_KEY), false);
    assert.equal(app.el("auth-modal").hidden, false, "the sign-in dialog is shown");
    assert.equal(historyItems(app).length, 0);
    assert.equal(app.el("save-settings-btn").disabled, true);
});

test("a 401 without a readable body also ends the session", async () => {
    const app = await boot({ "GET /operations": [401, NO_BODY] });
    assert.equal(app.store.has("finance_tracker_token"), false);
});

test("a wrong sign-in password shows the server message", async () => {
    const app = await boot({ "POST /auth/login": [401, { detail: "Неверный email или пароль" }] }, { storage: {} });
    app.el("auth-email").value = "someone@example.test";
    app.el("auth-password").value = "typo";
    await app.el("auth-form").onsubmit({ preventDefault() {} });
    assert.equal(app.el("auth-error").textContent, "Неверный email или пароль");
    assert.equal(app.store.has("finance_tracker_token"), false);
    assert.ok(!app.calls.some((call) => call.path === "/auth/me"), "no session is started");
});

test("a late 401 from the previous session does not sign out the next user", async () => {
    const late = deferred();
    const app = await boot({ "DELETE /goals/9": () => late.promise });
    const pending = app.run("deleteGoal(9)");
    await signOut(app);
    await signInNext(app);
    late.release([401, { detail: SESSION_EXPIRED }]);
    await pending;
    await settle();
    assert.equal(app.store.get("finance_tracker_token"), "next-token");
    assert.equal(app.el("auth-modal").hidden, true);
    assert.equal(historyItems(app).length, 1, "the next user's dashboard stays");
});

// R3 --------------------------------------------------------------------------

test("settings cannot be saved before they have loaded", async () => {
    const pendingSettings = deferred();
    const app = createApp({
        autoElements: true,
        routes: sessionRoutes({ account: "first" }, { "GET /auth/me": () => pendingSettings.promise, "PUT /auth/profile": [200, {}] }),
        storage: signedIn()
    });
    app.init();
    await settle();
    assert.equal(app.el("save-settings-btn").disabled, true);
    await app.el("save-settings-btn").onclick();
    assert.ok(!app.calls.some((call) => call.path === "/auth/profile"), "nothing is saved");

    pendingSettings.release([200, { id: 1, email: USER, settings: { language: "RU", theme: "light", currency: "USD" } }]);
    await settle();
    assert.equal(app.el("save-settings-btn").disabled, false);
    assert.equal(app.run("currentCurrency"), "USD");
});

test("when the next user's settings fail to load, nothing of the previous user's is used", async () => {
    const current = { account: "first" };
    const app = await boot({
        "GET /auth/me": () => (current.account === "first"
            ? [200, { id: 1, email: USER, settings: { language: "RU", theme: "dark", currency: "USD" } }]
            : [500, { detail: "Internal Server Error" }])
    }, { current });
    assert.equal(app.run("currentCurrency"), "USD");
    app.el("settings-currency").value = "USD";

    await signOut(app);
    app.context.console = { ...console, error() {} };
    await signInNext(app);

    assert.equal(app.run("currentCurrency"), "KZT");
    assert.equal(app.el("settings-currency").value, "KZT");
    assert.equal(app.el("settings-theme").value, "light");
    assert.equal(app.el("save-settings-btn").disabled, true, "saving stays disabled");
    assert.equal(notice(app), app.run('i18n.t("settingsLoadError")'));
    assert.equal(historyItems(app).length, 1, "the dashboard itself still loads");
});

test("an authorisation failure while loading settings does not load the dashboard", async () => {
    const app = await boot({ "GET /auth/me": [401, { detail: SESSION_EXPIRED }] });
    assert.ok(!app.calls.some((call) => ["/balance", "/statistics?period=all", "/operations?period=all"].includes(call.path)));
    assert.equal(app.store.has("finance_tracker_token"), false);
});

// R2 --------------------------------------------------------------------------

test("signing out clears unsent forms, passwords, CSV headers and the sign-in email", async () => {
    const app = await boot();
    for (const [id, value] of Object.entries({
        "current-password": "old-secret", "new-password": "new-secret", "delete-password": "old-secret",
        "budget-amount": "50000", "goal-title": "Тайная цель", "goal-target": "900000", "goal-current": "500",
        "goal-deadline": "2027", "import-income-values": "CR", "import-expense-values": "DR",
        "settings-currency": "USD", "settings-theme": "dark", "settings-language": "EN"
    })) app.el(id).value = value;
    app.el("add-budget-btn").onclick();
    app.el("add-goal-btn").onclick();
    assert.equal(app.el("budget-form").classList.contains("hidden"), false);
    app.run('importState.columns = ["Секретный заголовок", "Сумма"]; fillImportColumnSelects()');
    assert.ok(app.el("import-date-column").children.length > 1);

    await signOut(app);

    for (const id of ["current-password", "new-password", "delete-password", "budget-amount", "goal-title",
        "goal-target", "goal-deadline", "import-income-values", "import-expense-values", "auth-email", "auth-password"]) {
        assert.equal(app.el(id).value, "", id);
    }
    assert.equal(app.el("goal-current").value, "0");
    assert.equal(app.el("budget-form").classList.contains("hidden"), true);
    assert.equal(app.el("goal-form").classList.contains("hidden"), true);
    assert.equal(app.el("import-date-column").children.length, 0, "CSV headers are gone");
    assert.deepEqual(["language", "theme", "currency"].map((f) => app.el(`settings-${f}`).value), ["RU", "light", "KZT"]);
});

// R1 --------------------------------------------------------------------------

const LATE_ACTIONS = [
    ["settings save", "PUT /auth/profile", (app) => { app.el("settings-currency").value = "USD"; return app.el("save-settings-btn").onclick(); }],
    ["operation save", "POST /operations", (app) => { app.el("operation-amount").value = "10"; return app.el("save-operation-btn").onclick(); }],
    ["budget save", "POST /budgets", (app) => app.el("save-budget-btn").onclick()],
    ["goal save", "POST /goals", (app) => { app.el("goal-title").value = "Старая цель"; return app.el("save-goal-btn").onclick(); }],
    ["goal progress", "PUT /goals/9/progress", (app) => app.run("updateGoalProgress(9, 5)")],
    ["operation delete", "DELETE /operations/20", (app) => app.run("deleteOperation(20)")],
    ["budget delete", "DELETE /budgets/7", (app) => app.run("deleteBudget(7)")],
    ["goal delete", "DELETE /goals/9", (app) => app.run("deleteGoal(9)")],
    ["password change", "PUT /auth/password", (app) => app.el("change-password-btn").onclick()],
    ["account deletion", "DELETE /auth/delete", (app) => app.el("delete-account-btn").onclick()]
];
const LATE_OUTCOMES = [
    ["success", [200, { message: "ok", notification: "⚠️ Бюджет на Salary превышен!" }]],
    ["success with alerts", [200, { message: "ok", notification: "⚠️ Бюджет на Еда превышен!", alerts: [
        { budget_id: 1, category: "Еда", month: "2026-10", level: "exceeded", percent: 120, spent: 120, amount: 100 }] }]],
    ["error", [400, { detail: "Ошибка прежней сессии" }]]
];

for (const [name, route, trigger] of LATE_ACTIONS) {
    for (const [outcome, response] of LATE_OUTCOMES) {
        test(`a late ${name} response (${outcome}) does not touch the next session`, async () => {
            const late = deferred();
            const app = await boot({ [route]: () => late.promise });
            const pending = trigger(app);
            await signOut(app);
            await signInNext(app);
            // What the next user has on screen and in progress right now.
            app.el("budget-amount").value = "777";
            app.el("goal-title").value = "Новая цель";
            app.el("current-password").value = "next-typing";
            const before = { notice: notice(app), currency: app.run("currentCurrency"), calls: app.calls.length };

            late.release(response);
            await pending;
            await settle();

            assert.equal(notice(app), before.notice, "no notification from the previous session");
            assert.equal(app.run("currentCurrency"), "EUR");
            assert.equal(app.store.get("finance_tracker_token"), "next-token");
            assert.equal(app.reloads.length, 0, "the next user's page is not reloaded");
            assert.equal(app.el("budget-amount").value, "777");
            assert.equal(app.el("goal-title").value, "Новая цель");
            assert.equal(app.el("current-password").value, "next-typing");
            assert.equal(app.calls.length, before.calls, "no reload of data for the old session");
            assert.equal(historyItems(app).length, 1);
        });
    }
}

// Language codes ---------------------------------------------------------------
// The interface uses ru / en / kk; the server stores RU / EN / KZ, and profiles
// saved by older versions may still hold KK.

function languageButtons() {
    return ["kk", "en", "ru"].map((language) => {
        const button = new Element("button");
        button.className = "language-button";
        button.dataset.language = language;
        return button;
    });
}

// A tiny stand-in for the server's user_settings row, shared across "reloads".
function profileServer(language) {
    const server = { language, saved: [] };
    const routes = {
        ...apiRoutes(ALL),
        "GET /auth/me": () => [200, { id: 1, email: USER, settings: { language: server.language, theme: "light", currency: "KZT" } }],
        "PUT /auth/profile": () => [200, { message: "ok" }]
    };
    return { server, routes };
}

async function openPage({ server, routes }, storage = signedIn()) {
    const buttons = languageButtons();
    const app = createApp({ autoElements: true, routes, storage, queries: { ".language-button": buttons } });
    // Like the real endpoint, the server keeps what PUT /auth/profile sends.
    const fetchStub = app.context.fetch;
    app.context.fetch = async (url, options = {}) => {
        if (options.method === "PUT" && url.endsWith("/auth/profile")) {
            const body = JSON.parse(options.body);
            if (body.language) server.language = body.language;
            server.saved.push(body.language);
        }
        return fetchStub(url, options);
    };
    app.init();
    await settle();
    app.buttons = Object.fromEntries(buttons.map((button) => [button.dataset.language, button]));
    return app;
}

for (const [stored, expected] of [["KZ", "kk"], ["KK", "kk"], ["kz", "kk"], ["EN", "en"], ["RU", "ru"]]) {
    test(`a profile language stored as ${stored} opens the interface in ${expected}`, async () => {
        const app = await openPage(profileServer(stored));
        assert.equal(app.run("i18n.getLanguage()"), expected);
        assert.equal(app.el("settings-language").value, { kk: "KZ", en: "EN", ru: "RU" }[expected]);
        for (const [language, button] of Object.entries(app.buttons)) {
            assert.equal(button.classList.contains("active"), language === expected, `${language} button state`);
            assert.equal(button.attributes["aria-pressed"], String(language === expected));
        }
    });
}

test("an unknown stored language keeps the current one and shows RU in the form", async () => {
    const app = await openPage(profileServer("XX"), { ...signedIn(), finance_tracker_language: "en" });
    assert.equal(app.run("i18n.getLanguage()"), "en");
    assert.equal(app.el("settings-language").value, "RU");
});

test("switching KZ → EN → RU → KZ is saved and survives a reload each time", async () => {
    const profile = profileServer("RU");
    let app = await openPage(profile);
    const order = [["kk", "KZ"], ["en", "EN"], ["ru", "RU"], ["kk", "KZ"]];
    for (const [language, code] of order) {
        await app.buttons[language].onclick();
        await settle();
        assert.equal(app.run("i18n.getLanguage()"), language, `${language} is applied at once`);
        assert.equal(profile.server.language, code, `${code} is stored on the server`);
        assert.equal(app.el("settings-language").value, code);
        assert.equal(app.store.get("finance_tracker_language"), language);

        // Reload: a fresh page with the same browser storage and server profile.
        app = await openPage(profile, Object.fromEntries(app.store));
        assert.equal(app.run("i18n.getLanguage()"), language, `${language} after reload`);
        assert.equal(app.el("settings-language").value, code);
    }
    assert.deepEqual(profile.server.saved, ["KZ", "EN", "RU", "KZ"]);
    assert.equal(app.run('i18n.t("settings")'), "Баптаулар", "Kazakh texts are really used");
});

test("saving KZ in the settings form switches the interface to Kazakh at once", async () => {
    const profile = profileServer("RU");
    const app = await openPage(profile);
    app.el("settings-language").value = "KZ";
    await app.el("save-settings-btn").onclick();
    assert.equal(app.run("i18n.getLanguage()"), "kk");
    assert.equal(profile.server.language, "KZ");
});

test("before sign-in the switcher changes the language without calling the server", async () => {
    const profile = profileServer("RU");
    const app = await openPage(profile, {});
    await app.buttons.kk.onclick();
    assert.equal(app.run("i18n.getLanguage()"), "kk");
    assert.ok(!app.calls.some((call) => call.path === "/auth/profile"));
});

// Export ----------------------------------------------------------------------

const exportLinks = (app) => app.context.document.body.children.filter((node) => node.tagName === "A");

test("export sends the session token and saves the file", async () => {
    const app = await boot({ "GET /export/csv": [200, "ID,Дата"] });
    await app.el("export-csv-btn").onclick();
    const call = app.calls.find((entry) => entry.path === "/export/csv?period=all");
    assert.ok(call, "the export is requested for the current period");
    assert.equal(call.headers.Authorization, "Bearer test-token");
    assert.equal(app.downloads.length, 1);
    assert.equal(app.downloads[0].blob.blobOf, "ID,Дата");
    const [link] = exportLinks(app);
    assert.equal(link.download, "finance_export_all.csv");
    assert.ok(link.clicked && link.removed, "the file is saved and the helper link removed");
});

test("Excel and PDF exports use their own file names", async () => {
    const app = await boot({ "GET /export/excel": [200, "x"], "GET /export/pdf": [200, "y"] });
    await app.el("export-excel-btn").onclick();
    await app.el("export-pdf-btn").onclick();
    assert.deepEqual(exportLinks(app).map((link) => link.download), ["finance_export_all.xlsx", "finance_export_all.pdf"]);
});

test("a failed export shows a notification instead of leaving the app", async () => {
    const app = await boot({ "GET /export/excel": [500, { detail: "openpyxl не установлен" }] });
    await app.el("export-excel-btn").onclick();
    assert.equal(app.downloads.length, 0);
    assert.equal(app.el("notification").className, "notification error");
    assert.equal(notice(app), app.run('i18n.translateApiError("", 500)'));
    assert.equal(app.store.get("finance_tracker_token"), "test-token", "still signed in");
});

test("an export that finishes after sign-out is not saved", async () => {
    const late = deferred();
    const app = await boot({ "GET /export/csv": () => late.promise });
    const pending = app.el("export-csv-btn").onclick();
    await signOut(app);
    late.release([200, "previous user's data"]);
    await pending;
    assert.equal(app.downloads.length, 0);
});

// Password change, double submission, sign-in errors ---------------------------

test("after a password change the session continues on the new token", async () => {
    const app = await boot({
        "PUT /auth/password": [200, { message: "Пароль изменен", access_token: "rotated-token", token_type: "bearer" }]
    });
    app.el("current-password").value = "secret12";
    app.el("new-password").value = "secret34";
    await app.el("change-password-btn").onclick();
    assert.equal(app.store.get("finance_tracker_token"), "rotated-token");
    await app.run("loadDashboard()");
    const last = app.calls[app.calls.length - 1];
    assert.equal(last.headers.Authorization, "Bearer rotated-token");
    assert.equal(app.el("current-password").value, "");
});

for (const [button, path, fill] of [
    ["save-operation-btn", "POST /operations", (app) => { app.el("operation-amount").value = "10"; app.el("operation-category").value = "Еда"; }],
    ["save-budget-btn", "POST /budgets", (app) => { app.el("budget-category").value = "Еда"; app.el("budget-amount").value = "100"; }],
    ["save-goal-btn", "POST /goals", (app) => { app.el("goal-title").value = "Отпуск"; app.el("goal-target").value = "1000"; }]
]) {
    test(`a double click on ${button} saves once`, async () => {
        const slow = deferred();
        const app = await boot({ [path]: () => slow.promise });
        fill(app);
        const first = app.el(button).onclick();
        const second = app.el(button).onclick();
        assert.equal(app.el(button).disabled, true, "the button waits for the request");
        slow.release([201, { message: "ok", notification: null }]);
        await Promise.all([first, second]);
        await settle();
        const [method, route] = path.split(" ");
        assert.equal(app.calls.filter((call) => call.method === method && call.path === route).length, 1);
        assert.equal(app.el(button).disabled, false, "the button is usable again");
    });
}

test("a failed save re-enables the button", async () => {
    const app = await boot({ "POST /operations": [422, { detail: "Сумма должна быть больше нуля" }] });
    app.el("operation-amount").value = "0";
    await app.el("save-operation-btn").onclick();
    assert.equal(app.el("save-operation-btn").disabled, false);
});

test("a sign-up rejected by field validation shows a readable message", async () => {
    const app = await boot({
        "POST /auth/register": [422, { detail: [{ type: "string_too_short", loc: ["body", "password"], msg: "String should have at least 6 characters" }] }]
    }, { storage: {} });
    app.run('authMode = "register"');
    app.el("auth-email").value = "new@example.test";
    app.el("auth-password").value = "1";
    await app.el("auth-form").onsubmit({ preventDefault() {} });
    assert.equal(app.el("auth-error").textContent, app.run('i18n.translateApiError("", 422)'));
    assert.ok(!app.el("auth-error").textContent.includes("[object"));
});

test("a sign-up for a taken email keeps the server message", async () => {
    const app = await boot({ "POST /auth/register": [400, { detail: "Пользователь с таким email уже существует" }] }, { storage: {} });
    app.run('authMode = "register"');
    app.el("auth-email").value = "taken@example.test";
    app.el("auth-password").value = "secret12";
    await app.el("auth-form").onsubmit({ preventDefault() {} });
    assert.equal(app.el("auth-error").textContent, "Пользователь с таким email уже существует");
});

test("the object URL of a saved export is released", async () => {
    const app = await boot({ "GET /export/csv": [200, "ID"] });
    await app.el("export-csv-btn").onclick();
    assert.deepEqual(app.revoked, [], "not before the browser has started the download");
    app.timers.forEach((callback) => callback());
    assert.deepEqual(app.revoked, ["blob:finance.test/1"]);
});

test("a second click on an export button while it runs sends no second request", async () => {
    const slow = deferred();
    const app = await boot({ "GET /export/pdf": () => slow.promise });
    const first = app.el("export-pdf-btn").onclick();
    const second = app.el("export-pdf-btn").onclick();
    assert.equal(app.el("export-pdf-btn").disabled, true);
    slow.release([200, "pdf"]);
    await Promise.all([first, second]);
    assert.equal(app.calls.filter((call) => call.path.startsWith("/export/pdf")).length, 1);
    assert.equal(app.downloads.length, 1);
    assert.equal(app.el("export-pdf-btn").disabled, false);
});

for (const status of [401, 404, 422, 500, 503]) {
    test(`an export answered with HTTP ${status} is never saved as a file`, async () => {
        const app = await boot({ "GET /export/csv": [status, { detail: "x" }] });
        await app.el("export-csv-btn").onclick();
        assert.equal(app.downloads.length, 0);
        assert.equal(exportLinks(app).length, 0);
        assert.equal(app.el("export-csv-btn").disabled, false);
    });
}

// Budget alert switch in the settings -------------------------------------------

const meWith = (settings) => ({ "GET /auth/me": [200, { id: 1, email: USER, settings: { language: "RU", theme: "light", currency: "KZT", ...settings } }] });
const profileCalls = (app) => app.calls.filter((call) => call.path === "/auth/profile");

test("the in-app budget alert switch follows the account's setting", async () => {
    const off = await boot(meWith({ budget_alerts_inapp: false }));
    assert.equal(off.el("settings-alerts-inapp").checked, false);
    const on = await boot(meWith({ budget_alerts_inapp: true }));
    assert.equal(on.el("settings-alerts-inapp").checked, true);
});

test("an account without the setting has the switch on", async () => {
    const app = await boot();
    assert.equal(app.el("settings-alerts-inapp").checked, true);
});

test("saving the settings sends the switch as a boolean", async () => {
    const app = await boot({ ...meWith({ budget_alerts_inapp: true }), "PUT /auth/profile": [200, {}] });
    app.el("settings-alerts-inapp").checked = false;
    await app.el("save-settings-btn").onclick();
    assert.equal(JSON.parse(profileCalls(app)[0].body).budget_alerts_inapp, false);
    app.el("settings-alerts-inapp").checked = true;
    await app.el("save-settings-btn").onclick();
    assert.equal(JSON.parse(profileCalls(app)[1].body).budget_alerts_inapp, true);
});

test("signing out puts the switch back to its default for the next account", async () => {
    const app = await boot(meWith({ budget_alerts_inapp: false }));
    assert.equal(app.el("settings-alerts-inapp").checked, false);
    await signOut(app);
    assert.equal(app.el("settings-alerts-inapp").checked, true);
});

test("the alert texts exist in every language and fill in their values", () => {
    const values = { category: "Еда", percent: 85, spent: "850 ₸", amount: "1 000 ₸" };
    for (const language of ["ru", "en", "kk"]) {
        for (const key of ["budgetAlertWarning", "budgetAlertLimit", "budgetAlertExceeded", "budgetAlertsInApp"]) {
            const text = vm.runInContext(`i18n.setLanguage(${JSON.stringify(language)}); i18n.t(${JSON.stringify(key)}, ${JSON.stringify(values)})`,
                createApp().context);
            assert.ok(text && text !== key, `${language}.${key} is translated`);
            assert.ok(!/\{\w+\}/.test(text), `${language}.${key} has no unfilled placeholder: ${text}`);
        }
    }
});

// Budget alerts shown after a save ---------------------------------------------------

const ALERT_85 = { budget_id: 1, category: "Еда", month: "2026-10", level: "warning", percent: 85, spent: 850, amount: 1000 };
const ALERT_LIMIT = { budget_id: 1, category: "Еда", month: "2026-10", level: "limit", percent: 100, spent: 1000, amount: 1000 };
const ALERT_OVER = { budget_id: 2, category: "Дом", month: "2026-10", level: "exceeded", percent: 120.5, spent: 1205, amount: 1000 };
const saveExpense = async (app) => {
    app.el("operation-amount").value = "10";
    app.el("operation-category").value = "Еда";
    await app.el("save-operation-btn").onclick();
};
const toast = (app) => ({ text: app.el("notification-message").textContent, className: app.el("notification").className, hidden: app.el("notification").hidden });

test("a warning alert is shown in the interface language, with the category translated", async () => {
    const texts = {};
    for (const language of ["ru", "en", "kk"]) {
        const app = await boot({ "POST /operations": [201, { message: "ok", notification: null, alerts: [ALERT_85] }] });
        app.run(`i18n.setLanguage("${language}")`);
        await saveExpense(app);
        texts[language] = toast(app);
        assert.equal(texts[language].className, "notification warning");
        assert.equal(texts[language].hidden, false);
    }
    assert.match(texts.ru.text, /^Бюджет «Еда»: израсходовано 85%/);
    assert.match(texts.en.text, /^Budget “Food”: 85% used/);
    assert.match(texts.kk.text, /^«Тамақ» бюджеті: 85% жұмсалды/);
    assert.ok(!/\{\w+\}/.test(texts.ru.text + texts.en.text + texts.kk.text), "no placeholder is left unfilled");
});

test("limit and exceeded alerts have their own text and style", async () => {
    const limit = await boot({ "POST /operations": [201, { message: "ok", alerts: [ALERT_LIMIT] }] });
    await saveExpense(limit);
    assert.match(toast(limit).text, /исчерпан/);
    assert.equal(toast(limit).className, "notification warning");
    const over = await boot({ "POST /operations": [201, { message: "ok", alerts: [ALERT_OVER] }] });
    await saveExpense(over);
    assert.match(toast(over).text, /превышен/);
    assert.equal(toast(over).className, "notification error");
});

test("several alerts are shown together and the toast takes the worst style", async () => {
    const app = await boot({ "POST /operations": [201, { message: "ok", alerts: [ALERT_85, ALERT_OVER] }] });
    await saveExpense(app);
    assert.match(toast(app).text, /Еда.*•.*Дом/);
    assert.equal(toast(app).className, "notification error");
});

test("without alerts the usual success message is shown", async () => {
    const app = await boot({ "POST /operations": [201, { message: "ok", notification: null, alerts: [] }] });
    await saveExpense(app);
    assert.equal(toast(app).text, app.run('i18n.t("addedSuccess")'));
    assert.equal(toast(app).className, "notification success");
});

test("an empty alerts list wins over a stale notification text", async () => {
    // The server applies the account's switch to `alerts`; a switched-off account must see nothing.
    const app = await boot({ "POST /operations": [201, { message: "ok", notification: "⚠️ Бюджет на Еда превышен!", alerts: [] }] });
    await saveExpense(app);
    assert.equal(toast(app).text, app.run('i18n.t("addedSuccess")'));
});

test("a server that only sends the notification sentence is still understood", async () => {
    const app = await boot({ "POST /operations": [201, { message: "ok", notification: "⚠️ Бюджет на Еда превышен!" }] });
    await saveExpense(app);
    assert.equal(toast(app).text, "⚠️ Бюджет на Еда превышен!");
});

test("editing an operation shows the alerts of the update", async () => {
    const app = await boot({ "PUT /operations/20": [200, { message: "ok", notification: null, alerts: [ALERT_85] }] });
    app.run("editingOperationId = 20");
    await saveExpense(app);
    assert.match(toast(app).text, /израсходовано 85%/);
});

test("deleting an operation or refreshing the page never shows an alert", async () => {
    const app = await boot({ "DELETE /operations/20": [200, { message: "ok" }], "GET /budgets": [200, [
        { id: 1, category: "Еда", amount: 1000, spent: 950, percent: 95, level: 1, exceeded: false }] ] });
    await app.run("deleteOperation(20)");
    assert.equal(toast(app).text, app.run('i18n.t("deletedSuccess")'));
    await app.run("loadDashboard()");
    await app.run("loadBudgets()");
    assert.doesNotMatch(toast(app).text, /израсходовано|превышен|исчерпан/);
});

test("a confirmed import shows the alerts it caused after the dashboard has reloaded", async () => {
    const app = createApp({
        ids: [...DASHBOARD_IDS, ...IMPORT_IDS],
        routes: { ...apiRoutes(OLDER), "POST /import/csv/commit": [200, { imported: 2, duplicate_rows: 0, error_rows: 0, errors: [], columns: [], notification: null, alerts: [ALERT_OVER] }] },
        storage: signedIn()
    });
    app.run('importState.file = "date,amount\\n"; importState.preview = { valid_rows: 2, duplicate_rows: 0, rows: [], errors: [], error_rows: 0, total_rows: 2 }');
    await app.run("confirmImport()");
    assert.match(toast(app).text, /Дом.*превышен/);
    assert.equal(toast(app).className, "notification error");
});

test("a repeated import, which stores nothing, shows no alert", async () => {
    const app = createApp({
        ids: [...DASHBOARD_IDS, ...IMPORT_IDS],
        routes: { ...apiRoutes(OLDER), "POST /import/csv/commit": [200, { imported: 0, duplicate_rows: 2, error_rows: 0, errors: [], columns: [], notification: null, alerts: [] }] },
        storage: signedIn()
    });
    app.run('importState.file = "date,amount\\n"; importState.preview = { valid_rows: 2, duplicate_rows: 2, rows: [], errors: [], error_rows: 0, total_rows: 2 }');
    await app.run("confirmImport()");
    assert.doesNotMatch(toast(app).text, /израсходовано|превышен|исчерпан/);
});

test("budget bars are amber from 80 percent and red when exceeded", async () => {
    const budgets = [
        { id: 1, category: "Еда", amount: 1000, spent: 100, percent: 10, level: 0, exceeded: false },
        { id: 2, category: "Дом", amount: 1000, spent: 850, percent: 85, level: 1, exceeded: false },
        { id: 3, category: "Здоровье", amount: 1000, spent: 1000, percent: 100, level: 2, exceeded: false },
        { id: 4, category: "Транспорт", amount: 1000, spent: 1500, percent: 150, level: 3, exceeded: true }
    ];
    const app = createApp({ routes: apiRoutes(OLDER, { "GET /budgets": [200, budgets] }) });
    await app.run("loadBudgets()");
    const fills = allNodes(app.elements.get("budgets-list")).filter((node) => String(node.className).startsWith("budget-progress-fill"));
    assert.deepEqual(fills.map((node) => node.className.trim()), [
        "budget-progress-fill", "budget-progress-fill warning", "budget-progress-fill warning", "budget-progress-fill exceeded"
    ]);
});

// User categories ----------------------------------------------------------------------

const CATEGORY_LIST = {
    limit: 50,
    categories: [
        { id: null, kind: "Расход", name: "Еда", builtin: true },
        { id: 5, kind: "Расход", name: "Хобби", builtin: false },
        { id: 6, kind: "Доход", name: "Подарки", builtin: false }
    ]
};
const optionValues = (app, id) => app.el(id).children.map((option) => option.value);
const namesOf = (app, type) => JSON.parse(app.run(`JSON.stringify(categoryNames(${JSON.stringify(type)}))`));

test("the user's categories are offered by kind in the form and the budget form, and in the filter", async () => {
    const app = await boot({ "GET /categories": [200, CATEGORY_LIST] });
    assert.deepEqual(namesOf(app, "Расход"), ["Еда", "Транспорт", "Дом", "Развлечения", "Здоровье", "Другое", "Хобби"]);
    assert.deepEqual(namesOf(app, "Доход"), ["Зарплата", "Фриланс", "Бизнес", "Инвестиции", "Другое", "Подарки"]);
    // The stub form is on its default kind, income.
    assert.ok(optionValues(app, "operation-category").includes("Подарки"));
    assert.ok(!optionValues(app, "operation-category").includes("Хобби"));
    assert.ok(optionValues(app, "operation-category-filter").includes("Хобби"));
    assert.ok(optionValues(app, "operation-category-filter").includes("Подарки"));
    assert.ok(optionValues(app, "budget-category").includes("Хобби"));
    assert.ok(!optionValues(app, "budget-category").includes("Подарки"));
    assert.equal(allNodes(app.el("my-categories-list")).filter((node) => node.className === "my-category-item").length, 2);
});

test("when the categories cannot be loaded the built-in ones work and no error is shown", async () => {
    const app = await boot({ "GET /categories": [500, { detail: "x" }] });
    assert.deepEqual(namesOf(app, "Расход"), ["Еда", "Транспорт", "Дом", "Развлечения", "Здоровье", "Другое"]);
    assert.equal(notice(app), "");
    assert.equal(historyItems(app).length, 8, "the dashboard itself still loads");
});

test("signing out removes the previous user's categories from every list", async () => {
    const app = await boot({ "GET /categories": [200, CATEGORY_LIST] });
    assert.ok(optionValues(app, "operation-category-filter").includes("Хобби"));
    await signOut(app);
    assert.equal(app.run("userCategories.length"), 0);
    assert.ok(!optionValues(app, "operation-category-filter").includes("Хобби"));
    assert.ok(!optionValues(app, "operation-category").includes("Подарки"));
    assert.ok(!optionValues(app, "budget-category").includes("Хобби"));
    assert.ok(!textOfPage(app).includes("Хобби"));
});

test("adding a category sends its kind and trimmed name, then shows it", async () => {
    let created = false;
    const app = await boot({
        "GET /categories": () => [200, created ? CATEGORY_LIST : { limit: 50, categories: [] }],
        "POST /categories": () => { created = true; return [201, { message: "ok", category: CATEGORY_LIST.categories[1] }]; }
    });
    app.el("new-category-kind").value = "Расход";
    app.el("new-category-name").value = "  Хобби ";
    await app.el("add-category-btn").onclick();
    const call = app.calls.find((entry) => entry.method === "POST" && entry.path === "/categories");
    assert.deepEqual(JSON.parse(call.body), { kind: "Расход", name: "Хобби" });
    assert.equal(app.el("new-category-name").value, "");
    assert.ok(optionValues(app, "operation-category-filter").includes("Хобби"));
    assert.equal(notice(app), app.run('i18n.t("categoryAdded")'));
});

test("a refused category shows the server message in the interface language", async () => {
    const app = await boot({ "POST /categories": [409, { detail: "Категория с таким названием уже существует" }] });
    app.run('i18n.setLanguage("en")');
    app.el("new-category-name").value = "Хобби";
    await app.el("add-category-btn").onclick();
    assert.equal(notice(app), "A category with this name already exists");
    assert.equal(app.el("new-category-name").value, "Хобби", "the typed name is kept");
});

test("renaming and deleting a category reload the dashboard, which carries the new names", async () => {
    const app = await boot({
        "GET /categories": [200, CATEGORY_LIST],
        "PUT /categories/5": [200, { message: "ok", operations_updated: 2, budgets_updated: 0 }],
        "DELETE /categories/6": [200, { message: "ok", moved_to: "Другое", operations_moved: 1 }]
    });
    app.context.prompt = () => "  Увлечения ";
    let before = app.calls.length;
    await app.run("renameCategory({ id: 5, kind: 'Расход', name: 'Хобби' })");
    await settle();
    let sent = app.calls.slice(before);
    assert.deepEqual(JSON.parse(sent.find((call) => call.method === "PUT").body), { name: "Увлечения" });
    assert.ok(sent.some((call) => call.path === "/operations?period=all"), "operations are reloaded");
    assert.equal(notice(app), app.run('i18n.t("categoryRenamed")'));

    before = app.calls.length;
    await app.run("deleteCategory({ id: 6, kind: 'Доход', name: 'Подарки' })");
    await settle();
    sent = app.calls.slice(before);
    assert.ok(sent.some((call) => call.method === "DELETE" && call.path === "/categories/6"));
    assert.ok(sent.some((call) => call.path === "/operations?period=all"));
    assert.equal(notice(app), app.run('i18n.t("categoryDeleted")'));
});

test("a rename that is cancelled or unchanged sends nothing", async () => {
    const app = await boot({ "GET /categories": [200, CATEGORY_LIST] });
    const before = app.calls.length;
    for (const answer of [null, "", "   ", "Хобби"]) {
        app.context.prompt = () => answer;
        await app.run("renameCategory({ id: 5, kind: 'Расход', name: 'Хобби' })");
    }
    assert.equal(app.calls.length, before);
});

test("a categories response that arrives after sign-out is not drawn", async () => {
    const late = deferred();
    const app = await boot({ "GET /categories": () => late.promise });
    await signOut(app);
    late.release([200, CATEGORY_LIST]);
    await settle();
    assert.equal(app.run("userCategories.length"), 0);
    assert.ok(!optionValues(app, "operation-category-filter").includes("Хобби"));
});

test("the category texts and server errors exist in all three languages", () => {
    const app = createApp();
    const messages = ["Название категории должно содержать от 1 до 50 символов", "Название категории содержит недопустимые символы",
        "Название совпадает со встроенной категорией", "Достигнут лимит пользовательских категорий (50)",
        "Категория с таким названием уже существует", "Категория не найдена",
        "На категорию назначен бюджет: удалите или измените бюджет и повторите"];
    const keys = ["categoriesTitle", "myCategories", "categoryName", "addCategory", "noCustomCategories", "renameCategory",
        "deleteCategory", "renameCategoryPrompt", "deleteCategoryConfirm", "categoryAdded", "categoryRenamed", "categoryDeleted"];
    const unmapped = app.run(`${JSON.stringify(messages)}.filter((message) => !i18n.apiErrorKeys[message]).join("|")`);
    assert.equal(unmapped, "", "every category error the server sends has a translation key");
    const missing = app.run(`(() => {
        const all = i18n.getTranslations();
        const keys = ${JSON.stringify(keys)}.concat(Object.values(i18n.apiErrorKeys).filter((key) => /^category/.test(key)));
        const missing = [];
        for (const language of ["ru", "en", "kk"]) {
            for (const key of keys) {
                const text = all[language][key];
                if (!text || /\\{\\w+\\}/.test(i18n.setLanguage(language) && i18n.t(key, { name: "A", target: "B" }))) missing.push(language + ":" + key);
            }
        }
        return missing.join(",");
    })()`);
    assert.equal(missing, "");
});

// Account settings ---------------------------------------------------------------------

test("the account settings are inside the settings tab, not hidden, with the languages in KZ | EN | RU order", () => {
    const html = fs.readFileSync(path.join(FRONTEND, "index.html"), "utf8");
    const start = html.indexOf('data-mobile-tab="settings"');
    const end = html.indexOf("</main>");
    assert.ok(start > 0 && end > start, "the settings tab exists");
    const tab = html.slice(start, end);
    assert.ok(!/id="tab-settings"[^>]*\bhidden\b/.test(html), "the settings pane is not hidden");
    for (const id of ["tab-settings", "settings-language", "settings-theme", "settings-currency", "settings-alerts-inapp",
        "save-settings-btn", "change-password-btn", "delete-account-btn", "my-categories-list"]) {
        assert.ok(tab.includes(`id="${id}"`), `${id} is inside the settings tab`);
    }
    assert.equal(html.split('data-mobile-tab="').length - 1, 5, "five tabs, none added after the settings one");
    const select = tab.match(/<select id="settings-language">([\s\S]*?)<\/select>/)[1];
    assert.deepEqual([...select.matchAll(/value="(\w+)"/g)].map((match) => match[1]), ["KZ", "EN", "RU"]);
    const currencies = tab.match(/<select id="settings-currency">([\s\S]*?)<\/select>/)[1];
    assert.deepEqual([...currencies.matchAll(/value="(\w+)"/g)].map((match) => match[1]), ["KZT", "USD", "EUR", "RUB"]);
});

// Sign-in dialog, sign-out button and calendar in the interface language -------------------

test("changing the language relabels the sign-out button and the dialog button without a reload", async () => {
    const app = await boot();
    const logout = () => allNodes(app.el("user-auth-area")).find((node) => node.id === "logout-btn");
    assert.equal(logout().textContent, "Выйти");
    for (const [language, expected] of [["en", "Sign out"], ["kk", "Шығу"], ["ru", "Выйти"]]) {
        app.run(`changeLanguage("${language}")`);
        assert.equal(logout().textContent, expected, language);
        assert.equal(app.el("auth-submit-btn").textContent, app.run('i18n.t("signIn")'), language);
    }
    app.run('authMode = "register"; changeLanguage("kk")');
    assert.equal(app.el("auth-submit-btn").textContent, "Аккаунт жасау");
});

test("the sign-in button of a signed-out page follows the language too", async () => {
    const app = await boot({}, { storage: {} });
    const label = () => allNodes(app.el("user-auth-area")).find((node) => node.tagName === "BUTTON").textContent;
    assert.equal(label(), "Войти / Регистрация");
    app.run('changeLanguage("en")');
    assert.equal(label(), "Sign in / Register");
    app.run('changeLanguage("kk")');
    assert.equal(label(), "Кіру / Тіркелу");
});

test("the calendar weekdays follow the interface language, Monday first", async () => {
    const app = await boot();
    const headers = () => app.el("calendar-grid").children.filter((node) => node.className === "calendar-day-header").map((node) => node.textContent);
    const expected = (locale) => Array.from({ length: 7 }, (_, index) =>
        new Intl.DateTimeFormat(locale, { weekday: "short" }).format(new Date(2024, 0, 1 + index)));
    for (const [language, locale] of [["en", "en-US"], ["kk", "kk-KZ"], ["ru", "ru-RU"]]) {
        app.run(`changeLanguage("${language}")`);
        assert.deepEqual(headers(), expected(locale), language);
    }
    app.run('changeLanguage("en")');
    assert.deepEqual(headers(), ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]);
});

test("the sign-in dialog translates known server messages and hides unknown ones", async () => {
    const wrong = await boot({ "POST /auth/login": [401, { detail: "Неверный email или пароль" }] }, { storage: {} });
    wrong.run('i18n.setLanguage("en")');
    wrong.el("auth-email").value = "a@example.test";
    wrong.el("auth-password").value = "secret12";
    await wrong.el("auth-form").onsubmit({ preventDefault() {} });
    assert.equal(wrong.el("auth-error").textContent, "Incorrect email or password");

    const taken = await boot({ "POST /auth/register": [400, { detail: "Пользователь с таким email уже существует" }] }, { storage: {} });
    taken.run('authMode = "register"; i18n.setLanguage("kk")');
    taken.el("auth-email").value = "taken@example.test";
    taken.el("auth-password").value = "secret12";
    await taken.el("auth-form").onsubmit({ preventDefault() {} });
    assert.equal(taken.el("auth-error").textContent, "Бұл email бар пайдаланушы бұрыннан тіркелген");

    const broken = await boot({ "POST /auth/login": [500, { detail: "Traceback: secret internals" }] }, { storage: {} });
    broken.run('i18n.setLanguage("en")');
    broken.el("auth-email").value = "a@example.test";
    broken.el("auth-password").value = "secret12";
    await broken.el("auth-form").onsubmit({ preventDefault() {} });
    assert.equal(broken.el("auth-error").textContent, "Server error");
    assert.ok(!broken.el("auth-error").textContent.includes("secret"));
});

test("the success message of signing in and registering is in the interface language", async () => {
    const login = await boot({}, { storage: {} });
    login.run('i18n.setLanguage("en")');
    login.el("auth-email").value = USER;
    login.el("auth-password").value = "secret12";
    await login.el("auth-form").onsubmit({ preventDefault() {} });
    await settle();
    assert.ok(login.calls.some((call) => call.path === "/auth/login"), "the login request was made");
    assert.equal(notice(login), "Signed in successfully!");

    const register = await boot({ "POST /auth/register": [201, { access_token: "t", token_type: "bearer", email: USER }] }, { storage: {} });
    register.run('authMode = "register"; i18n.setLanguage("kk")');
    register.el("auth-email").value = USER;
    register.el("auth-password").value = "secret12";
    await register.el("auth-form").onsubmit({ preventDefault() {} });
    await settle();
    assert.ok(register.calls.some((call) => call.path === "/auth/register"));
    assert.equal(notice(register), "Аккаунт сәтті жасалды!");
});
