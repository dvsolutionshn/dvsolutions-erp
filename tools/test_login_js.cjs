"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../core/static/core/login.js"), "utf8");
const oldToken = "a".repeat(64);
const newToken = "b".repeat(64);

function element(tagName, initial) {
    let html = initial;
    return {
        tagName,
        disabled: false,
        value: initial,
        hidden: false,
        attrs: {},
        setAttribute(name, value) { this.attrs[name] = value; },
        get innerHTML() { return html; },
        set innerHTML(value) { html = value; },
        get textContent() { return html; },
        set textContent(value) { html = value; },
    };
}

function harness({ respectAbort = true, fetchAvailable = true, abortAvailable = true } = {}) {
    const token = { value: oldToken };
    const password = { value: "test-password-that-must-stay-in-form" };
    const buttons = [element("BUTTON", "<span>Ingresar</span>"), element("INPUT", "Entrar")];
    const initiallyDisabled = element("BUTTON", "No disponible");
    initiallyDisabled.disabled = true;
    buttons.push(initiallyDisabled);
    const attrs = {};
    const formListeners = {};
    const windowListeners = {};
    const timers = new Map();
    const requests = [];
    const posts = [];
    const form = {
        dataset: { csrfRefreshUrl: "/login/csrf-token/" },
        method: "post",
        valid: true,
        validityReports: 0,
        attrs,
        reportValidity() {
            this.validityReports += 1;
            return this.valid && password.value !== "";
        },
        querySelector(selector) {
            assert.equal(selector, 'input[name="csrfmiddlewaretoken"]');
            return token;
        },
        querySelectorAll() { return buttons; },
        setAttribute(name, value) { attrs[name] = value; },
        removeAttribute(name) { delete attrs[name]; },
        prepend(status) { this.status = status; },
        addEventListener(name, listener) { formListeners[name] = listener; },
    };
    function fetch(url, options) {
        let resolve;
        let reject;
        const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
        const request = {
            url,
            options,
            resolveToken(value = newToken, ok = true) {
                resolve({ ok, async json() { return { csrfToken: value }; } });
            },
            resolveInvalidJson() {
                resolve({ ok: true, async json() { throw new SyntaxError("Invalid JSON"); } });
            },
            reject,
        };
        if (respectAbort) {
            options.signal.addEventListener("abort", () => reject(new Error("Aborted")), { once: true });
        }
        requests.push(request);
        return promise;
    }
    let nextTimer = 1;
    const window = {
        fetch: fetchAvailable ? fetch : undefined,
        AbortController: abortAvailable ? AbortController : undefined,
        addEventListener(name, listener) { windowListeners[name] = listener; },
        setTimeout(callback, delay) {
            const id = nextTimer++;
            timers.set(id, { callback, delay });
            return id;
        },
        clearTimeout(id) { timers.delete(id); },
    };
    const storage = {
        setItem() { assert.fail("Login must not persist credentials"); },
        getItem() { assert.fail("Login must not read persisted credentials"); },
    };
    vm.runInNewContext(source, {
        document: {
            querySelectorAll() { return [form]; },
            createElement() { return element("P", ""); },
        },
        window,
        fetch,
        AbortController,
        HTMLFormElement: {
            prototype: {
                submit() {
                    assert.equal(this, form);
                    posts.push({ method: form.method, csrfToken: token.value, password: password.value });
                },
            },
        },
        localStorage: storage,
        sessionStorage: storage,
    }, { filename: "login.js" });
    function submit({ defaultPrevented = false } = {}) {
        const event = {
            defaultPrevented,
            preventDefault() { this.defaultPrevented = true; },
        };
        return { event, done: formListeners.submit ? formListeners.submit(event) : Promise.resolve() };
    }
    return {
        form, token, password, buttons, requests, posts, timers, submit,
        pageshow(persisted = true) { windowListeners.pageshow({ persisted }); },
        fireTimeout() {
            assert.equal(timers.size, 1);
            const timer = [...timers.values()][0];
            assert.equal(timer.delay, 10000);
            timer.callback();
        },
    };
}

function assertRestored(h) {
    assert.equal(h.buttons[0].disabled, false);
    assert.equal(h.buttons[0].innerHTML, "<span>Ingresar</span>");
    assert.equal(h.buttons[1].disabled, false);
    assert.equal(h.buttons[1].value, "Entrar");
    assert.equal(h.buttons[2].disabled, true);
    assert.equal(h.form.attrs["aria-busy"], undefined);
}

test("refreshes same-origin CSRF before exactly one native form POST", async () => {
    const h = harness();
    const attempt = h.submit();
    assert.equal(attempt.event.defaultPrevented, true);
    assert.equal(h.requests.length, 1);
    assert.equal(h.posts.length, 0);
    assert.equal(h.token.value, oldToken);
    assert.equal(h.form.attrs["aria-busy"], "true");
    assert.equal(h.buttons[0].disabled, true);
    assert.equal(h.buttons[0].textContent, "Ingresando…");
    const { url, options } = h.requests[0];
    assert.equal(url, "/login/csrf-token/");
    assert.equal(options.method, "GET");
    assert.equal(options.credentials, "same-origin");
    assert.equal(options.mode, "same-origin");
    assert.equal(options.cache, "no-store");
    assert.equal(options.redirect, "error");
    assert.equal(options.headers.Accept, "application/json");
    assert.equal(options.body, undefined);
    h.requests[0].resolveToken();
    await attempt.done;
    assert.deepEqual(h.posts, [{ method: "post", csrfToken: newToken, password: h.password.value }]);
    assert.equal(h.timers.size, 0);
});

test("rapid button clicks and Enter events cannot create extra requests or POSTs", async () => {
    const h = harness();
    const first = h.submit();
    const second = h.submit();
    const third = h.submit();
    await Promise.all([second.done, third.done]);
    assert.equal(second.event.defaultPrevented, true);
    assert.equal(third.event.defaultPrevented, true);
    assert.equal(h.requests.length, 1);
    h.requests[0].resolveToken();
    await first.done;
    const afterPost = h.submit();
    await afterPost.done;
    assert.equal(h.requests.length, 1);
    assert.equal(h.posts.length, 1);
});

test("clearing a required field during refresh prevents POST and permits a corrected retry", async () => {
    const h = harness();
    const first = h.submit();
    h.password.value = "";
    h.requests[0].resolveToken();
    await first.done;
    assert.equal(h.form.validityReports, 1);
    assert.equal(h.posts.length, 0);
    assert.equal(h.token.value, oldToken);
    assertRestored(h);
    assert.equal(h.form.status.hidden, true);
    assert.equal(h.timers.size, 0);
    h.password.value = "corrected-test-password";
    const retry = h.submit();
    h.requests[1].resolveToken();
    await retry.done;
    assert.equal(h.form.validityReports, 2);
    assert.equal(h.posts.length, 1);
    assert.equal(h.posts[0].password, "corrected-test-password");
});

test("network errors preserve credentials, restore controls, and allow retry", async () => {
    const h = harness();
    const first = h.submit();
    h.requests[0].reject(new Error("Offline"));
    await first.done;
    assert.equal(h.posts.length, 0);
    assert.equal(h.token.value, oldToken);
    assert.equal(h.password.value, "test-password-that-must-stay-in-form");
    assertRestored(h);
    assert.equal(h.form.status.hidden, false);
    assert.match(h.form.status.textContent, /intenta nuevamente/);
    const retry = h.submit();
    assert.equal(h.form.status.hidden, true);
    assert.equal(h.requests.length, 2);
    h.requests[1].resolveToken();
    await retry.done;
    assert.equal(h.posts.length, 1);
});

test("HTTP failure never submits the password", async () => {
    const h = harness();
    const attempt = h.submit();
    h.requests[0].resolveToken(newToken, false);
    await attempt.done;
    assert.equal(h.posts.length, 0);
    assertRestored(h);
    assert.equal(h.form.status.hidden, false);
});

test("an invalid JSON response restores the form without posting credentials", async () => {
    const h = harness();
    const attempt = h.submit();
    h.requests[0].resolveInvalidJson();
    await attempt.done;
    assert.equal(h.posts.length, 0);
    assert.equal(h.token.value, oldToken);
    assertRestored(h);
    assert.equal(h.form.status.hidden, false);
    assert.equal(h.timers.size, 0);
});

for (const value of [null, 123, "", "x".repeat(63), "x".repeat(65), "!".repeat(64)]) {
    test(`malformed CSRF token ${JSON.stringify(value)} never reaches POST`, async () => {
        const h = harness();
        const attempt = h.submit();
        h.requests[0].resolveToken(value);
        await attempt.done;
        assert.equal(h.posts.length, 0);
        assert.equal(h.token.value, oldToken);
        assertRestored(h);
        assert.equal(h.timers.size, 0);
    });
}

test("10-second timeout aborts refresh and restores a usable form", async () => {
    const h = harness();
    const attempt = h.submit();
    h.fireTimeout();
    await attempt.done;
    assert.equal(h.requests[0].options.signal.aborted, true);
    assert.equal(h.posts.length, 0);
    assertRestored(h);
    assert.equal(h.form.status.hidden, false);
    assert.equal(h.timers.size, 0);
});

test("BFCache restore cancels old refresh; its late result cannot interfere with retry", async () => {
    const h = harness({ respectAbort: false });
    const first = h.submit();
    h.pageshow(true);
    assert.equal(h.requests[0].options.signal.aborted, true);
    assertRestored(h);
    assert.equal(h.form.status.hidden, true);
    const retry = h.submit();
    h.requests[0].resolveToken("c".repeat(64));
    await first.done;
    assert.equal(h.token.value, oldToken);
    assert.equal(h.posts.length, 0);
    assert.equal(h.form.attrs["aria-busy"], "true");
    h.requests[1].resolveToken();
    await retry.done;
    assert.equal(h.posts.length, 1);
    assert.equal(h.posts[0].csrfToken, newToken);
    assert.equal(h.timers.size, 0);
});

test("initial pageshow while assets load does not cancel an active login", async () => {
    const h = harness();
    const attempt = h.submit();
    h.pageshow(false);
    h.requests[0].resolveToken();
    await attempt.done;
    assert.equal(h.requests[0].options.signal.aborted, false);
    assert.equal(h.posts.length, 1);
});

test("returning through BFCache after a completed POST permits a fresh login", async () => {
    const h = harness();
    const first = h.submit();
    h.requests[0].resolveToken();
    await first.done;
    h.pageshow(true);
    assertRestored(h);
    const retry = h.submit();
    h.requests[1].resolveToken("c".repeat(64));
    await retry.done;
    assert.equal(h.requests.length, 2);
    assert.equal(h.posts.length, 2);
    assert.equal(h.posts[1].csrfToken, "c".repeat(64));
});

test("an already prevented submit remains untouched", async () => {
    const h = harness();
    await h.submit({ defaultPrevented: true }).done;
    assert.equal(h.requests.length, 0);
    assertRestored(h);
});

for (const missing of ["fetchAvailable", "abortAvailable"]) {
    test(`without ${missing}, leaves normal browser form submission available`, async () => {
        const h = harness({ [missing]: false });
        const attempt = h.submit();
        await attempt.done;
        assert.equal(attempt.event.defaultPrevented, false);
        assert.equal(h.requests.length, 0);
    });
}
