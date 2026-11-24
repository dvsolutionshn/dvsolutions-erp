# Publicar los buscadores en la version web activa

El servicio de produccion puede ejecutar una copia en
`/var/www/dvsolutions-releases`, distinta del repositorio
`/var/www/dvsolutions-erp`. Un `git pull` en el repositorio y un reinicio no
actualizan por si solos esa copia.

Este actualizador aplica **solo** los ocho archivos de los buscadores moviles
del commit `bd49c1b89c25507ac434aeeafb98679b7bacfdce`. No es un desplegador
generico de futuros commits. Copia la version activa, conserva sus otros
parches y rechaza modificaciones no reconocidas en los archivos afectados.
Incluye la dependencia de navegacion del template y reconoce exclusivamente
la variante historica revisada que no tenia aun las correcciones de navegacion
y fecha. Una variante diferente vuelve a detener el despliegue.

En una consola Linux autenticada como root:

```bash
cd /var/www/dvsolutions-erp
git pull --ff-only origin main
.venv/bin/python deploy/deploy_app_search_release.py
```

Si `systemctl` dejo la consola en un paginador `(END)`, salir con `q`
antes de introducir comandos. El actualizador comprueba que los dos nuevos
archivos estaticos compartidos ya coincidan con el commit publicado; no ejecuta
un `collectstatic` global que pueda revertir otros parches.

El resultado debe indicar `DEPLOY_OK` o `ALREADY_ACTIVE`. Ante
`DEPLOY_STOPPED`, no cambiar manualmente el servicio al repositorio ni
sobrescribir archivos de la version activa: revisar el motivo. Una copia
preparada pero no activada se conserva para diagnostico.

Antes de activar se verifican Django, origen de modulos y templates, proceso
actual y archivos revisados. Tras activar se verifican proceso, HTTP, rutas
de las cuatro clinicas, webhook y estado del temporizador CRM. Los fallos de
activacion restauran el servicio anterior retirando solo el override nuevo.
La carpeta anterior y el respaldo se conservan.

No aplica migraciones, no modifica datos o permisos, no toca el entorno ni
reinicia Nginx o el temporizador CRM. Despues, cerrar y abrir la app y comprobar
el buscador dentro de **Ver Facturas** y la consulta de citas por nombre.
