/* Closed service-worker fault tests. No browser, real fetch, storage or timer. */
'use strict';
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

let checks = 0;
function workerHarness(code, home, behavior, options = {}) {
  const handlers = new Map(), timers = new Map(), calls = [];
  let timerId = 0;
  const self = {
    location: {origin: 'https://dvsolutionshn.com'},
    addEventListener(type, handler) {
      assert(!handlers.has(type), 'Each worker event has one handler');
      handlers.set(type, handler);
    },
    skipWaiting() {},
    clients: {claim: () => Promise.resolve(), matchAll: () => Promise.resolve([]), openWindow: () => Promise.resolve()},
    registration: {showNotification: () => Promise.resolve()},
  };
  const context = {
    self, URL, Response, Promise,
    AbortController: options.noAbort ? undefined : AbortController,
    setTimeout(callback, delay) { const id = ++timerId; timers.set(id, {callback, delay}); return id; },
    clearTimeout(id) { timers.delete(id); },
    fetch(...args) { calls.push(args); return behavior(...args); },
  };
  Object.defineProperty(context, 'caches', {get() { throw new Error('Cache access forbidden'); }});
  Object.defineProperty(context, 'localStorage', {get() { throw new Error('Storage forbidden'); }});
  Object.defineProperty(self, 'caches', {get() { throw new Error('Cache access forbidden'); }});
  vm.runInNewContext(code, context, {timeout: 1000, filename: 'synthetic-service-worker.js'});
  assert.equal(typeof handlers.get('fetch'), 'function');
  assert.equal(typeof handlers.get('notificationclick'), 'function');
  assert.equal(typeof handlers.get('message'), 'function');
  function dispatch(updates = {}) {
    const request = {method: 'GET', mode: 'navigate', credentials: 'same-origin', redirect: 'follow',
      url: 'https://dvsolutionshn.com' + home + '?citas_q=PRIVATE-PATIENT&token=SECRET-QUERY', ...updates};
    let answer, responseCount = 0;
    handlers.get('fetch')({request, respondWith(promise) { answer = promise; responseCount += 1; }});
    assert(responseCount <= 1);
    return {request, answer, responseCount};
  }
  async function deadline() {
    await Promise.resolve();
    assert.equal(timers.size, 1, 'One bounded GET timeout');
    const timer = [...timers.values()][0];
    assert.equal(timer.delay, 20000);
    timers.clear();
    timer.callback();
    await Promise.resolve();
  }
  return {dispatch, calls, timers, deadline};
}

async function expectRecovery(response, home) {
  assert.equal(response.status, 503);
  assert.equal(response.headers.get('Cache-Control'), 'no-store');
  assert.match(response.headers.get('Content-Type'), /text\/html/);
  assert.match(response.headers.get('Content-Security-Policy'), /default-src 'none'/);
  const body = await response.text();
  assert.match(body, /No se pudo abrir la app/);
  assert.match(body, /Volver a intentar/);
  assert(!/<(?:script|form)\b/i.test(body));
  assert(!/PRIVATE-PATIENT|SECRET-QUERY|PRIVATE-EXCEPTION|csrf|sessionid/i.test(body));
  const links = [...body.matchAll(/href="([^"]+)"/g)].map(match => match[1]);
  assert.deepEqual(links, [home]);
  checks += 1;
}

async function testWorker({code, home}) {
  const unhandled = [
    {method: 'POST'}, {method: 'PUT'}, {method: 'HEAD'},
    {mode: 'cors'}, {mode: 'same-origin'}, {mode: 'no-cors'},
    {url: 'https://other.example' + home},
    {url: 'https://dvsolutionshn.com' + home + 'modules.json'},
    {url: 'https://dvsolutionshn.com' + home + 'service-worker.js'},
    {url: 'https://dvsolutionshn.com' + home.replace('/citas/app/', '/clinica/pacientes/')},
    {url: 'https://dvsolutionshn.com' + home.replace('/citas/app/', '/facturacion/crear/')},
    {url: 'https://dvsolutionshn.com' + home.replace('/citas/app/', '/facturacion/12/pdf/')},
    {url: 'https://dvsolutionshn.com/unrelated/dashboard/citas/app/'},
    {url: 'https://dvsolutionshn.com' + home.slice(0, -1)},
  ];
  for (const request of unhandled) {
    const harness = workerHarness(code, home, () => { throw new Error('Unexpected fetch'); });
    const result = harness.dispatch(request);
    assert.equal(result.responseCount, 0);
    assert.equal(result.answer, undefined);
    assert.equal(harness.calls.length, 0);
    assert.equal(harness.timers.size, 0);
    checks += 1;
  }
  for (const status of [200, 201, 301, 302, 401, 403, 404, 409, 429]) {
    const response = new Response(status === 200 ? 'Synthetic ordinary or login page' : null, {status});
    const harness = workerHarness(code, home, () => Promise.resolve(response));
    const result = harness.dispatch();
    assert.equal(await result.answer, response, 'Normal/denied/redirect response must remain identical');
    assert.equal(harness.calls.length, 1);
    assert.equal(harness.calls[0][0], result.request, 'Original request and credential/redirect policy retained');
    const fetchOptions = harness.calls[0][1] || {};
    assert.deepEqual(Object.keys(fetchOptions).sort(), ['cache', 'signal']);
    assert.equal(fetchOptions.cache, 'no-store');
    assert.equal(harness.timers.size, 0);
    checks += 1;
  }
  for (const status of [204, 205, 500, 502, 503, 504]) {
    const harness = workerHarness(code, home, () => Promise.resolve(new Response(null, {status})));
    await expectRecovery(await harness.dispatch().answer, home);
    assert.equal(harness.calls.length, 1, 'Failures are never retried automatically');
    assert.equal(harness.timers.size, 0);
  }
  for (const failsSynchronously of [false, true]) {
    const harness = workerHarness(code, home, () => {
      if (failsSynchronously) throw new Error('PRIVATE-EXCEPTION');
      return Promise.reject(new Error('PRIVATE-EXCEPTION'));
    });
    await expectRecovery(await harness.dispatch().answer, home);
    assert.equal(harness.calls.length, 1);
  }
  for (const noAbort of [false, true]) {
    let resolveLate;
    const late = new Promise(resolve => { resolveLate = resolve; });
    const harness = workerHarness(code, home, () => late, {noAbort});
    const result = harness.dispatch();
    await harness.deadline();
    const recovered = await result.answer;
    await expectRecovery(recovered.clone(), home);
    assert.equal(harness.calls.length, 1);
    assert.equal(harness.calls[0][1].cache, 'no-store');
    if (noAbort) assert.deepEqual(Object.keys(harness.calls[0][1]), ['cache']);
    else assert.equal(harness.calls[0][1].signal.aborted, true);
    resolveLate(new Response('Late successful page', {status: 200}));
    await Promise.resolve();
    await Promise.resolve();
    assert.equal(await result.answer, recovered, 'A late response cannot overwrite recovery or trigger navigation');
    assert.equal(harness.calls.length, 1);
  }
}

(async () => {
  const input = JSON.parse(fs.readFileSync(0, 'utf8'));
  assert.equal(input.workers.length, 4);
  for (const worker of input.workers) await testWorker(worker);
  process.stdout.write(JSON.stringify({workers: input.workers.length, checks, realFetches: 0, cacheWrites: 0, posts: 0}) + '\n');
})().catch(error => { process.stderr.write(error.stack + '\n'); process.exitCode = 1; });
