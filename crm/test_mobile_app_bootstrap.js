/* Evaluate the actual rendered startup script in a closed synthetic DOM. */
'use strict';
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

let checks = 0;
function createHarness(page, options = {}) {
  const timers = new Map(), store = new Map(options.values || []), writes = [];
  let timer = 0;
  function element(id) {
    return {id, hidden: id === 'mobileAppRecovery', textContent: '', listeners: new Map(),
      addEventListener(type, handler) { assert(!this.listeners.has(type)); this.listeners.set(type, handler); },
      scrollIntoView() {},
    };
  }
  const elements = new Map(['mobileAppRecovery', 'mobileAppRecoveryMessage', 'mobileAppRecoveryHome', 'mobileAppRecoveryDismiss'].map(id => [id, element(id)]));
  elements.get('mobileAppRecoveryHome').href = page.home;
  const drafts = {invoice: [{id: 'synthetic-product', quantity: 2}], note: 'Synthetic unsaved clinical form', date: '2026-11-25'};
  const originalDrafts = JSON.stringify(drafts);
  const visibleUi = {homeVisible: true, heroVisible: true, navVisible: true};
  const storage = {
    getItem(key) {
      if (options.storage === 'read-error') throw new Error('Storage unavailable');
      return store.has(key) ? store.get(key) : null;
    },
    setItem(key, value) {
      if (options.storage === 'write-error') throw new Error('Quota exceeded');
      writes.push([key, String(value)]); store.set(key, String(value));
    },
    clear() { throw new Error('Storage clearing is forbidden'); },
    removeItem() { throw new Error('Deleting preferences is forbidden'); },
  };
  const window = {
    setTimeout(callback, delay) { const id = ++timer; timers.set(id, {callback, delay}); return id; },
    clearTimeout(id) { timers.delete(id); },
    confirm: () => options.confirm !== false,
    __drafts: drafts,
  };
  const location = {};
  Object.defineProperty(location, 'href', {get: () => page.home, set() { throw new Error('Automatic navigation is forbidden'); }});
  location.reload = () => { throw new Error('Automatic reload is forbidden'); };
  window.location = location;
  Object.defineProperty(window, 'localStorage', {get() {
    if (options.storage === 'getter-error') throw new Error('SecurityError');
    return storage;
  }});
  Object.defineProperty(window, 'sessionStorage', {get() { throw new Error('Session storage access forbidden'); }});
  Object.defineProperty(window, 'fetch', {get() { throw new Error('No transports in bootstrap'); }});
  Object.defineProperty(window, 'caches', {get() { throw new Error('Cache mutation forbidden'); }});
  if (options.namedCollision) window.mobileAppRecovery = elements.get('mobileAppRecovery');
  const context = vm.createContext({window,
    document: {getElementById(id) { return elements.get(id) || null; }},
    console: {log() { throw new Error('No diagnostic data logging'); }},
  });
  vm.runInContext(page.bootstrap, context, {timeout: 1000});
  const api = window.mobileAppRecovery;
  assert.equal(typeof api.markNavigationReady, 'function');
  assert.equal(typeof api.markFeaturesReady, 'function');
  function intact() {
    assert.equal(JSON.stringify(drafts), originalDrafts);
    assert.deepEqual(visibleUi, {homeVisible: true, heroVisible: true, navVisible: true});
    assert.equal(elements.get('mobileAppRecoveryHome').href, page.home);
  }
  function click(id) {
    let prevented = false;
    elements.get(id).listeners.get('click')({preventDefault() { prevented = true; }});
    return prevented;
  }
  return {context, window, api, elements, timers, store, writes, intact, click};
}

function actualMetadataCode(page) {
  const recentStart = page.main.indexOf('const quickRecentIds=');
  const recentEnd = page.main.indexOf('const setQuickStatus=', recentStart);
  const favoriteStart = page.main.indexOf('const patientFavoriteKey=');
  const favoriteEnd = page.main.indexOf('const fallbackCopyPatientInvite=', favoriteStart);
  const persistStart = page.main.indexOf('const persistPatientFavorites=');
  const persistEnd = page.main.indexOf('const patientAppointmentMarkup=', persistStart);
  assert(recentStart >= 0 && recentEnd > recentStart && favoriteStart >= 0 && favoriteEnd > favoriteStart && persistStart >= 0 && persistEnd > persistStart,
    'Use exact production preference functions, not rewritten test copies');
  return 'var appCompanySlug=' + JSON.stringify(page.slug) + ';var appRecovery=window.mobileAppRecovery;\n' +
    page.main.slice(recentStart, recentEnd) + page.main.slice(favoriteStart, favoriteEnd) + page.main.slice(persistStart, persistEnd) +
    ';this.metadata={recent:quickRecentIds,remember:rememberQuickProduct,favorites:patientFavorites,persist:persistPatientFavorites};';
}

function testPage(page) {
  const h = createHarness(page, {namedCollision: true});
  assert.notEqual(h.api, h.elements.get('mobileAppRecovery'), 'DOM named id cannot suppress the real bootstrap');
  assert.equal(h.elements.get('mobileAppRecovery').hidden, true);
  assert.equal(h.timers.size, 1);
  assert.equal([...h.timers.values()][0].delay, 10000);
  [...h.timers.values()][0].callback();
  assert.equal(h.elements.get('mobileAppRecovery').hidden, false);
  assert.equal(h.api.check(), false);
  h.api.markNavigationReady();
  assert.equal(h.api.check(), false);
  h.api.markFeaturesReady();
  assert.equal(h.api.check(), true);
  assert.equal(h.elements.get('mobileAppRecovery').hidden, true);
  assert.equal(h.timers.size, 0);
  h.intact(); checks += 1;

  const duplicateApi = h.api;
  vm.runInContext(page.bootstrap, h.context, {timeout: 1000});
  assert.equal(h.window.mobileAppRecovery, duplicateApi);
  assert.equal(h.timers.size, 0);
  h.intact(); checks += 1;

  const dismissed = createHarness(page);
  dismissed.api.check();
  dismissed.click('mobileAppRecoveryDismiss');
  assert.equal(dismissed.elements.get('mobileAppRecovery').hidden, true);
  dismissed.api.check();
  assert.equal(dismissed.elements.get('mobileAppRecovery').hidden, true);
  dismissed.api.showHelp('<b>Literal compatibility warning</b>');
  assert.equal(dismissed.elements.get('mobileAppRecoveryMessage').textContent, '<b>Literal compatibility warning</b>');
  dismissed.api.markNavigationReady(); dismissed.api.markFeaturesReady();
  assert.equal(dismissed.elements.get('mobileAppRecovery').hidden, false, 'Manual compatibility warning is not hidden by startup readiness');
  dismissed.intact(); checks += 1;

  for (const confirmation of ['cancel', 'accept', 'missing', 'throws']) {
    const manual = createHarness(page, {confirm: confirmation !== 'cancel'});
    if (confirmation === 'missing') manual.window.confirm = undefined;
    if (confirmation === 'throws') manual.window.confirm = () => { throw new Error('Dialogs unavailable'); };
    assert.equal(manual.click('mobileAppRecoveryHome'), confirmation !== 'accept');
    manual.intact(); checks += 1;
  }

  for (const failure of ['getter-error', 'read-error', 'write-error']) {
    const blocked = createHarness(page, {storage: failure, values: [['unrelated', 'KEEP']]});
    assert.equal(blocked.api.readPreference('missing', 'fallback'), 'fallback');
    assert.equal(blocked.api.writePreference('clinic_favorites', '["synthetic-id"]'), failure === 'read-error');
    assert.equal(blocked.api.readPreference('clinic_favorites', 'fallback'), '["synthetic-id"]');
    assert.equal(blocked.store.get('unrelated'), 'KEEP');
    blocked.api.markNavigationReady(); blocked.api.markFeaturesReady();
    assert.equal(blocked.api.check(), true);
    blocked.intact(); checks += 1;
  }

  for (const malformed of ['{bad-json', '{}', 'null', '42', '"text"']) {
    const malformedStore = createHarness(page, {storage: 'write-error', values: [
      [page.slug + '_quick_products', malformed], [page.slug + '_patient_favorites', malformed], ['unrelated', 'KEEP'],
    ]});
    vm.runInContext(actualMetadataCode(page), malformedStore.context, {timeout: 1000});
    const metadata = malformedStore.context.metadata;
    assert.deepEqual(Array.from(metadata.recent()), []);
    assert.equal(metadata.favorites.size, 0);
    metadata.remember('new-synthetic-id');
    assert.deepEqual(Array.from(metadata.recent()), ['new-synthetic-id']);
    metadata.favorites.add('favorite-synthetic-id'); metadata.persist();
    assert.equal(malformedStore.api.readPreference(page.slug + '_patient_favorites', ''), '["favorite-synthetic-id"]');
    assert.equal(malformedStore.store.get('unrelated'), 'KEEP');
    assert.equal(malformedStore.store.get(page.slug + '_quick_products'), malformed, 'Blocked storage is not deleted or overwritten');
    malformedStore.intact(); checks += 1;
  }

  const scroll = createHarness(page);
  scroll.api.scrollToElement({});
  let called = 0;
  scroll.api.scrollToElement({scrollIntoView(options) { called += 1; if (options) throw new Error('Options unsupported'); }});
  assert.equal(called, 2);
  scroll.window.requestAnimationFrame = () => { throw new Error('rAF unavailable'); };
  scroll.api.scrollToElement({scrollIntoView() { throw new Error('Scrolling unavailable'); }});
  scroll.api.showHelp('Synthetic help remains usable');
  scroll.intact(); checks += 1;
}

try {
  const input = JSON.parse(fs.readFileSync(0, 'utf8'));
  assert.equal(input.pages.length, 4);
  for (const page of input.pages) testPage(page);
  process.stdout.write(JSON.stringify({pages: input.pages.length, checks, realTransports: 0, draftMutations: 0, automaticNavigations: 0}) + '\n');
} catch (error) { process.stderr.write(error.stack + '\n'); process.exitCode = 1; }
