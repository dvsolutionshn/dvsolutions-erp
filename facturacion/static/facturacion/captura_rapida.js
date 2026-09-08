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
    duplicate = Boolean(data); skip.hidden = !duplicate; save.disabled = duplicate;
    message.textContent = data ? `FACTURA YA REGISTRADA\nFecha: ${data.fecha} · Proveedor: ${data.proveedor} · Nº factura: ${data.numero} · Total: L ${data.total} · Estado: ${data.estado}` : '';
  }
  async function checkDuplicate() {
    const version = ++duplicateVersion, key = identity();
    if (!field('proveedor').value || !field('numero_factura').value) return;
    try {
      const data = await api({accion:'duplicado', proveedor:field('proveedor').value, numero_factura:field('numero_factura').value});
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
      option.textContent = `${item.nombre}${item.rtn ? ' · ' + item.rtn : ''}`;
      option.addEventListener('mousedown', event => {event.preventDefault(); selected = index; choose(); field('numero_factura').focus();});
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
      suggestions = data.proveedores; selected = 0; renderOptions();
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
      choose(); if (event.key === 'Enter') {event.preventDefault(); field('numero_factura').focus();}
    } else if ((event.key === 'Tab' && !event.shiftKey || event.key === 'Enter') && !field('proveedor').value && supplier.value.trim()) {
      event.preventDefault(); clearTimeout(timer); await search();
      if (suggestions.length) {choose(); field('numero_factura').focus();}
      else message.textContent = 'No se encontraron proveedores. Revisa la búsqueda.';
    } else if (event.key === 'Escape') closeOptions();
  });
  field('numero_factura').addEventListener('input', changedIdentity);
  field('numero_factura').addEventListener('blur', () => {
    const input = field('numero_factura'), n = input.value.trim();
    if (/^[0-9]{9,}$/.test(n)) input.value = `${n.slice(0,3)}-${n.slice(3,6)}-${n.slice(6,8)}-${n.slice(8)}`;
    checkDuplicate();
  });
  field('fecha_documento').addEventListener('blur', () => {
    const input = field('fecha_documento');
    if (/^[0-9]{6}$/.test(input.value)) input.value = `${input.value.slice(0,2)}/${input.value.slice(2,4)}/20${input.value.slice(4)}`;
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
      for (const [name, value] of Object.entries({isv_15:t15,isv_18:t18,total:exento+b15+b18+t15+t18})) document.getElementById(name).textContent = money(value);
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
    form.reset(); suggestions = []; closeOptions(); showDuplicate(null);
    form.querySelectorAll('[aria-invalid]').forEach(input => input.removeAttribute('aria-invalid'));
    calculate(); field('fecha_documento').focus();
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
    if (busy || duplicate || !calculate()) return;
    if (!field('proveedor').value) { message.textContent = 'Selecciona un proveedor de las sugerencias.'; supplier.focus(); return; }
    busy = true; ++duplicateVersion; save.disabled = true; closeOptions();
    const body = new FormData(form);
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
        saved++; nextRow();
        status.textContent = `${saved} guardada(s) · Última: ${data.registro.numero} · L ${data.registro.total}`;
      }
      if (!data.registro) status.textContent = 'Fila pendiente';
    } catch(error) { message.textContent = error.message; status.textContent = 'Sin confirmar. Reintenta guardar.'; }
    finally { busy = false; inputs.forEach(input => {input.readOnly = false;}); save.disabled = duplicate; }
  });
})();
