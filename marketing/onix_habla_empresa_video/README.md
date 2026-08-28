# ONIX — Habla con tu empresa

Comercial cinematográfico de 86 segundos basado en la interfaz real de ONIX dentro de DV Solutions ERP.

## Contenido

- Conversaciones reales mostradas como flujo principal.
- Escritura progresiva, procesamiento y respuestas de ONIX.
- Ventas, cuentas por cobrar, comparación anual, inventario, planilla y CRM.
- Cierre con análisis integral y prioridades del día.
- Capturas reales del ERP en 16:9 y 9:16.
- Música cinematográfica y efectos de interfaz.
- Versiones principales sin narración y versiones alternativas con voces en español de Honduras.

## Renderizar

```powershell
cd F:\dvsolutions
.\venv\Scripts\python.exe marketing\onix_habla_empresa_video\render_onix_habla_empresa.py --format all
```

Solo storyboards y fotogramas de control:

```powershell
.\venv\Scripts\python.exe marketing\onix_habla_empresa_video\render_onix_habla_empresa.py --format all --preview-only
```

Los archivos terminados quedan en `marketing/onix_habla_empresa_video/output/`.

Para regenerar únicamente las voces y remezclar el audio, conservando la animación:

```powershell
.\venv\Scripts\python.exe marketing\onix_habla_empresa_video\render_onix_habla_empresa.py --format all --audio-only
```

Para crear la versión cinematográfica principal sin narración, conservando únicamente música, impactos y sonidos de interfaz:

```powershell
.\venv\Scripts\python.exe marketing\onix_habla_empresa_video\render_onix_habla_empresa.py --format all --music-only
```
