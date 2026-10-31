/* Offline execution of the actual template's signup script. No browser/network. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('crm/templates/crm/form.html', 'utf8');
const source = html.match(/<script>([\s\S]*?)<\/script>/)[1];

function setup(mode = 'coexistence') {
    const callbacks = {};
    const button = {disabled: false, addEventListener: (_, fn) => callbacks.click = fn};
    const result = {};
    const calls = [];
    const window = {addEventListener: (_, fn) => callbacks.message = fn, setTimeout() {}, location: {reload() {}}};
    const context = {
        window,
        document: {getElementById: id => ({'meta-whatsapp-connect': button, 'meta-whatsapp-result': result,
            'meta-whatsapp-mode': {value: mode}})[id], querySelector: () => ({value: 'synthetic-csrf'}),
            createElement: () => ({}), body: {appendChild() {}}},
        FB: {init() {}, login: (callback, options) => {callbacks.login = callback; callbacks.options = options;}},
        fetch: async (_, options) => {calls.push(JSON.parse(options.body)); return {ok: true, json: async () => ({ok: true, message: 'Paused'})};},
    };
    vm.runInNewContext(source, context);
    window.fbAsyncInit();
    callbacks.click();
    return {callbacks, calls, button};
}

const event = (type = 'FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING', origin = 'https://www.facebook.com') => ({
    origin, data: JSON.stringify({type: 'WA_EMBEDDED_SIGNUP', event: type, data: {waba_id: 'account-qa'}}),
});

(async () => {
    for (const codeFirst of [true, false]) {
        const {callbacks, calls} = setup();
        const code = () => callbacks.login({authResponse: {code: 'code-qa'}});
        const session = () => callbacks.message(event());
        if (codeFirst) {code(); session();} else {session(); code();}
        session(); // duplicate postMessage must not exchange the single-use code twice
        await new Promise(resolve => setImmediate(resolve));
        assert.equal(calls.length, 1);
        assert.equal(calls[0].waba_id, 'account-qa');
        assert.equal(calls[0].phone_number_id, undefined); // server discovers only an unambiguous number
    }
    const badOrigin = setup();
    badOrigin.callbacks.login({authResponse: {code: 'code-qa'}});
    badOrigin.callbacks.message(event('FINISH', 'https://attacker.invalid'));
    assert.equal(badOrigin.calls.length, 0);
    const cancelled = setup();
    cancelled.callbacks.message(event('CANCEL'));
    cancelled.callbacks.login({authResponse: {code: 'late-code'}});
    cancelled.callbacks.message(event());
    assert.equal(cancelled.calls.length, 0);
    assert.equal(cancelled.button.disabled, false);
    const retried = setup();
    const previousLogin = retried.callbacks.login;
    retried.callbacks.message(event('CANCEL'));
    retried.callbacks.click();
    retried.callbacks.message(event());
    previousLogin({authResponse: {code: 'old-attempt-code'}});
    assert.equal(retried.calls.length, 0);
    retried.callbacks.login({authResponse: {code: 'current-attempt-code'}});
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(retried.calls[0].code, 'current-attempt-code');
    assert.equal(setup().callbacks.options.extras.featureType, 'whatsapp_business_app_onboarding');
    assert.equal(Object.keys(setup('cloud').callbacks.options.extras).length, 0);
    process.stdout.write('Signup: syntax, callback races, duplicate events, origin, cancellation and mode selection OK\n');
})().catch(error => {process.stderr.write(String(error.stack) + '\n'); process.exitCode = 1;});
