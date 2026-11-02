/* Presentation only. Purchases use the original server form, formset and saver. */
(() => {
  'use strict';
  const form=document.getElementById('mobilePurchaseForm');if(!form)return;
  const inlineSupported=typeof window.fetch==='function'&&typeof window.HTMLDialogElement!=='undefined'&&typeof HTMLDialogElement.prototype.showModal==='function';
  const list=document.getElementById('purchaseLineList'),template=document.getElementById('purchaseEmptyLine'),total=form.querySelector('[name="lineas_compra-TOTAL_FORMS"]'),maximum=form.querySelector('[name="lineas_compra-MAX_NUM_FORMS"]'),add=document.getElementById('addPurchaseLine'),status=document.getElementById('purchaseLineStatus');
  const payload=id=>{try{return JSON.parse(document.getElementById(id)?.textContent||'{}');}catch(_error){return {};}};
  const taxes=new Map((payload('mobilePurchaseTaxes')||[]).map(item=>[String(item.id),Number(item.porcentaje)||0])),products=new Map((payload('mobilePurchaseProducts')||[]).map(item=>[String(item.id),item])),suppliers=payload('mobilePurchaseSuppliers');
  const field=(card,name)=>card.querySelector(`[name$="-${name}"]`),named=name=>form.elements.namedItem(name),number=input=>Number(String(input?.value||'0').replace(',','.'))||0,normalize=value=>String(value||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().trim(),money=value=>'L. '+Number(value).toLocaleString('es-HN',{minimumFractionDigits:2,maximumFractionDigits:2});
  const active=()=>[...list.querySelectorAll('[data-purchase-line]')].filter(card=>!field(card,'DELETE')?.checked);
  function estimates(){let sum=0;active().forEach((card,index)=>{card.querySelector('[data-purchase-line-title]').textContent=`Producto ${index+1}`;const raw=number(field(card,'cantidad'))*number(field(card,'costo_unitario')),net=Math.max(0,raw*(1-number(field(card,'descuento_porcentaje'))/100)),taxId=field(card,'impuesto')?.value||products.get(String(field(card,'producto')?.value))?.impuesto_id,amount=net*(1+(taxes.get(String(taxId))||0)/100);sum+=amount;card.querySelector('[data-purchase-line-total]').textContent=money(amount);});document.getElementById('purchaseLineCount').textContent=`${active().length} líneas`;document.getElementById('purchasePreviewTotal').textContent=money(sum);}
  function filter(card){const select=field(card,'producto'),input=card.querySelector('[data-purchase-product-search]'),options=card._purchaseOptions,selected=select.value,query=normalize(input.value);const matched=options.filter(option=>!option.value||option.value===selected||normalize(option.text).includes(query));select.replaceChildren(...matched.map(item=>{const option=new Option(item.text,item.value,false,item.value===selected);option.disabled=item.disabled;return option;}));const count=options.filter(option=>option.value&&(!query||normalize(option.text).includes(query))).length;card.querySelector('[data-purchase-search-status]').textContent=query?`${count} resultado${count===1?'':'s'}. La selección actual se conserva.`:'Seleccione un producto del inventario de esta empresa.';}
  function initialize(card){const select=field(card,'producto');card._purchaseOptions=[...select.options].map(option=>({value:option.value,text:option.textContent,disabled:option.disabled}));card.querySelector('[data-purchase-product-search]').addEventListener('input',()=>filter(card));select.addEventListener('change',()=>{const tax=field(card,'impuesto'),product=products.get(String(select.value));if(tax&&!tax.value&&product?.impuesto_id){tax.value=String(product.impuesto_id);}estimates();});card.querySelectorAll('[data-purchase-creator-requires-js]').forEach(button=>{button.disabled=!inlineSupported;});if(field(card,'DELETE')?.checked)remove(card,false);}
  function remove(card,focus=true){const deleted=field(card,'DELETE');if(!deleted)return;deleted.checked=true;card.hidden=true;card.querySelectorAll('input,select,textarea').forEach(control=>{if(control!==deleted&&control.type!=='hidden')control.disabled=true;});card.querySelectorAll('[data-operation-field]').forEach(node=>{delete node.dataset.operationField;});estimates();if(focus)add.focus();}
  [...list.querySelectorAll('[data-purchase-line]')].forEach(initialize);
  add.addEventListener('click',()=>{const index=Number(total.value),limit=Number(maximum?.value)||1000;if(!Number.isSafeInteger(index)||index<0||index>=limit){status.textContent='Se alcanzó el máximo de líneas permitido por el formulario.';return;}const fragment=template.content.cloneNode(true),wrapper=document.createElement('div');wrapper.append(fragment);wrapper.innerHTML=wrapper.innerHTML.replace(/__prefix__/g,String(index));const card=wrapper.firstElementChild;if(!card)return;list.append(card);total.value=String(index+1);initialize(card);estimates();status.textContent='Producto agregado. Complete sus datos antes de guardar.';card.querySelector('[data-purchase-product-search]').focus();});
  list.addEventListener('click',event=>{const button=event.target.closest('[data-purchase-remove]');if(button)remove(button.closest('[data-purchase-line]'));});list.addEventListener('input',estimates);list.addEventListener('change',estimates);
  function dueDate(){const date=named('fecha_documento'),due=named('fecha_vencimiento'),days=named('dias_credito');if(!date?.value||!due||!days)return;const parts=/^(\d{4})-(\d{2})-(\d{2})$/.exec(date.value);if(!parts)return;const cash=named('condicion_pago')?.value==='contado';if(cash)days.value='0';const count=cash?0:Number(days.value);if(!Number.isInteger(count)||count<0||count>36500)return;const value=new Date(Date.UTC(Number(parts[1]),Number(parts[2])-1,Number(parts[3])));value.setUTCDate(value.getUTCDate()+count);if(!Number.isNaN(value.getTime()))due.value=value.toISOString().slice(0,10);}
  function stateNote(){const applied=named('estado')?.value==='aplicada',cash=named('condicion_pago')?.value==='contado',note=document.getElementById('purchaseStateNote');note.classList.toggle('is-applied',applied);note.textContent=applied?'Aplicada: al confirmar, se registrará la entrada de inventario y los efectos originales de la compra.'+(cash?' Al ser de contado, también se registrará su pago automático.':''):'Borrador: guarda el documento sin modificar las existencias.';form.dataset.operationCaption=`${form.dataset.purchaseCompany}: ${note.textContent}`;}
  named('proveedor')?.addEventListener('change',()=>{const select=named('proveedor');if(!select.value)return;const name=named('proveedor_nombre');if(name)name.value=select.selectedOptions[0].textContent.trim();const config=suppliers[String(select.value)];if(config){named('condicion_pago').value=config.condicion_pago||'contado';named('dias_credito').value=String(config.dias_credito||0);dueDate();stateNote();}});
  ['fecha_documento','condicion_pago','dias_credito'].forEach(name=>named(name)?.addEventListener('change',()=>{dueDate();stateNote();}));named('estado')?.addEventListener('change',stateNote);
  form.addEventListener('invalid',event=>{let node=event.target.parentElement;while(node){if(node.tagName==='DETAILS')node.open=true;node=node.parentElement;}},{capture:true});

  // The catalog forms are siblings of the purchase, never nested forms. Their
  // marker lets the existing unsaved-work guard also see cancelled catalog
  // drafts. Intercept their submit at document capture BEFORE that shared
  // guard runs, so creating one record never navigates away from the purchase.
  const catalogStatus=document.getElementById('purchaseCatalogStatus');
  const creators=[...document.querySelectorAll('dialog[data-purchase-creator]')].map(sheet=>({sheet,kind:sheet.dataset.purchaseCreator,form:sheet.querySelector('form[data-purchase-creator-form]'),opener:null,target:null,returnFocus:null}));
  let pendingCreator=null;
  function announce(text){catalogStatus.hidden=false;catalogStatus.textContent=text;}
  function creatorStatus(creator,text){creator.sheet.querySelector('[data-purchase-creator-status]').textContent=text;}
  function closeCreator(creator){
    if(pendingCreator===creator){creatorStatus(creator,'Creando el registro. Espere la respuesta antes de cerrar; la compra no se está enviando.');return;}
    creator.sheet.close();
  }
  function openCreator(kind,button,card=null){
    const creator=creators.find(item=>item.kind===kind);if(!creator||!inlineSupported)return;
    if(pendingCreator||form.dataset.submitting==='true'){announce('Espere a que termine el guardado en curso. No se enviará otra operación.');return;}
    if(creators.some(item=>item.sheet.open))return;
    creator.opener=button;creator.target=card;creator.returnFocus=null;
    const name=creator.form.elements.namedItem('nombre');
    if(!name.value.trim())name.value=kind==='product'?(card?.querySelector('[data-purchase-product-search]')?.value||''):(!named('proveedor')?.value?(named('proveedor_nombre')?.value||''):'');
    creatorStatus(creator,'Al volver, los datos de este formulario se conservarán mientras permanezca en esta pantalla.');creator.sheet.showModal();creator.sheet.scrollTop=0;name.focus({preventScroll:true});
  }
  function installSupplier(item){
    const id=String(item.id),select=named('proveedor');let option=[...select.options].find(value=>value.value===id);
    if(!option){option=new Option(item.nombre,id);select.append(option);}else option.textContent=item.nombre;
    suppliers[id]={condicion_pago:item.condicion_pago||'contado',dias_credito:item.dias_credito||0};
    select.value=id;named('proveedor_nombre').value=item.nombre;
    // Do not dispatch change: its normal handler intentionally applies a
    // supplier's defaults. Here the doctor's dates/terms must stay untouched.
    return select;
  }
  function installProduct(item,creator){
    const id=String(item.id),text=item.codigo?`${item.nombre} · ${item.codigo}`:item.nombre,option={value:id,text,disabled:false};
    products.set(id,{id,impuesto_id:item.impuesto_id||''});
    if(item.impuesto_id&&!taxes.has(String(item.impuesto_id)))taxes.set(String(item.impuesto_id),Number(item.impuesto)||0);
    list.querySelectorAll('[data-purchase-line]').forEach(card=>{if(!card._purchaseOptions.some(value=>value.value===id))card._purchaseOptions.push({...option});filter(card);});
    const emptySelect=template.content.querySelector('select[name$="-producto"]');if(emptySelect&&![...emptySelect.options].some(value=>value.value===id))emptySelect.append(new Option(text,id));
    const target=creator.target;
    if(target?.isConnected&&!field(target,'DELETE')?.checked){
      const select=field(target,'producto');if(![...select.options].some(value=>value.value===id))select.append(new Option(text,id));select.value=id;filter(target);
      const tax=field(target,'impuesto');if(tax&&!tax.value&&item.impuesto_id)tax.value=String(item.impuesto_id);
      estimates();return select;
    }
    estimates();return add;
  }
  async function saveCreator(creator){
    if(pendingCreator||form.dataset.submitting==='true')return;
    if(!creator.form.checkValidity()){creator.form.reportValidity();return;}
    const error=creator.sheet.querySelector('[data-purchase-creator-error]');error.hidden=true;error.textContent='';
    const fields=Object.fromEntries(new FormData(creator.form).entries()),csrf=fields.csrfmiddlewaretoken;delete fields.csrfmiddlewaretoken;
    const controls=[...creator.form.querySelectorAll('input,select,textarea,button')].map(control=>[control,control.disabled]),mainButton=form.querySelector('button[type=submit]'),mainDisabled=mainButton.disabled;
    pendingCreator=creator;creator.sheet.setAttribute('aria-busy','true');controls.forEach(([control])=>{control.disabled=true;});mainButton.disabled=true;creatorStatus(creator,'Creando en el catálogo. La compra y sus líneas se mantienen sin guardar.');
    let created=false,succeeded=false;
    try{
      const url=new URL(creator.form.action,window.location.href);if(url.origin!==window.location.origin||!csrf)throw new Error('No se pudo validar la dirección o la sesión de esta operación.');
      const response=await fetch(url.href,{method:'POST',mode:'same-origin',credentials:'same-origin',redirect:'error',cache:'no-store',headers:{'Content-Type':'application/json','Accept':'application/json','X-CSRFToken':csrf},body:JSON.stringify(fields)});
      let data;try{data=await response.json();}catch(_error){throw new Error('La respuesta no permite confirmar el registro. Revise el catálogo antes de repetir la creación; la compra sigue intacta.');}
      if(!response.ok||data?.ok!==true)throw new Error(typeof data?.error==='string'?data.error:'No se pudo crear el registro. Revise los datos y los permisos de la empresa.');
      const item=creator.kind==='supplier'?data.proveedor:data.producto;
      if(!item||!/^[1-9]\d{0,18}$/.test(String(item.id))||typeof item.nombre!=='string')throw new Error('No se pudo confirmar qué registro se creó. Revise el catálogo antes de volver a intentarlo; sus datos se mantienen.');
      created=true;creator.returnFocus=creator.kind==='supplier'?installSupplier(item):installProduct(item,creator);
      creator.form.reset();succeeded=true;
      announce(`${creator.kind==='supplier'?'Proveedor':'Producto'} creado y seleccionado. La compra todavía no se ha guardado y no se han alimentado existencias.`);
    }catch(exception){
      error.hidden=false;error.textContent=created?'El registro fue creado, pero no pudo seleccionarse en esta pantalla. No lo cree de nuevo: revise el catálogo. La compra y sus líneas siguen sin guardar.':(exception instanceof TypeError?'No se pudo confirmar la conexión. Revise el catálogo antes de repetir: el servidor podría haber recibido la solicitud. Sus datos y la compra se conservan.':exception.message||'No se pudo confirmar la creación. Sus datos se mantienen.');
      creatorStatus(creator,'No se realizará ningún reintento automático.');
    }finally{
      pendingCreator=null;creator.sheet.removeAttribute('aria-busy');controls.forEach(([control,disabled])=>{control.disabled=disabled;});mainButton.disabled=mainDisabled;
      if(succeeded){creatorStatus(creator,'');creator.sheet.close();}else error.focus();
    }
  }
  document.addEventListener('submit',event=>{
    const creator=creators.find(item=>item.form===event.target);
    if(creator){event.preventDefault();event.stopImmediatePropagation();if(inlineSupported)saveCreator(creator);return;}
    if(event.target===form&&(pendingCreator||creators.some(item=>item.sheet.open))){event.preventDefault();event.stopImmediatePropagation();announce('Termine o cierre el formulario de catálogo antes de revisar la compra. No se ha enviado la compra.');}
  },{capture:true});
  document.addEventListener('click',event=>{
    if(pendingCreator&&event.target.closest('a[href]')){event.preventDefault();event.stopImmediatePropagation();creatorStatus(pendingCreator,'Espere la respuesta antes de salir. No se ha enviado la compra.');return;}
    const close=event.target.closest('[data-purchase-close-creator]');if(close){event.preventDefault();event.stopImmediatePropagation();const creator=creators.find(item=>item.sheet===close.closest('dialog'));if(creator)closeCreator(creator);return;}
    const supplier=event.target.closest('[data-purchase-open-supplier]'),product=event.target.closest('[data-purchase-open-product]');
    if(supplier||product){event.preventDefault();event.stopImmediatePropagation();openCreator(supplier?'supplier':'product',supplier||product,product?.closest('[data-purchase-line]'));}
  },{capture:true});
  creators.forEach(creator=>{
    creator.sheet.addEventListener('cancel',event=>{event.preventDefault();closeCreator(creator);});
    creator.sheet.addEventListener('click',event=>{if(event.target!==creator.sheet)return;const box=creator.sheet.getBoundingClientRect();if(event.clientX<box.left||event.clientX>box.right||event.clientY<box.top||event.clientY>box.bottom){event.preventDefault();event.stopImmediatePropagation();closeCreator(creator);}},{capture:true});
    creator.sheet.addEventListener('close',()=>{const target=creator.returnFocus||creator.opener;creator.target=null;creator.returnFocus=null;if(target?.isConnected)target.focus();});
    creator.form.querySelectorAll('[data-purchase-save-creator]').forEach(button=>{button.disabled=!inlineSupported;});
  });
  form.querySelectorAll('[data-purchase-creator-requires-js]').forEach(button=>{button.disabled=!inlineSupported;});
  form.querySelectorAll('[data-purchase-requires-js]').forEach(button=>{button.disabled=false;});estimates();stateNote();
})();
