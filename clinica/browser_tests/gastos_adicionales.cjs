// Run against a rendered form at GA_TEST_URL. Catalog requests and POSTs are
// mocked; no messages are sent and no production records are changed.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.CHROME_EXECUTABLE ? { executablePath: process.env.CHROME_EXECUTABLE } : { channel: 'chrome' }) });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    const requests = [];
    page.on('pageerror', error => errors.push(error.message));
    const product = (id, nombre, precio, impuesto, origen = 'Medical Spa') => ({ id, nombre, precio, codigo: 'GA-' + id, empresa_nombre: origen, impuesto_id: 1, impuesto_porcentaje: impuesto, impuesto_activo: true });
    const products = [product(9001, 'Botox <b>original</b>', '115.00', '15.00'), product(9002, 'Servicio exento', '100.00', '0.00', 'Hospital Mia')];
    const patients = [{ id: 9101, nombre: 'María López', identidad: 'QA-001', telefono: '50499995678' }, { id: 9102, nombre: 'Mario Hernández', identidad: 'QA-002', telefono: '50499990000' }];
    await page.route('**/gastos-adicionales/productos/buscar/**', async route => {
      const url = new URL(route.request().url());
      const q = url.searchParams.get('q');
      const number = Number(url.searchParams.get('page') || 1);
      requests.push({ kind: 'product', q, page: number });
      const result = q ? products.filter(p => (p.nombre + p.codigo).toLowerCase().includes(q.toLowerCase())) : products;
      await route.fulfill({ json: number === 2 ? { results: [product(9003, 'Producto de segunda página', '0.05', '0.00')], total: 3, page: 2, has_more: false } : { results: result, total: q ? result.length : 3, page: 1, has_more: !q, next_page: !q ? 2 : null } });
    });
    await page.route('**/gastos-adicionales/pacientes/buscar/**', async route => {
      const q = new URL(route.request().url()).searchParams.get('q');
      requests.push({ kind: 'patient', q });
      const result = patients.filter(p => (p.nombre + p.identidad + p.telefono).toLowerCase().includes((q || '').toLowerCase()));
      await route.fulfill({ json: { results: result, total: result.length, has_more: false, page: 1 } });
    });
    await page.goto(process.env.GA_TEST_URL);
    await page.locator('#ga-patient-search').click();
    await page.locator('#ga-patient-results [role=option]').first().waitFor();
    assert.equal(await page.locator('#ga-patient-search').inputValue(), '');
    assert.equal(await page.locator('#ga-patient-results [role=option]').count(), 2);
    await page.locator('#ga-patient-search').fill('M');
    await page.waitForFunction(() => document.querySelectorAll('#ga-patient-results [role=option]').length === 2);
    await page.locator('#ga-patient-search').press('ArrowDown');
    await page.locator('#ga-patient-search').press('Enter');
    assert.equal(await page.locator('[name=paciente]').inputValue(), '9101');
    await page.locator('#ga-patient-search').fill('99995678');
    assert.equal(await page.locator('[name=paciente]').inputValue(), '');
    await page.locator('#ga-patient-results [role=option]').first().waitFor();
    assert.equal(await page.locator('#ga-patient-results [role=option]').count(), 1);
    await page.locator('#ga-patient-search').press('Enter');

    await page.locator('#ga-product-search').click();
    await page.locator('#ga-product-results .ga-search-more').waitFor();
    assert.equal(await page.locator('#ga-product-results [role=option]').count(), 2);
    await page.locator('#ga-product-results .ga-search-more').click();
    await page.waitForFunction(() => document.querySelectorAll('#ga-product-results [role=option]').length === 3);
    await page.locator('#ga-product-results [role=option]').last().click();
    await page.locator('#ga-lines [data-value=cantidad]').last().fill('0.50');
    assert.equal(await page.locator('#ga-lines [data-subtotal]').last().innerText(), 'L 0.02');
    await page.locator('#ga-lines .ga-line-remove').last().click();

    await page.locator('#ga-product-search').fill('b');
    await page.locator('#ga-product-results [role=option]').first().waitFor();
    assert.equal(await page.locator('#ga-product-results [role=option]').count(), 1);
    await page.locator('#ga-product-search').press('ArrowDown');
    await page.locator('#ga-product-search').press('Enter');
    assert.equal(await page.locator('#ga-lines td strong').last().innerText(), 'Botox <b>original</b>');
    assert.equal(await page.locator('#ga-lines td b').count(), 0);
    await page.locator('#ga-lines [data-value=cantidad]').last().fill('2');
    assert.equal(await page.locator('#ga-subtotal').innerText(), 'L 200.00');
    assert.equal(await page.locator('#ga-taxes').innerText(), 'L 30.00');
    assert.equal(await page.locator('#ga-total').innerText(), 'L 230.00');
    await page.locator('#ga-lines [data-value=precio_unitario]').last().fill('57.50');
    assert.equal(await page.locator('#ga-total').innerText(), 'L 115.00');
    assert.equal(await page.locator('#ga-subtotal').innerText(), 'L 100.00');
    assert.equal(await page.locator('#ga-taxes').innerText(), 'L 15.00');

    await page.locator('#ga-product-search').fill('Servicio');
    await page.locator('#ga-product-results [role=option]').first().waitFor();
    await page.locator('#ga-product-search').press('Enter');
    assert.equal(await page.locator('#ga-total').innerText(), 'L 215.00');
    assert.equal(await page.locator('#ga-subtotal').innerText(), 'L 200.00');
    assert.equal(await page.locator('#ga-taxes').innerText(), 'L 15.00');
    const lines = JSON.parse(await page.locator('[name=lineas]').inputValue());
    assert.deepEqual(lines, [{ producto_id: '9001', cantidad: '2.00', precio_unitario: '57.50' }, { producto_id: '9002', cantidad: '1.00', precio_unitario: '100.00' }]);
    assert(requests.some(r => r.kind === 'patient' && r.q === ''));
    assert(requests.some(r => r.kind === 'patient' && r.q === 'M'));
    assert(requests.some(r => r.kind === 'product' && r.q === 'b'));
    assert(requests.some(r => r.kind === 'product' && r.page === 2));

    for (const size of [{ width: 390, height: 844 }, { width: 320, height: 740 }, { width: 768, height: 1024 }]) {
      await page.setViewportSize(size);
      await page.waitForFunction(() => {
        const nav = document.querySelector('.erp-mobile-bottom-nav').getBoundingClientRect();
        const summary = document.querySelector('.ga-compose-summary').getBoundingClientRect();
        return nav.height === 0 || summary.bottom <= nav.top;
      });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await page.locator('#ga-product-search').scrollIntoViewIfNeeded();
      await page.locator('#ga-product-search').fill('bot');
      await page.locator('#ga-product-results [role=option]').first().waitFor();
      const option = page.locator('#ga-product-results [role=option]').first();
      assert.equal(await option.isVisible(), true);
      await option.click();
      await page.locator('#ga-lines .ga-line-remove').last().click();
    }
    assert.deepEqual(errors, []);
    console.log('GA: listas sin texto, filtro desde una letra, teléfono, teclado, paginación, importes e impuestos, origen, XSS, filas y móvil OK.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
