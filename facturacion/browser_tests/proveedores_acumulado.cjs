const {chromium}=require(process.env.PLAYWRIGHT_MODULE);
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_EXECUTABLE});
 try {
  const context=await browser.newContext({viewport:{width:1600,height:1050}});
  await context.addCookies([{name:'sessionid',value:process.env.P_SESSION,domain:new URL(process.env.P_BOOK).hostname,path:'/'}]);
  const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(process.env.P_BOOK);
  await page.getByRole('link',{name:'Crear / Vincular proveedores desde este libro',exact:true}).click();
  assert.equal(await page.locator('[name=grupos]').count(),1);
  await page.getByRole('button',{name:'Confirmar vinculación',exact:true}).click();
  await page.waitForURL(process.env.P_BOOK);
  await page.goto(process.env.P_CATALOG);
  await page.locator('tbody tr').filter({hasText:'Café Prueba'}).getByRole('link',{name:'Editar',exact:true}).click();
  await page.locator('[name=cuenta_habitual]').selectOption(process.env.P_ACCOUNT);
  await page.getByRole('button',{name:'Guardar proveedor',exact:true}).click();
  await page.goto(process.env.P_ACC);
  await page.locator('[name=proveedor_id]').selectOption({label:'Café Prueba'});
  await page.getByRole('button',{name:'Filtrar facturas',exact:true}).click();
  await page.locator('[name=alcance]').selectOption('filtradas');
  await page.getByRole('button',{name:'Aplicar cuenta sugerida a todas',exact:true}).click();
  assert.equal(await page.locator('#mass-count').textContent(),'2');
  assert.equal(await page.locator('#mass-total').textContent(),'1,380.00');
  await page.locator('[data-mass-id="'+process.env.P_EXCLUDE+'"]').uncheck();
  assert.equal(await page.locator('#mass-total').textContent(),'1,150.00');
  if(process.env.P_SCREENSHOT)await page.screenshot({path:process.env.P_SCREENSHOT,fullPage:true});
  await page.getByRole('button',{name:'Confirmar asignación',exact:true}).click();
  assert.match(await page.locator('.acc-info').textContent(),/1 por clasificar/);
  await page.goto(process.env.P_BOOK);
  await page.locator('#supplier').fill('cafe prueba');
  await page.locator('#supplier-options [role=option]').first().waitFor();
  await page.locator('#supplier').press('Tab');
  assert.match(await page.locator('#supplier-suggestion').textContent(),/Cuenta sugerida/);
  assert.deepEqual(errors,[]);
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
