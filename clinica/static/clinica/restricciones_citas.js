(function () {
  function install() {
    document.querySelectorAll('select[data-no-agendar-selector]').forEach(function (select) {
      const form = select.closest('form');
      if (!form) return;
      const notice = document.createElement('div');
      notice.setAttribute('role', 'alert');
      notice.style.cssText = 'margin-top:10px;padding:12px;border:1px solid #fecaca;border-radius:12px;background:#fff1f2;color:#9f1239;font-weight:700;line-height:1.5;white-space:pre-line';
      notice.hidden = true;
      const container = select.closest('.erp-field, .field, .clinic-field') || select.parentElement;
      container.appendChild(notice);
      function sync() {
        const option = select.selectedOptions[0];
        const blocked = option && option.dataset.noAgendar === '1' && select.value !== select.dataset.pacienteOriginal;
        notice.textContent = blocked ? option.dataset.restriccionMensaje || 'No se puede agendar una nueva cita para este paciente. Está marcado como NO AGENDAR.' : '';
        notice.hidden = !blocked;
        return blocked;
      }
      select.addEventListener('change', sync);
      form.addEventListener('submit', function (event) {
        if (sync()) {
          event.preventDefault();
          notice.scrollIntoView({block: 'center', behavior: 'smooth'});
        }
      });
      sync();
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install);
  else install();
})();
