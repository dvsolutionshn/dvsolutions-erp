// Ejecutar mediante CapturaRapidaBrowserTests sobre una BD aislada de Django.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE);
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({headless:true, executablePath:process.env.CHROME_EXECUTABLE});
  try {
    const context = await browser.newContext({viewport:{width:1440,height:1000}});
    const base = process.env.CAPTURA_TEST_URL;
    await context.addCookies([{name:'sessionid',value:process.env.CAPTURA_TEST_SESSION,url:base}]);
    const page = await context.newPage();
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    await page.goto(base + '/demo_1/dashboard/facturacion/libro-compras/captura-rapida/');
    const date = page.locator('[name=fecha_documento]');
    await date.waitFor();
    await date.fill('5/8/26'); await page.keyboard.press('Tab');
    assert.equal(await date.inputValue(), '05/08/2026');
    await page.keyboard.type('lar');
    await page.locator('[role=option]').first().waitFor();
    await page.keyboard.press('Tab');
    assert.equal(await page.locator('#supplier').inputValue(), 'Larach');
    await page.keyboard.type('0040120158956348'); await page.keyboard.press('Tab');
    assert.equal(await page.locator('[name=numero_factura]').inputValue(),'004-012-01-58956348');
    await page.keyboard.type('10'); await page.keyboard.press('Tab');
    await page.keyboard.type('100.10'); await page.keyboard.press('Tab');
    await page.keyboard.type('200.25');
    assert.equal(await page.locator('#total').textContent(),'361.41');
    await page.keyboard.press('Shift+Tab');
    assert.equal(await page.locator(':focus').getAttribute('name'),'base_15');
    await page.keyboard.press('Tab'); await page.keyboard.press('Enter');
    await page.waitForFunction(() => document.querySelector('#capture-status').textContent.includes('1 guardada'));
    assert.equal(await page.locator(':focus').getAttribute('name'),'fecha_documento');
    assert.equal(await date.inputValue(),'');
    // Nueva factura, otro mes, sin navegación ni recarga.
    await page.keyboard.type('180726'); await page.keyboard.press('Tab');
    await page.keyboard.type('lar'); await page.locator('[role=option]').first().waitFor();
    await page.keyboard.press('Enter');
    await page.keyboard.type('000001010000000085'); await page.keyboard.press('Tab');
    await page.waitForFunction(() => document.querySelector('#row-message').textContent.includes('FACTURA YA REGISTRADA'));
    assert.match(await page.locator('#row-message').textContent(), /02\/01\/2024/);
    await page.keyboard.press('Escape');
    assert.equal(await page.locator(':focus').getAttribute('name'),'fecha_documento');
    assert.equal(await date.inputValue(),'');
    // Fecha inválida: conserva fila y muestra error; reintento corregido.
    await page.keyboard.type('310226'); await page.keyboard.press('Tab');
    await page.keyboard.type('lar'); await page.locator('[role=option]').first().waitFor();
    await page.keyboard.press('Tab'); await page.keyboard.type('000001011');
    await page.keyboard.press('Tab'); await page.keyboard.type('0.10');
    await page.keyboard.press('Tab'); await page.keyboard.press('Tab'); await page.keyboard.press('Enter');
    await page.waitForFunction(() => document.querySelector('#row-message').textContent.includes('Fecha inválida'));
    assert.equal(await page.locator('[name=exento]').inputValue(),'0.10');
    await date.fill('180726'); await page.locator('[name=base_18]').focus(); await page.keyboard.press('Enter');
    await page.waitForFunction(() => document.querySelector('#capture-status').textContent.includes('2 guardada'));
    assert.equal(await page.locator(':focus').getAttribute('name'),'fecha_documento');
    assert.deepEqual(errors,[]);
    // Dos envíos concurrentes: una sola factura; SQLite puede solicitar reintento.
    const concurrent = await page.evaluate(async proveedor => {
      const form = document.querySelector('#capture-form');
      const payload = {csrfmiddlewaretoken:form.elements.csrfmiddlewaretoken.value,fecha_documento:'070926',
        proveedor,numero_factura:'0000010199',exento:'5'};
      const send = async () => {
        const response = await fetch(form.action,{method:'POST',body:new URLSearchParams(payload)});
        return {status:response.status,body:await response.text()};
      };
      const results = await Promise.all([send(),send()]);
      return [...results,await send()];
    }, String(process.env.CAPTURA_TEST_PROVEEDOR));
    for (const result of concurrent) assert.ok([201,409,503].includes(result.status),result.body);
    assert.ok([201,409].includes(concurrent[2].status),concurrent[2].body);
    // Alta desde la búsqueda: solo RTN, errores conservan fila, cancelar y reintentar.
    await date.fill('070926'); await page.keyboard.press('Tab');
    await page.keyboard.type('Papelería nueva');
    await page.getByRole('option').filter({hasText:'Crear'}).waitFor();
    await page.keyboard.press('Tab');
    await page.locator('#supplier-dialog').waitFor({state:'visible'});
    assert.equal(await page.locator(':focus').getAttribute('id'),'supplier-rtn');
    await page.keyboard.press('Escape');
    assert.equal(await date.inputValue(),'07/09/2026');
    await page.locator('#supplier').focus();
    await page.getByRole('option').filter({hasText:'Crear'}).waitFor();
    await page.keyboard.press('Enter');
    await page.locator('#supplier-rtn').fill('abc'); await page.keyboard.press('Enter');
    await page.waitForFunction(() => document.querySelector('#supplier-error').textContent.includes('RTN'));
    await page.locator('#supplier-rtn').fill('08012020123456'); await page.keyboard.press('Enter');
    await page.waitForFunction(() => !document.querySelector('#supplier-dialog').open);
    assert.equal(await page.locator(':focus').getAttribute('name'),'numero_factura');
    assert.equal(await page.locator('#supplier').inputValue(),'Papelería nueva');
    await page.keyboard.type('000001011234'); await page.keyboard.press('Tab');
    await page.keyboard.type('50'); await page.keyboard.press('Tab'); await page.keyboard.press('Tab');
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => document.querySelector('#capture-status').textContent.includes('3 guardada'));
    assert.equal(await page.locator(':focus').getAttribute('name'),'fecha_documento');
    assert.deepEqual(errors,[]);
    if (process.env.CAPTURA_SCREENSHOT) await page.screenshot({path:process.env.CAPTURA_SCREENSHOT,fullPage:true});
    console.log('PASS: teclado, SHIFT+TAB, fechas, proveedor TAB/ENTER, cálculos, guardado, foco, duplicado ESC, error y reintento.');
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exit(1);});
