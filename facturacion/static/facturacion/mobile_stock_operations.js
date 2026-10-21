/* Presentation only. Original views/forms own product and stock validation/save. */
(() => {
  'use strict';
  const forms=[...document.querySelectorAll('form[data-mobile-operation-form]')];if(!forms.length)return;
  const sheet=document.getElementById('mobileOperationConfirmSheet'),title=document.getElementById('mobileOperationConfirmTitle'),caption=document.getElementById('mobileOperationConfirmCaption'),values=document.getElementById('mobileOperationConfirmValues'),otherDrafts=document.getElementById('mobileOperationOtherDrafts'),typed=document.getElementById('mobileOperationConfirmation'),hint=document.getElementById('mobileOperationConfirmationHint'),status=document.getElementById('mobileOperationConfirmStatus'),confirm=document.getElementById('confirmMobileOperation'),leave=document.getElementById('mobileOperationUnsavedSheet');
  const fileValue=file=>({name:file.name,size:file.size,type:file.type,lastModified:file.name?file.lastModified:0});
  const snapshot=form=>JSON.stringify([...new FormData(form).entries()].map(([name,value])=>[name,value instanceof File?fileValue(value):value]));
  const states=forms.map(form=>({form,initial:snapshot(form),bound:form.dataset.operationUnsaved==='1'}));
  const dirty=state=>state.bound||snapshot(state.form)!==state.initial;
  const anyDirty=()=>states.some(dirty);
  const reveal=node=>{let parent=node.parentElement;while(parent){if(parent.tagName==='DETAILS')parent.open=true;parent=parent.parentElement;}};
  let current=null,reviewed=null,accepted=null,leaving=false,pendingLeave=null;
  const reviewSnapshot=state=>JSON.stringify({body:snapshot(state.form),others:states.filter(other=>other!==state&&dirty(other)).map(other=>({label:other.form.dataset.operationLabel,body:snapshot(other.form)}))});
  function renderReview(state){
    title.textContent=state.form.dataset.operationTitle||'Revisar operación';caption.textContent=state.form.dataset.operationCaption||'';values.replaceChildren();
    state.form.querySelectorAll('[data-operation-field]').forEach(field=>{
      const controls=[...field.querySelectorAll('input:not([type=hidden]),select,textarea')].filter(control=>control.name&&!control.disabled),label=field.querySelector('.msop-label')?.textContent.trim()||field.dataset.operationField;if(!controls.length)return;
      let text='';const first=controls[0],file=controls.find(control=>control.type==='file');
      if(file){text=file.files.length?[...file.files].map(item=>`${item.name} · ${item.size} bytes`).join('\n'):'Sin nuevo archivo seleccionado';if(controls.some(control=>control.type==='checkbox'&&control.checked))text+='\nSe solicitó retirar el archivo actual';}
      else if(first.type==='checkbox'&&controls.length===1)text=first.checked?'Sí':'No';
      else if(first.type==='checkbox'||first.type==='radio')text=controls.filter(control=>control.checked).map(control=>control.labels?.[0]?.textContent.trim()||control.value).join('\n')||'Sin selección';
      else if(first.tagName==='SELECT')text=[...first.selectedOptions].map(option=>option.textContent.trim()).join('\n');else text=first.value||'Sin valor';
      const row=document.createElement('div'),name=document.createElement('span'),value=document.createElement('strong');name.textContent=label;value.textContent=text;row.append(name,value);values.append(row);
    });
    const others=states.filter(other=>other!==state&&dirty(other));otherDrafts.hidden=!others.length;otherDrafts.textContent=others.length?'También hay datos sin guardar en: '+others.map(other=>other.form.dataset.operationLabel).join(', ')+'. Esta operación enviará sólo su propio formulario; los otros datos no se enviarán y se perderán al salir.':'';
    const strong=state.form.dataset.operationKind==='baja';typed.closest('.msop-field').hidden=!strong;hint.textContent=strong?'Escriba ELIMINAR para confirmar. Este texto no se envía.':'';typed.value='';confirm.disabled=strong;confirm.classList.toggle('danger',strong);status.textContent='';
  }
  function showLeave(action){pendingLeave=action;if(sheet.open)sheet.close();if(!leave.open)leave.showModal();}
  states.forEach(state=>{
    const form=state.form;form.querySelectorAll('[data-operation-requires-js]').forEach(button=>{button.disabled=false;});
    form.addEventListener('invalid',event=>reveal(event.target),{capture:true});
    form.addEventListener('submit',event=>{
      if(form.dataset.submitting==='true'){event.preventDefault();accepted=null;return;}
      const fresh=reviewSnapshot(state);
      // Keep the modal shield until native navigation: a second tap must not
      // reach a link behind it and cancel the pending POST.
      if(current===state&&accepted!==null&&accepted===fresh){accepted=null;reviewed=null;leaving=true;confirm.disabled=true;return;}
      event.preventDefault();current=state;accepted=null;reviewed=fresh;renderReview(state);if(!sheet.open)sheet.showModal();
    },{capture:true});
  });
  typed.addEventListener('input',()=>{confirm.disabled=!current||(current.form.dataset.operationKind==='baja'&&typed.value.trim()!=='ELIMINAR');});
  const submitting=()=>leaving&&current?.form.dataset.submitting==='true';
  sheet.addEventListener('cancel',event=>{if(submitting())event.preventDefault();});
  sheet.addEventListener('click',event=>{
    if(submitting()&&(event.target===sheet||event.target.closest('[data-close-sheet]'))){event.preventDefault();event.stopImmediatePropagation();}
  },{capture:true});
  confirm.addEventListener('click',()=>{
    if(!current||current.form.dataset.submitting==='true'||(current.form.dataset.operationKind==='baja'&&typed.value.trim()!=='ELIMINAR'))return;
    if(!current.form.checkValidity()){accepted=null;reviewed=null;sheet.close();current.form.reportValidity();return;}
    const fresh=reviewSnapshot(current);if(fresh!==reviewed){accepted=null;reviewed=fresh;renderReview(current);status.textContent=current.form.dataset.operationKind==='baja'?'Los datos cambiaron. Revise los valores y vuelva a escribir ELIMINAR.':'Los datos cambiaron. Revise los valores y confirme de nuevo.';return;}
    accepted=fresh;current.form.requestSubmit(current.form.querySelector('button[type=submit]'));
  });
  sheet.addEventListener('close',()=>{accepted=null;reviewed=null;typed.value='';confirm.disabled=true;});
  document.addEventListener('click',event=>{const link=event.target.closest('a[href]');if(!link||event.defaultPrevented||leaving||!anyDirty()||link.target==='_blank'||link.hasAttribute('download')||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;event.preventDefault();showLeave(()=>{leaving=true;window.location.href=link.href;});});
  document.addEventListener('submit',event=>{if(forms.includes(event.target)||event.defaultPrevented||leaving||!anyDirty())return;event.preventDefault();const form=event.target,submitter=event.submitter;showLeave(()=>{leaving=true;form.requestSubmit(submitter||undefined);});},{capture:true});
  document.getElementById('leaveMobileOperation').addEventListener('click',()=>{const action=pendingLeave;pendingLeave=null;if(action)action();});
  leave.addEventListener('close',()=>{pendingLeave=null;});
  window.addEventListener('beforeunload',event=>{if(!leaving&&anyDirty()){event.preventDefault();event.returnValue='';}});
  window.addEventListener('pageshow',()=>{leaving=false;current=null;accepted=null;reviewed=null;pendingLeave=null;if(sheet.open)sheet.close();if(leave.open)leave.close();});
  const errors=[...document.querySelectorAll('[data-operation-field] .mi-error')];errors.forEach(reveal);const first=errors[0]?.closest('[data-operation-field]')?.querySelector('input:not([type=hidden]),select,textarea');if(first){reveal(first);first.focus();}else document.querySelector('.msop-error-summary')?.focus();
})();
