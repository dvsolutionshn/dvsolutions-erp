/* Presentation only: server validates and saves the local permission copy. */
(() => {
  'use strict';
  const form=document.getElementById('mobileClinicalPermissionsForm');
  if(!form){
    if(document.getElementById('mobileClinicalPermissionsReadonlyDraft'))window.addEventListener('beforeunload',event=>{event.preventDefault();event.returnValue='';});
    return;
  }
  const controls=[...form.querySelectorAll('[data-clinical-permission]')],groups=[...form.querySelectorAll('[data-permission-group]')];
  const sheet=document.getElementById('mobileClinicalPermissionsConfirmSheet'),leave=document.getElementById('mobileClinicalPermissionsUnsavedSheet');
  const identity=document.getElementById('mobileClinicalPermissionsConfirmIdentity'),values=document.getElementById('mobileClinicalPermissionsConfirmValues');
  const status=document.getElementById('mobileClinicalPermissionsConfirmStatus'),confirm=document.getElementById('confirmMobileClinicalPermissions'),save=document.getElementById('mobileClinicalPermissionsReview');
  const stale=form.dataset.permissionsStale==='true',bound=form.dataset.permissionsBound==='true';
  const snapshot=()=>JSON.stringify([...new FormData(form).entries()]);
  const initial=snapshot();let reviewed=null,accepted=null,busy=false,leaving=false,pendingLeave=null;
  const dirty=()=>bound||snapshot()!==initial;
  const reveal=node=>{for(let parent=node.parentElement;parent&&parent!==form;parent=parent.parentElement)if(parent.tagName==='DETAILS')parent.open=true;};
  function counts(){
    const total=document.getElementById('mobileClinicalPermissionsCount');total.hidden=false;total.textContent=`${controls.filter(control=>control.checked).length} de ${controls.length} acciones seleccionadas`;
    groups.forEach(group=>{const output=group.querySelector('[data-permission-group-count]'),items=[...group.querySelectorAll('[data-clinical-permission]')];output.hidden=false;output.textContent=`${items.filter(control=>control.checked).length} / ${items.length}`;});
  }
  function addIdentity(label,text){const row=document.createElement('div'),term=document.createElement('dt'),description=document.createElement('dd');term.textContent=label;description.textContent=text;row.append(term,description);identity.append(row);}
  function review(changed=false){
    if(stale||busy)return;if(!form.reportValidity())return;
    identity.replaceChildren();values.replaceChildren();addIdentity('Empresa · Asignación local',form.dataset.permissionsCompany||'');addIdentity('Persona',form.dataset.permissionsUser||'');addIdentity('Cuenta',form.dataset.permissionsAccount||'');
    groups.forEach(group=>{
      const section=document.createElement('section'),heading=document.createElement('h3'),list=document.createElement('ul');section.className='mup-review-group';heading.textContent=group.dataset.permissionGroupName;
      group.querySelectorAll('[data-clinical-permission]').forEach(control=>{const row=document.createElement('li'),label=document.createElement('span'),state=document.createElement('strong');label.textContent=control.dataset.permissionLabel;state.textContent=control.checked?'Seleccionado':'No seleccionado';row.classList.toggle('selected',control.checked);row.append(label,state);list.append(row);});
      section.append(heading,list);values.append(section);
    });
    reviewed=snapshot();accepted=null;confirm.disabled=false;status.textContent=changed?'Las selecciones cambiaron. Revise el resumen actualizado y confirme de nuevo.':'';
    if(leave.open)leave.close();if(!sheet.open)sheet.showModal();confirm.focus();
  }
  form.addEventListener('input',()=>{accepted=null;counts();});form.addEventListener('change',()=>{accepted=null;counts();});
  form.addEventListener('invalid',event=>reveal(event.target),true);
  form.addEventListener('submit',event=>{
    if(stale||busy||form.dataset.submitting==='true'){event.preventDefault();event.stopImmediatePropagation();return;}
    const fresh=snapshot();
    if(sheet.open&&accepted!==null&&accepted===fresh){accepted=null;reviewed=null;busy=true;leaving=true;confirm.disabled=true;return;}
    event.preventDefault();event.stopImmediatePropagation();review();
  },true);
  confirm.addEventListener('click',()=>{if(stale||busy||!sheet.open)return;if(snapshot()!==reviewed){review(true);return;}if(!form.reportValidity())return;accepted=reviewed;form.requestSubmit(save);});
  // Retain the modal shield during native navigation, including a second tap.
  sheet.addEventListener('cancel',event=>{if(busy)event.preventDefault();});
  sheet.addEventListener('click',event=>{if(busy&&(event.target===sheet||event.target.closest('[data-close-sheet]'))){event.preventDefault();event.stopImmediatePropagation();}},true);
  sheet.addEventListener('close',()=>{if(!busy){accepted=null;reviewed=null;}});
  document.addEventListener('click',event=>{
    const link=event.target.closest('a[href]');if(!link||event.defaultPrevented||!dirty()||leaving||busy||event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey||link.target==='_blank'||link.hasAttribute('download'))return;
    event.preventDefault();pendingLeave=link.href;if(sheet.open)sheet.close();if(!leave.open)leave.showModal();
  });
  document.getElementById('leaveMobileClinicalPermissions').addEventListener('click',()=>{if(!pendingLeave||busy)return;leaving=true;location.assign(pendingLeave);});
  leave.addEventListener('close',()=>{if(!leaving)pendingLeave=null;});
  window.addEventListener('beforeunload',event=>{if(dirty()&&!leaving&&!busy){event.preventDefault();event.returnValue='';}});
  window.addEventListener('pageshow',()=>{busy=false;leaving=false;pendingLeave=null;accepted=null;reviewed=null;if(sheet.open)sheet.close();if(leave.open)leave.close();save.disabled=stale;counts();});
  counts();save.disabled=stale;document.getElementById('mobileClinicalPermissionsErrors')?.focus();
})();
