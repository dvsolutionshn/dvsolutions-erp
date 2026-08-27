# Onix Mobile

Aplicacion Flutter de Onix para iOS y Android. La app permite iniciar sesion con una empresa del ERP, recuperar la conversacion del usuario, consultar informacion por chat y confirmar o descartar acciones preparadas por Onix.

## Seguridad y arquitectura

- La app nunca contiene `OPENAI_API_KEY` ni se comunica directamente con OpenAI.
- Django autentica al usuario y entrega un token movil opaco y revocable.
- El token se guarda con `flutter_secure_storage` en Keychain (iOS) o almacenamiento cifrado respaldado por Android Keystore.
- El servidor guarda solamente el hash HMAC del token.
- Cada solicitud vuelve a validar usuario, empresa, licencia, piloto de Onix y permisos del ERP.
- Las operaciones sensibles requieren una vista previa y confirmacion explicita.

## Funciones de esta primera version

- Inicio de sesion con empresa, usuario/correo y contrasena del ERP.
- Sesion persistente y cierre remoto de sesion.
- Chat de pantalla completa e historial sincronizado con Onix web.
- Categorias disponibles: resumen, facturas, cobros, clientes y productos.
- Vista previa, confirmacion y descarte de borradores de factura.
- Categorias visibles para las siguientes etapas: calendario, gastos, pagos, bancos e inquilinos.

## Preparar el proyecto nativo

Este equipo no tiene instalado el SDK de Flutter. En una computadora de desarrollo con Flutter estable, ejecuta una sola vez:

```bash
cd mobile/onix_mobile
flutter create --platforms=android,ios --org com.dvsolutions --project-name onix_mobile .
flutter pub get
```

Para `flutter_secure_storage` 11, Android debe usar `minSdk = 23`. Tambien debe desactivarse el respaldo de datos cifrados agregando `android:allowBackup="false"` al elemento `<application>` de `android/app/src/main/AndroidManifest.xml`. En iOS se debe activar la capacidad Keychain Sharing para Runner antes de distribuir la app.

No confirmes los directorios nativos hasta ejecutar y revisar la generacion con la version de Flutter que se utilizara para publicar.

## Ejecutar contra el servidor

El URL predeterminado es `https://dvsolutionshn.com`. Para usar otro servidor:

```bash
flutter run --dart-define=ONIX_API_URL=https://tu-servidor.com
```

Pruebas y analisis:

```bash
flutter analyze
flutter test
```

La empresa debe estar incluida en `ONIX_ALLOWED_COMPANY_SLUGS`. Durante el piloto se usa `demo_1`.

## Compilar

Android:

```bash
flutter build appbundle --release --dart-define=ONIX_API_URL=https://dvsolutionshn.com
```

iOS, desde macOS con Xcode:

```bash
flutter build ipa --release --dart-define=ONIX_API_URL=https://dvsolutionshn.com
```

La firma, los identificadores definitivos, los iconos y las fichas de App Store/Google Play se configuran en la etapa de publicacion.

