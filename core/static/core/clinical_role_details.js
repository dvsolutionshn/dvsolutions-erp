/* Read-only role preview. Never changes a select, validation or POST body. */
(() => {
  'use strict';
  const root=document.getElementById('clinicalRoleSelectionPreview');if(!root)return;
  const select=document.getElementById(root.dataset.roleSelect),content=root.querySelector('[data-role-preview-content]'),status=root.querySelector('[data-role-preview-status]'),title=document.getElementById('clinicalRolePreviewTitle');
  if(!select||!content||!status)return;
  function read(id){try{return JSON.parse(document.getElementById(id)?.textContent||'null');}catch(_error){return null;}}
  const roles=read(root.dataset.roleMap),current=read(root.dataset.roleCurrent);if(!roles||typeof roles!=='object'||Array.isArray(roles))return;
  function element(tag,className,text){const node=document.createElement(tag);if(className)node.className=className;if(text!==undefined&&text!==null)node.textContent=String(text);return node;}
  function permissionCount(total){return `${total} permiso${total===1?'':'s'} configurado${total===1?'':'s'}`;}
  function empty(text){content.replaceChildren(element('p','crd-empty',text));}
  function detail(value){
    const box=element('div','crd-detail'),meta=element('div','crd-meta');box.dataset.roleDetail='';box.dataset.roleId=String(value.id);
    meta.append(element('span','crd-badge',value.modo_label),element('span','crd-badge'+(value.activo?'':' inactive'),value.activo?'Rol activo':'Inactivo · Configurados, no habilitados'),element('span','crd-count',permissionCount(value.total_permisos)));box.append(meta);
    if(value.descripcion_registrada)box.append(element('p','crd-recorded-description',value.descripcion_registrada));
    const groups=Array.isArray(value.grupos)?value.grupos:[];
    groups.forEach(group=>{const section=element('section','crd-group'),list=element('ul','crd-permissions');section.append(element('h4','',group.grupo));
      (Array.isArray(group.permisos)?group.permisos:[]).forEach(permission=>{const row=element('li');row.dataset.permissionCode=permission.campo;row.append(element('strong','',permission.etiqueta),element('p','',permission.descripcion));list.append(row);});
      section.append(list);box.append(section);
    });
    if(!groups.length)box.append(element('p','crd-empty','Sin permisos configurados en el catálogo de este rol. Esto no describe las autorizaciones independientes de una cuenta.'));
    (Array.isArray(value.advertencias)?value.advertencias:[]).forEach(text=>box.append(element('p','crd-notice',text)));
    content.replaceChildren(box);
  }
  function render(){
    const option=select.selectedOptions[0];
    if(!option||option.disabled||option.hasAttribute('data-role-placeholder')){
      if(title)title.textContent='Permisos del rol actual';
      status.textContent='Elija explícitamente un rol o Sin rol. La información siguiente corresponde a la asignación guardada; no es una nueva selección.';
      if(current)detail(current);else empty('No hay un rol asignado. La cuenta y sus autorizaciones independientes no se modifican.');return;
    }
    if(title)title.textContent='Vista previa del rol seleccionado';
    if(option.value===''){
      status.textContent='Vista previa: Sin rol asignado. La selección todavía no se ha guardado.';
      empty('No se asignarán permisos mediante un rol en esta empresa. Esto no elimina la cuenta ni revoca sus autorizaciones independientes.');return;
    }
    if(!Object.prototype.hasOwnProperty.call(roles,option.value)||!roles[option.value]){
      status.textContent='No hay información disponible para esta selección. No se ha cambiado ni guardado el formulario.';
      empty('Revise el catálogo autorizado de roles. Esta vista previa no determina ni modifica los permisos.');return;
    }
    const value=roles[option.value];status.textContent=`Vista previa: ${value.nombre}. ${permissionCount(value.total_permisos)}${value.activo?'':'; este rol está inactivo y no concede acceso'}. La selección todavía no se ha guardado.`;detail(value);
  }
  select.addEventListener('change',render);render();
})();
