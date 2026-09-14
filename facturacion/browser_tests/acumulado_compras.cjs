const {chromium}=require(process.env.PLAYWRIGHT_MODULE);
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_EXECUTABLE});
 try {
  const context=await browser.newContext({viewport:{width:1600,height:1050}});
  const url=process.env.ACC_URL;
  await context.addCookies([{name:'sessionid',value:process.env.ACC_SESSION,domain:new URL(url).hostname,path:'/'}]);
  const page=await context.newPage(); const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(url);
  assert.match(await page.locator('.acc-info').textContent(),/2 por clasificar/);
  assert.match(await page.locator('#acc-matrix').textContent(),/1,380.00/);
  await page.locator('[name=mes]').selectOption('8');
  await page.getByRole('button',{name:'Filtrar facturas',exact:true}).click();
  assert.equal(await page.locator('#acc-detail [name=registros]').count(),1);
  await page.getByRole('checkbox',{name:'Seleccionar las facturas de esta página',exact:true}).check();
  await page.locator('[name=cuenta_destino]').selectOption(process.env.ACC_ACCOUNT);
  await page.getByRole('button',{name:'Aplicar a seleccionadas',exact:true}).click();
  assert.match(await page.locator('.acc-info').textContent(),/1 por clasificar/);
  const account=page.locator('#acc-matrix tr.cuenta').filter({hasText:'Compras en PriceSmart'});
  assert.equal(await account.locator('td').nth(7).textContent(),'1,000.00');
  await account.locator('a').nth(7).click();
  assert.equal(await page.locator('#acc-detail [name=registros]').count(),1);
  assert.match(await page.locator('#acc-detail').textContent(),/25\/12\/2025/);
  const popupPromise=page.waitForEvent('popup');
  await page.getByRole('link',{name:'Ver factura',exact:true}).click();
  const popup=await popupPromise;await popup.waitForLoadState();
  try {await popup.locator('#book-row-'+process.env.ACC_RECORD).waitFor({timeout:10000});}
  catch(error){console.error('Popup:',popup.url(),(await popup.locator('body').textContent()).slice(-3500));throw error;}
  await popup.close();
  if(process.env.ACC_SCREENSHOT){await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:process.env.ACC_SCREENSHOT,fullPage:true});}
  await page.getByRole('link',{name:'Administrar cuentas',exact:true}).click();
  assert.equal(await page.locator('[name=nombre]').count(),1);
  assert.deepEqual(errors,[]);
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
