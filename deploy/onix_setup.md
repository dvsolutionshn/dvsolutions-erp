# Onix por chat

Onix funciona en dos modos:

- **Modo guia:** no requiere API y conserva las respuestas locales del ERP.
- **Modo IA:** usa OpenAI, memoria local por empresa, herramientas de consulta, acciones confirmables y medicion de consumo.

Onix puede consultar clientes, productos, facturas y cuentas por cobrar respetando los permisos
del usuario. En la empresa piloto tambien puede preparar una factura, mostrar su vista previa y,
solamente despues de una confirmacion explicita, crearla como borrador. Todavia no emite,
modifica, paga ni envia documentos.

## Configuracion

1. Instalar dependencias:

```bash
pip install -r requirements.txt
```

2. Aplicar migraciones:

```bash
python manage.py migrate
```

3. Agregar al archivo de entorno de produccion:

```env
ONIX_ENABLED=true
OPENAI_API_KEY=colocar-la-clave-en-el-servidor
ONIX_ALLOWED_COMPANY_SLUGS=demo_1
ONIX_TRIAL_MODE=true
ONIX_TRIAL_MONTHLY_TOKEN_LIMIT=100000
ONIX_MODEL=gpt-5.6-luna
ONIX_REASONING_EFFORT=low
ONIX_TIMEOUT_SECONDS=45
ONIX_MAX_HISTORY_MESSAGES=12
ONIX_MAX_TOOL_ROUNDS=4
ONIX_INPUT_PRICE_PER_MTOK=0.20
ONIX_CACHED_INPUT_PRICE_PER_MTOK=0.02
ONIX_OUTPUT_PRICE_PER_MTOK=1.20
```

Nunca guardar la clave real en Git. Reiniciar Gunicorn despues de cambiar variables.

`ONIX_ALLOWED_COMPANY_SLUGS` controla donde aparece y donde acepta consultas Onix. Durante
el piloto debe permanecer en `demo_1`. Para habilitar varias empresas se separan sus slugs
con comas; el valor `*` lo habilita para todas las empresas del ERP.

Durante el piloto, `ONIX_TRIAL_MODE=true` aplica un segundo limite de seguridad. El limite
efectivo es el menor entre `ONIX_TRIAL_MONTHLY_TOKEN_LIMIT` y el limite configurado para la
empresa. Para pasar a operacion comercial se debe desactivar el modo de prueba y definir el
limite mensual correspondiente al plan contratado.

## Separacion y permisos

- Cada conversacion, mensaje y consumo contiene la empresa propietaria.
- Las herramientas vuelven a filtrar todas las consultas por empresa.
- Los permisos actuales del rol determinan si Onix puede consultar clientes, productos o facturas.
- Crear un borrador exige `puede_crear_facturas` tanto al prepararlo como al confirmarlo.
- La confirmacion solo puede realizarla el mismo usuario que preparo la accion y vence en 30 minutos.
- Una accion confirmada es idempotente: repetir la solicitud devuelve el mismo resultado y no duplica la factura.
- Solo se envian a OpenAI los ultimos mensajes configurados y los resultados necesarios.
- Las conversaciones completas permanecen en PostgreSQL para auditoria interna.

## Consumo

`ConfiguracionOnix.limite_tokens_mensual` establece el limite por empresa. Su valor inicial es
500,000 tokens; cero desactiva el limite interno. Cada llamada crea un `ConsumoOnix` con tokens,
modelo, herramientas usadas y costo estimado.

Los precios estimados se controlan mediante variables de entorno porque OpenAI puede cambiarlos.
Hay que revisar las tarifas antes de facturar el modulo a clientes.

## Prueba piloto

Probar inicialmente con una sola empresa y estos casos:

1. Buscar un cliente existente y uno inexistente.
2. Buscar productos o servicios y verificar precios.
3. Consultar facturas pendientes y cuentas por cobrar.
4. Probar un usuario sin permisos de facturacion.
5. Intentar consultar datos de otra empresa.
6. Pedir que prepare una factura, revisar cliente, lineas, impuestos y total sin confirmar.
7. Confirmar la vista previa y verificar que se crea un unico borrador en facturacion.
8. Repetir la confirmacion y comprobar que no se duplica la factura.
9. Descartar otra vista previa y comprobar que no crea documentos.
10. Revisar tokens, acciones y costo estimado desde el admin interno.

## Siguiente fase

La siguiente fase agregara emision fiscal y envio por correo o WhatsApp, cada uno con sus propias
validaciones y confirmaciones. La voz reutilizara el mismo backend de Onix, por lo que no sera
necesario reconstruir las reglas ni las herramientas.
