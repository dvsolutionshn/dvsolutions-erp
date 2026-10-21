/* Presentation only: original BoundFields/Form own clinical data and saving. */
(() => {
  'use strict';
  const form=document.getElementById('mobileGeneralHistoryForm');
  if(!form)return;
  const sheet=document.getElementById('generalHistoryConfirmSheet'),confirm=document.getElementById('confirmGeneralHistorySave'),status=document.getElementById('generalHistoryConfirmStatus'),save=document.getElementById('saveGeneralHistory');
  let reviewed=null,accepted=null;
  const snapshot=()=>JSON.stringify([...new FormData(form).entries()]);
  const reveal=element=>{let parent=element.parentElement;while(parent&&parent!==form){if(parent.tagName==='DETAILS')parent.open=true;parent=parent.parentElement;}};
  form.addEventListener('invalid',event=>reveal(event.target),{capture:true});
  const errors=[...form.querySelectorAll('[data-general-history-field] .mi-error')];
  errors.forEach(error=>reveal(error));
  if(errors.length){const first=errors[0].closest('[data-general-history-field]')?.querySelector('input:not([type=hidden]),select,textarea');if(first){reveal(first);first.focus();}}
  else form.querySelector('.mgh-error-summary')?.focus();
  form.addEventListener('submit',event=>{
    if(form.dataset.submitting==='true'){event.preventDefault();accepted=null;return;}
    const current=snapshot();
    if(accepted!==null&&accepted===current){accepted=null;reviewed=null;if(sheet.open)sheet.close();return;}
    event.preventDefault();accepted=null;reviewed=current;status.textContent='';if(!sheet.open)sheet.showModal();
  },{capture:true});
  confirm.addEventListener('click',()=>{
    if(form.dataset.submitting==='true')return;
    if(!form.checkValidity()){reviewed=null;accepted=null;sheet.close();form.reportValidity();return;}
    const current=snapshot();
    if(reviewed!==current){reviewed=current;accepted=null;status.textContent='Los datos cambiaron. Revise el formulario y confirme de nuevo antes de guardar.';return;}
    accepted=current;form.requestSubmit(save);
  });
  sheet.addEventListener('close',()=>{reviewed=null;accepted=null;status.textContent='';});
  window.addEventListener('pageshow',()=>{reviewed=null;accepted=null;status.textContent='';if(sheet.open)sheet.close();});
})();
