# API Onix Mobile v1

Base URL:

```text
https://dvsolutionshn.com/api/onix/mobile/v1/
```

## Endpoints

| Metodo | Ruta | Uso |
| --- | --- | --- |
| `POST` | `login/` | Autentica empresa y usuario; devuelve token y bootstrap. |
| `GET` | `bootstrap/` | Perfil, empresa, capacidades y categorias. |
| `GET` | `history/` | Ultimos mensajes de la conversacion activa. |
| `POST` | `chat/` | Envia una consulta al mismo Onix del ERP. |
| `POST` | `actions/<uuid>/` | Confirma o cancela una accion preparada. |
| `POST` | `logout/` | Revoca el token actual. |

Excepto `login/`, todas las rutas requieren:

```http
Authorization: Bearer onx_TOKEN_OPACO
Content-Type: application/json
```

## Ejemplos

Inicio de sesion:

```json
{
  "empresa": "demo_1",
  "usuario": "usuario_o_correo",
  "password": "contrasena",
  "dispositivo": "iPhone de Daniel"
}
```

Chat:

```json
{
  "pregunta": "Muestrame las facturas pendientes de cobro"
}
```

Confirmacion:

```json
{
  "decision": "confirmar"
}
```

## Configuracion del servidor

Variables disponibles:

```text
ONIX_MOBILE_TOKEN_DAYS=30
ONIX_MOBILE_MAX_SESSIONS_PER_USER=5
ONIX_MOBILE_LOGIN_MAX_ATTEMPTS=5
ONIX_MOBILE_LOGIN_WINDOW_SECONDS=900
ONIX_MOBILE_MAX_BODY_BYTES=65536
```

El despliegue requiere ejecutar `python manage.py migrate` para crear la tabla de sesiones moviles. No se debe incluir ninguna clave de OpenAI en la app ni en `--dart-define`.

