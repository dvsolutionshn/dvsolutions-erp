from django import forms

from .restricciones_citas import MENSAJE_NO_AGENDAR, puede_ver_motivo


class PacienteCitaSelect(forms.Select):
    """Estado de agenda junto a cada opción, sin exponer motivos a otros perfiles."""

    def __init__(self, *args, usuario=None, empresa=None, original_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.ver_motivo = puede_ver_motivo(usuario, empresa) if empresa else False
        self.attrs.update({
            "data-no-agendar-selector": "1",
            "data-paciente-original": str(original_id or ""),
        })

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        paciente = getattr(value, "instance", None)
        if paciente and paciente.no_agendar:
            mensaje = MENSAJE_NO_AGENDAR
            if self.ver_motivo and paciente.motivo_no_agendar:
                mensaje += f"\nMotivo: {paciente.motivo_no_agendar}"
            option["attrs"]["data-no-agendar"] = "1"
            option["attrs"]["data-restriccion-mensaje"] = mensaje
            option["label"] = f"{label} · NO AGENDAR"
        return option
