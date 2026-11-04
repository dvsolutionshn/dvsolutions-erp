const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const template = fs.readFileSync(path.join(__dirname, '../templates/facturacion/factura_pos_auto_print.html'), 'utf8');
const script = template.match(/<script>([\s\S]*?)<\/script>/)[1];

async function launch(responses) {
    const events = {};
    const timers = [];
    const requests = [];
    let printed = 0;
    let parentPrinted = 0;
    const button = {hidden: true, addEventListener(name, callback) {this[name] = callback;}};
    const frame = {
        src: 'about:blank', dataset: {pdfUrl: '/medical_spa/dashboard/facturacion/1/pdf/vista-previa/'},
        addEventListener(name, callback) {events[name] = callback;},
        contentWindow: {focus() {}, print() {printed += 1;}, addEventListener() {}},
    };
    const title = {};
    const message = {};
    const elements = {ticketFrame: frame, printAgain: button, printTitle: title, printMessage: message};
    const context = {
        document: {getElementById: id => elements[id]},
        fetch: async (url, options) => {
            requests.push({url, options});
            const response = responses.shift();
            if(response instanceof Error) throw response;
            return response;
        },
        URL: {createObjectURL: () => 'blob:verified-invoice', revokeObjectURL() {}},
        window: {setTimeout: callback => timers.push(callback), addEventListener() {}, close() {}, print() {parentPrinted += 1;}},
    };
    vm.runInNewContext(script, context);
    await new Promise(resolve => setImmediate(resolve));
    return {frame, button, title, message, requests, events, timers,
        count: () => ({frame: printed, parent: parentPrinted}),
        flush: () => timers.splice(0).forEach(callback => callback()),
    };
}

function documentResponse(type, {ok = true, redirected = false, size = 100} = {}) {
    return {ok, redirected, headers: {get: () => type}, blob: async () => ({size})};
}

test('dashboard HTML, login redirects and denied/empty responses never reach the printer', async t => {
    const cases = {
        dashboard: documentResponse('text/html'),
        forbidden: documentResponse('text/html', {ok: false}),
        redirect: documentResponse('application/pdf', {redirected: true}),
        expiredSession: new TypeError('Redirect rejected'),
        empty: documentResponse('application/pdf', {size: 0}),
    };
    for(const [name, response] of Object.entries(cases)) {
        await t.test(name, async () => {
            const page = await launch([response]);
            page.events.load();
            page.flush();
            assert.equal(page.frame.src, 'about:blank');
            assert.deepEqual(page.count(), {frame: 0, parent: 0});
            assert.equal(page.button.hidden, false);
            assert.equal(page.button.textContent, 'Reintentar');
            assert.match(page.message.textContent, /Imprimir o descargar facturas/);
        });
    }
});

test('only the verified invoice PDF prints, after its frame loads', async () => {
    const page = await launch([documentResponse('application/pdf; charset=binary')]);
    assert.equal(page.requests[0].url, page.frame.dataset.pdfUrl);
    assert.equal(page.requests[0].options.redirect, 'error');
    assert.equal(page.requests[0].options.credentials, 'same-origin');
    assert.equal(page.frame.src, 'blob:verified-invoice');
    assert.deepEqual(page.count(), {frame: 0, parent: 0});
    page.events.load();
    page.flush();
    assert.deepEqual(page.count(), {frame: 1, parent: 0});
    page.events.load();
    page.flush();
    assert.deepEqual(page.count(), {frame: 1, parent: 0});
    page.button.click();
    assert.deepEqual(page.count(), {frame: 2, parent: 0});
});

test('a failed load can retry without printing the error screen', async () => {
    const page = await launch([documentResponse('text/html'), documentResponse('application/pdf')]);
    page.button.click();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(page.requests.length, 2);
    assert.deepEqual(page.count(), {frame: 0, parent: 0});
    page.events.load();
    page.flush();
    assert.deepEqual(page.count(), {frame: 1, parent: 0});
});
