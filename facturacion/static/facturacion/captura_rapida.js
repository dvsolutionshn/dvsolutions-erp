(() => {
  'use strict';
  const form = document.querySelector('#capture-form');
  const field = name => form.elements.namedItem(name);
  const supplier = document.querySelector('#supplier');
  const options = document.querySelector('#supplier-options');
  const message = document.querySelector('#row-message');
  const status = document.querySelector('#capture-status');
  const skip = document.querySelector('#skip');
  const save = document.querySelector('#save');
  const dialog = document.querySelector('#supplier-dialog');
  const book = JSON.parse(document.querySelector('#initial-book').textContent);
  const records = new Map();
  let editing = null;
  let bookState = book.estado_libro;
  const amountNames = ['exento','base_15','base_18','isv_15','isv_18','total'];
  const allowed = action => form.dataset[action] === 'true';
  let creatingSupplier = false, newSupplierName = '';
  let suggestions = [], selected = 0, searchVersion = 0, duplicateVersion = 0;
  let timer, duplicateTimer, busy = false, duplicate = false, saved = 0;
  const identity = () => `${field('proveedor').value}|${field('numero_factura').value}`;
  const closeOptions = () => { options.hidden = true; supplier.setAttribute('aria-expanded', 'false'); supplier.removeAttribute('aria-activedescendant'); };
  async function api(params, init) {
    const url = new URL(form.action);
    if (params) url.search = new URLSearchParams(params);
    const response = await fetch(url, init);
    if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('Sesión o acceso no disponible. Vuelve a iniciar sesión; la fila se conserva.');
    const data = await response.json();
    if (!response.ok && !data.errores && !data.duplicada) throw new Error(data.error || 'No se pudo completar la operación. Reintenta; la fila se conserva.');
    return data;
  }
  function showDuplicate(data) {
    duplicate = Boolean(data); skip.hidden = !duplicate; syncControls();
    message.textContent = data ? `FACTURA YA REGISTRADA\nFecha: ${data.fecha} · Proveedor: ${data.proveedor} · Nº factura: ${data.numero} · Total: L ${data.total} · Estado: ${data.estado}` : '';
  }
  async function checkDuplicate() {
    const version = ++duplicateVersion, key = identity();
    if (!field('proveedor').value || !field('numero_factura').value) return;
    try {
      const data = await api({accion:'duplicado', proveedor:field('proveedor').value, numero_factura:field('numero_factura').value, registro_id:editing?.id || ''});
      if (version !== duplicateVersion || key !== identity() || busy) return;
      if (data.errores) { message.textContent = Object.values(data.errores).flat().join(' '); return; }
      showDuplicate(data.duplicada);
    } catch (error) { if (version === duplicateVersion && !busy) message.textContent = error.message; }
  }
  function changedIdentity() {
    ++duplicateVersion; clearTimeout(duplicateTimer); showDuplicate(null);
    duplicateTimer = setTimeout(checkDuplicate, 180);
  }
  function choose() {
    const item = suggestions[selected];
    if (!item) return;
    if (item.create) {
      newSupplierName = item.nombre;
      ++searchVersion; closeOptions();
      document.querySelector('#supplier-new-name').textContent = newSupplierName;
      document.querySelector('#supplier-create-form').reset();
      document.querySelector('#supplier-error').textContent = '';
      dialog.showModal(); document.querySelector('#supplier-rtn').focus();
      return;
    }
    supplier.value = item.nombre; field('proveedor').value = item.id;
    ++searchVersion; closeOptions(); changedIdentity();
  }
  function renderOptions() {
    const rect = supplier.getBoundingClientRect();
    options.style.top = `${rect.bottom + 4}px`;
    options.style.left = `${Math.min(rect.left, window.innerWidth - 300)}px`;
    options.replaceChildren();
    suggestions.forEach((item, index) => {
      const option = document.createElement('div');
      option.id = `supplier-${item.id}`; option.role = 'option';
      option.setAttribute('aria-selected', String(index === selected));
      option.textContent = item.create ? `+ Crear «${item.nombre}» · ingresar RTN` : `${item.nombre}${item.rtn ? ' · ' + item.rtn : ''}`;
      option.addEventListener('mousedown', event => {event.preventDefault(); selected = index; choose(); if (!dialog?.open) field('numero_factura').focus();});
      options.append(option);
    });
    options.hidden = !suggestions.length;
    supplier.setAttribute('aria-expanded', String(!options.hidden));
    if (suggestions[selected]) {
      supplier.setAttribute('aria-activedescendant', `supplier-${suggestions[selected].id}`);
      options.children[selected].scrollIntoView({block:'nearest'});
    }
  }
  async function search() {
    const version = ++searchVersion, query = supplier.value;
    try {
      const data = await api({accion:'proveedores', q:query});
      if (version !== searchVersion || document.activeElement !== supplier) return;
      suggestions = data.proveedores;
      if (dialog && query.trim() && !suggestions.some(item => item.nombre.toLocaleLowerCase() === query.trim().toLocaleLowerCase())) {
        suggestions.push({id:'create',nombre:query.trim(),create:true});
      }
      selected = 0; renderOptions();
    } catch(error) { if (version === searchVersion) message.textContent = error.message; }
  }
  supplier.addEventListener('input', () => {
    field('proveedor').value = ''; suggestions = []; closeOptions(); ++searchVersion;
    changedIdentity(); clearTimeout(timer); timer = setTimeout(search, 100);
  });
  supplier.addEventListener('focus', search);
  supplier.addEventListener('blur', () => { ++searchVersion; closeOptions(); });
  window.addEventListener('resize', closeOptions);
  supplier.addEventListener('keydown', async event => {
    if ((event.key === 'ArrowDown' || event.key === 'ArrowUp') && suggestions.length) {
      event.preventDefault(); selected = (selected + (event.key === 'ArrowDown' ? 1 : -1) + suggestions.length) % suggestions.length; renderOptions();
    } else if ((event.key === 'Tab' && !event.shiftKey || event.key === 'Enter') && !options.hidden) {
      event.preventDefault(); choose(); if (!dialog?.open) field('numero_factura').focus();
    } else if ((event.key === 'Tab' && !event.shiftKey || event.key === 'Enter') && !field('proveedor').value && supplier.value.trim()) {
      event.preventDefault(); clearTimeout(timer); await search();
      if (suggestions.length) {choose(); if (!dialog?.open) field('numero_factura').focus();}
      else message.textContent = 'No se encontraron proveedores. Revisa la búsqueda.';
    } else if (event.key === 'Escape') closeOptions();
  });
  if (dialog) {
    const createForm = document.querySelector('#supplier-create-form');
    const rtn = document.querySelector('#supplier-rtn');
    const createSave = document.querySelector('#supplier-create-save');
    const cancel = document.querySelector('#supplier-create-cancel');
    cancel.addEventListener('click', () => { if (!creatingSupplier) { dialog.close(); supplier.focus(); } });
    dialog.addEventListener('cancel', event => { if (creatingSupplier) event.preventDefault(); });
    createForm.addEventListener('submit', async event => {
      event.preventDefault(); if (creatingSupplier) return;
      creatingSupplier = true; createSave.disabled = true; cancel.disabled = true; rtn.readOnly = true;
      const errorBox = document.querySelector('#supplier-error'); errorBox.textContent = '';
      const body = new FormData();
      body.set('csrfmiddlewaretoken',field('csrfmiddlewaretoken').value);
      body.set('accion','crear_proveedor'); body.set('nombre',newSupplierName); body.set('rtn',rtn.value);
      try {
        const data = await api(null, {method:'POST',body});
        if (data.errores) {errorBox.textContent = Object.values(data.errores).flat().join(' '); return;}
        supplier.value = data.proveedor.nombre; field('proveedor').value = data.proveedor.id;
        ++searchVersion; suggestions = []; closeOptions(); dialog.close(); changedIdentity();
        status.textContent = data.creado ? 'Proveedor creado. Continúa con la factura.' : 'RTN ya registrado: proveedor existente seleccionado.';
        field('numero_factura').focus();
      } catch (error) {errorBox.textContent = error.message;}
      finally {creatingSupplier = false; createSave.disabled = false; cancel.disabled = false; rtn.readOnly = false;}
    });
  }
  field('numero_factura').addEventListener('input', changedIdentity);
  field('numero_factura').addEventListener('blur', () => {
    const input = field('numero_factura'), n = input.value.trim();
    if (/^[0-9]{9,}$/.test(n)) input.value = `${n.slice(0,3)}-${n.slice(3,6)}-${n.slice(6,8)}-${n.slice(8)}`;
    checkDuplicate();
  });
  field('fecha_documento').addEventListener('blur', () => {
    const input = field('fecha_documento');
    input.value = input.value.trim();
    if (/^[0-9]{6}$/.test(input.value)) input.value = `${input.value.slice(0,2)}/${input.value.slice(2,4)}/20${input.value.slice(4)}`;
    const short = input.value.match(/^([0-9]{1,2})([/-])([0-9]{1,2})\2([0-9]{2}|[0-9]{4})$/);
    if (short) input.value = `${short[1].padStart(2,'0')}/${short[3].padStart(2,'0')}/${short[4].length === 2 ? '20' + short[4] : short[4]}`;
  });
  // Aritmética decimal exacta: centavos enteros, redondeo HALF_EVEN de Decimal.
  function cents(value) {
    value = value.trim() || '0';
    if (!/^[0-9]+(?:\.[0-9]{0,2})?$/.test(value)) throw new Error('Usa montos positivos con punto decimal y hasta dos decimales.');
    const [whole, fraction = ''] = value.split('.');
    return BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0'));
  }
  function tax(base, rate) {
    const value = base * rate, quotient = value / 100n, rest = value % 100n;
    return quotient + (rest > 50n || rest === 50n && quotient % 2n === 1n ? 1n : 0n);
  }
  const money = value => `${value / 100n}.${String(value % 100n).padStart(2,'0')}`;
  function calculate() {
    try {
      const exento = cents(field('exento').value), b15 = cents(field('base_15').value), b18 = cents(field('base_18').value);
      const t15 = tax(b15, 15n), t18 = tax(b18, 18n);
      for (const [name, value] of Object.entries({isv_15:t15,isv_18:t18,total:exento+b15+b18+t15+t18+cents(editing?.exonerado || '')})) document.getElementById(name).textContent = money(value);
      return true;
    } catch(error) {
      for (const name of ['isv_15','isv_18','total']) document.getElementById(name).textContent = '—';
      if (!duplicate) message.textContent = error.message;
      return false;
    }
  }
  for (const name of ['exento','base_15','base_18']) field(name).addEventListener('input', calculate);
  function nextRow() {
    ++searchVersion; ++duplicateVersion; clearTimeout(timer); clearTimeout(duplicateTimer);
    editing = null; form.reset(); suggestions = []; closeOptions(); showDuplicate(null);
    form.querySelectorAll('[aria-invalid]').forEach(input => input.removeAttribute('aria-invalid'));
    document.querySelectorAll('.is-editing').forEach(row => row.classList.remove('is-editing'));
    calculate(); syncControls(); field('fecha_documento').focus();
  }
  skip.addEventListener('click', () => {if (!busy) nextRow();});
  form.addEventListener('keydown', event => {
    if (busy) { event.preventDefault(); return; }
    if (event.key === 'Escape' && duplicate) {event.preventDefault(); nextRow();}
    if (event.key === 'Enter' && event.target.tagName === 'INPUT') {
      event.preventDefault();
      if (event.target === field('base_18')) form.requestSubmit();
    }
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy || duplicate || !(editing ? allowed('edit') : allowed('create') && bookState === 'en_proceso') || !calculate()) return;
    if (!field('proveedor').value && !(editing && !editing.proveedor_id && supplier.value === editing.proveedor)) { message.textContent = 'Selecciona un proveedor de las sugerencias.'; supplier.focus(); return; }
    busy = true; ++duplicateVersion; save.disabled = true; closeOptions();
    const body = new FormData(form);
    if (editing) {body.set('accion','editar'); body.set('registro_id',editing.id); body.set('version',editing.version);}
    const inputs = [...form.querySelectorAll('input:not([type=hidden])')];
    inputs.forEach(input => {input.readOnly = true; input.removeAttribute('aria-invalid');});
    status.textContent = 'Guardando…';
    try {
      const data = await api(null, {method:'POST', body});
      if (data.duplicada) showDuplicate(data.duplicada);
      else if (data.errores) {
        const labels = {fecha_documento:'Fecha',proveedor:'Proveedor',numero_factura:'Nº factura',exento:'Exenta',base_15:'Base 15%',base_18:'Base 18%'};
        message.textContent = Object.entries(data.errores).map(([name, errors]) => `${labels[name] ? labels[name] + ': ' : ''}${errors.join(' ')}`).join('\n');
        for (const name of Object.keys(data.errores)) field(name)?.setAttribute('aria-invalid','true');
      } else {
        applyBook(data); saved++; nextRow();
        status.textContent = `${saved} guardada(s) · Última: ${data.registro.numero} · L ${data.registro.total}`;
      }
      if (!data.registro) status.textContent = 'Fila pendiente';
    } catch(error) { message.textContent = error.message; status.textContent = 'Sin confirmar. Reintenta guardar.'; }
    finally { busy = false; inputs.forEach(input => {input.readOnly = false;}); syncControls(); }
  });

  function syncControls() {
    const editable = editing ? allowed('edit') : allowed('create') && bookState === 'en_proceso';
    document.querySelector('#capture-row').hidden = !editable;
    save.disabled = busy || duplicate || !editable;
    save.textContent = editing ? 'Guardar cambios · ENTER' : 'Guardar · ENTER';
    document.querySelector('#cancel-edit').hidden = !editable;
    document.querySelector('#cancel-edit').textContent = editing ? 'Cancelar edición' : 'Limpiar fila';
    document.querySelector('#book-state').textContent = bookState === 'finalizado' ? 'Finalizado' : 'En proceso';
    const toggle = document.querySelector('#book-toggle');
    if (toggle) {toggle.textContent = bookState === 'finalizado' ? 'Reabrir libro' : 'Finalizar libro'; toggle.disabled = busy;}
  }
  function upsert(record) {
    records.set(record.id,record);
    let row = document.getElementById(`book-row-${record.id}`);
    if (!row) {row = document.createElement('tr'); row.id = `book-row-${record.id}`; document.querySelector('#saved-rows').append(row);}
    row.classList.toggle('is-void',record.estado_codigo === 'anulada');
    row.replaceChildren();
    for (const key of ['fecha','proveedor','numero',...amountNames]) {
      const td = document.createElement('td'); td.textContent = record[key];
      if (amountNames.includes(key)) td.style.textAlign = 'right';
      if (key === 'numero') {
        const actions = document.createElement('div'); actions.className = 'row-actions';
        if (record.estado_codigo === 'anulada') {actions.textContent = 'Anulada';}
        else for (const [permission,label,action] of [['edit','Editar','edit'],['void','Anular','void']]) {
          if (!allowed(permission)) continue;
          const button = document.createElement('button'); button.type = 'button'; button.tabIndex = -1;
          button.textContent = label; button.dataset.action = action; button.dataset.id = record.id;
          actions.append(button);
        }
        td.append(actions);
        if (cents(record.exonerado || '0') > 0n) {
          const note = document.createElement('small'); note.textContent = `Incluye exonerado: ${record.exonerado}`; td.append(note);
        }
      }
      row.append(td);
    }
  }
  function applyBook(data) {
    if (data.registro) upsert(data.registro);
    if (data.registros) {
      records.clear(); document.querySelector('#saved-rows').replaceChildren(); data.registros.forEach(upsert);
    }
    if (data.resumen) {
      for (const key of amountNames) document.getElementById(`sum-${key}`).textContent = data.resumen[key];
      document.querySelector('#book-count').textContent = `${data.resumen.documentos} facturas activas`;
    }
    bookState = data.estado_libro || bookState; syncControls();
  }
  const hasDraft = () => ['fecha_documento','numero_factura','exento','base_15','base_18'].some(name => field(name).value) || supplier.value;
  document.querySelector('#cancel-edit').addEventListener('click', () => {if (!busy) nextRow();});
  async function mutateBook(values) {
    if (busy) return;
    busy = true; syncControls();
    const body = new FormData(); body.set('csrfmiddlewaretoken',field('csrfmiddlewaretoken').value);
    for (const [key,value] of Object.entries(values)) body.set(key,value);
    try {
      const data = await api(null,{method:'POST',body});
      if (data.errores) {message.textContent = Object.values(data.errores).flat().join(' '); return;}
      applyBook(data); message.textContent = ''; status.textContent = 'Libro actualizado.';
    } catch(error) {message.textContent = error.message;}
    finally {busy = false; syncControls();}
  }
  document.querySelector('#saved-rows').addEventListener('click', event => {
    const button = event.target.closest('button[data-action]'); if (!button || busy) return;
    const record = records.get(Number(button.dataset.id)); if (!record) return;
    if (hasDraft() || editing) {message.textContent = 'Termina o cancela la fila activa antes de cambiar otra factura.'; return;}
    if (button.dataset.action === 'void') {
      mutateBook({accion:'anular',registro_id:record.id,version:record.version}); return;
    }
    editing = record;
    supplier.value = record.proveedor; field('proveedor').value = record.proveedor_id || '';
    field('fecha_documento').value = record.fecha; field('numero_factura').value = record.numero;
    for (const key of ['exento','base_15','base_18']) field(key).value = record[key];
    showDuplicate(null); calculate(); syncControls();
    document.getElementById(`book-row-${record.id}`).classList.add('is-editing');
    field('fecha_documento').focus();
    status.textContent = `Editando ${record.numero}`;
  });
  document.querySelector('#book-toggle')?.addEventListener('click', () => {
    if (hasDraft() || editing) {message.textContent = 'Termina o cancela la fila activa antes de cambiar el estado del libro.'; return;}
    mutateBook({accion:'estado',estado:bookState === 'finalizado' ? 'en_proceso' : 'finalizado'});
  });
  document.querySelector('#book-refresh').addEventListener('click', async () => {
    if (busy) return;
    try {applyBook(await api({accion:'cuadro'})); status.textContent = 'Cuadro actualizado.';}
    catch(error) {message.textContent = error.message;}
  });
  applyBook(book);
})();
