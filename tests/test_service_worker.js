// The service worker must cache only the app shell, never API responses with
// the user's data, and must drop caches of previous versions.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const SOURCE = fs.readFileSync(path.join(__dirname, "..", "frontend", "service-worker.js"), "utf8");

function loadWorker(existingCaches = []) {
    const listeners = {};
    const deleted = [];
    const requests = [];
    const context = {
        URL,
        Promise,
        Request: class { constructor(url, init = {}) { this.url = url; this.cache = init.cache; } },
        fetch: async (request) => { requests.push(request); return { ok: true, clone() { return this; } }; },
        caches: {
            open: async () => ({ addAll: async (list) => { requests.push(...list); }, put: async () => {} }),
            keys: async () => existingCaches,
            delete: async (key) => { deleted.push(key); return true; },
            match: async () => undefined
        }
    };
    context.self = {
        location: { origin: "https://finance.test" },
        addEventListener: (type, handler) => { listeners[type] = handler; },
        skipWaiting() {},
        clients: { claim() {} }
    };
    vm.createContext(context);
    vm.runInContext(SOURCE, context);
    return { listeners, deleted, requests, cacheName: vm.runInContext("CACHE_NAME", context) };
}

function intercepts(worker, url, method = "GET") {
    let responded = false;
    worker.listeners.fetch({ request: { url, method }, respondWith: (promise) => { responded = true; promise.catch(() => {}); } });
    return responded;
}

test("API requests bypass the service worker cache", () => {
    const worker = loadWorker();
    for (const api of ["/operations?period=all", "/balance", "/statistics?period=all", "/budgets", "/goals", "/auth/me"]) {
        assert.equal(intercepts(worker, `https://finance.test${api}`), false, api);
    }
    assert.equal(intercepts(worker, "https://finance.test/import/csv/commit", "POST"), false);
    assert.equal(intercepts(worker, "https://other.example/static/script.js"), false);
});

test("the app shell is still served network-first with an offline copy", () => {
    const worker = loadWorker();
    for (const shell of ["/", "/static/script.js", "/static/i18n.js", "/static/style.css"]) {
        assert.equal(intercepts(worker, `https://finance.test${shell}`), true, shell);
    }
});

test("activation removes caches of earlier versions", async () => {
    const worker = loadWorker(["finance-tracker-v9", "finance-tracker-v10", "finance-tracker-v11"]);
    assert.equal(worker.cacheName, "finance-tracker-v11");
    let pending;
    worker.listeners.activate({ waitUntil: (promise) => { pending = promise; } });
    await pending;
    assert.deepEqual(worker.deleted, ["finance-tracker-v9", "finance-tracker-v10"]);
});

test("the app shell bypasses copies the browser cached before the fix", async () => {
    const worker = loadWorker();
    let installing;
    worker.listeners.install({ waitUntil: (promise) => { installing = promise; } });
    await installing;
    assert.ok(worker.requests.length > 0);
    assert.ok(worker.requests.every((request) => request.cache === "reload"), "install skips the HTTP cache");

    worker.requests.length = 0;
    intercepts(worker, "https://finance.test/static/script.js");
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(worker.requests[0].cache, "no-cache", "fetch revalidates with the server");
});
