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

## Entorno nativo preparado

El proyecto incluye los directorios Android e iOS generados con Flutter estable. Android usa API minima 24, desactiva el respaldo de secretos y bloquea trafico HTTP no cifrado. iOS incluye los entitlements de Keychain Sharing requeridos por `flutter_secure_storage`.

En una computadora con Flutter instalado:

```bash
cd mobile/onix_mobile
flutter pub get
flutter analyze
flutter test
```

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

El APK piloto usa la firma de desarrollo para poder instalarse directamente. Antes de publicar en Google Play se debe crear y proteger una clave de firma definitiva. iOS requiere macOS, Xcode y una cuenta de Apple Developer para firmar y distribuir por TestFlight o App Store.
