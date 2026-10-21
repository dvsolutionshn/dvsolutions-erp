import json
from decimal import Decimal

from django.db import transaction
from django.db.models import Max, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from .models import LineaPedidoMero, PedidoMero, ProductoMero, PuestoMero


PRODUCTOS_DEMO = [
    ("Baleada sencilla", "🫓", "25.00"),
    ("Baleada con huevo", "🍳", "35.00"),
    ("Baleada especial", "🥩", "55.00"),
    ("Baleada super", "✨", "70.00"),
    ("Café", "☕", "20.00"),
    ("Fresco", "🥤", "20.00"),
]


def _puesto_demo():
    puesto, _ = PuestoMero.objects.get_or_create(
        slug="baleadas-dona-maria",
        defaults={
            "nombre": "Baleadas Doña María",
            "descripcion": "Piloto móvil de inclusión digital",
        },
    )
    if not puesto.productos.exists():
        ProductoMero.objects.bulk_create([
            ProductoMero(puesto=puesto, nombre=nombre, emoji=emoji, precio=precio, orden=orden)
            for orden, (nombre, emoji, precio) in enumerate(PRODUCTOS_DEMO, start=1)
        ])
    return puesto


def _pedido_payload(pedido):
    transcurrido = max(0, int((timezone.now() - pedido.fecha_creacion).total_seconds()))
    return {
        "id": pedido.id,
        "numero": pedido.numero,
        "color": pedido.color,
        "vehiculo": pedido.referencia_vehiculo or "Cliente en mostrador",
        "tipo_atencion": pedido.tipo_atencion,
        "tipo_atencion_label": pedido.get_tipo_atencion_display(),
        "nota": pedido.nota,
        "estado": pedido.estado,
        "estado_label": pedido.get_estado_display(),
        "metodo_pago": pedido.metodo_pago,
        "metodo_pago_label": pedido.get_metodo_pago_display(),
        "total": float(pedido.total),
        "hora": timezone.localtime(pedido.fecha_creacion).strftime("%I:%M %p"),
        "segundos": transcurrido,
        "lineas": [
            {
                "nombre": linea.producto.nombre,
                "emoji": linea.producto.emoji,
                "cantidad": linea.cantidad,
                "subtotal": float(linea.subtotal),
            }
            for linea in pedido.lineas.select_related("producto").all()
        ],
    }


@ensure_csrf_cookie
def demo(request):
    puesto = _puesto_demo()
    return render(request, "mero/demo.html", {"puesto": puesto})


@require_GET
def estado_demo(request):
    puesto = _puesto_demo()
    hoy = timezone.localdate()
    pedidos_qs = (
        puesto.pedidos.filter(fecha_creacion__date=hoy)
        .exclude(estado="cancelado")
        .prefetch_related("lineas__producto")
    )
    activos = list(pedidos_qs.exclude(estado="entregado").order_by("fecha_creacion")[:60])
    entregados_recientes = list(pedidos_qs.filter(estado="entregado").order_by("-fecha_actualizacion")[:20])
    pedidos = activos + entregados_recientes
    entregados = [pedido for pedido in pedidos if pedido.estado == "entregado"]
    return JsonResponse({
        "puesto": puesto.nombre,
        "productos": [
            {"id": p.id, "nombre": p.nombre, "emoji": p.emoji, "precio": float(p.precio)}
            for p in puesto.productos.filter(activo=True)
        ],
        "pedidos": [_pedido_payload(pedido) for pedido in pedidos],
        "resumen": {
            "pedidos": len(entregados),
            "ventas": float(sum((pedido.total for pedido in entregados), Decimal("0.00"))),
            "en_cocina": sum(pedido.estado in {"nuevo", "preparando"} for pedido in pedidos),
            "listos": sum(pedido.estado == "listo" for pedido in pedidos),
        },
    })


@require_POST
def crear_pedido(request):
    puesto = _puesto_demo()
    try:
        data = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "El pedido no tiene un formato válido."}, status=400)

    items = data.get("items") or []
    if not items:
        return JsonResponse({"ok": False, "error": "Agregá al menos un producto."}, status=400)

    producto_ids = []
    cantidades = {}
    for item in items:
        try:
            producto_id = int(item["producto_id"])
            cantidad = int(item["cantidad"])
        except (KeyError, TypeError, ValueError):
            return JsonResponse({"ok": False, "error": "Hay un producto inválido."}, status=400)
        if cantidad < 1 or cantidad > 99:
            return JsonResponse({"ok": False, "error": "La cantidad debe estar entre 1 y 99."}, status=400)
        producto_ids.append(producto_id)
        cantidades[producto_id] = cantidad

    productos = {p.id: p for p in puesto.productos.filter(id__in=producto_ids, activo=True)}
    if len(productos) != len(set(producto_ids)):
        return JsonResponse({"ok": False, "error": "Uno de los productos ya no está disponible."}, status=400)

    with transaction.atomic():
        hoy = timezone.localdate()
        ultimo = (
            PedidoMero.objects.select_for_update()
            .filter(puesto=puesto, fecha_creacion__date=hoy)
            .aggregate(numero=Max("numero"))["numero"]
            or 0
        )
        pedido = PedidoMero.objects.create(
            puesto=puesto,
            numero=ultimo + 1,
            tipo_atencion=(data.get("tipo_atencion") if data.get("tipo_atencion") in {"carro", "fila"} else "carro"),
            referencia_vehiculo=str(data.get("vehiculo") or "")[:80].strip(),
            nota=str(data.get("nota") or "")[:240].strip(),
        )
        total = Decimal("0.00")
        lineas = []
        for producto_id, cantidad in cantidades.items():
            producto = productos[producto_id]
            total += producto.precio * cantidad
            lineas.append(LineaPedidoMero(
                pedido=pedido,
                producto=producto,
                cantidad=cantidad,
                precio_unitario=producto.precio,
            ))
        LineaPedidoMero.objects.bulk_create(lineas)
        pedido.total = total
        pedido.save(update_fields=["total"])

    pedido = PedidoMero.objects.prefetch_related("lineas__producto").get(pk=pedido.pk)
    return JsonResponse({"ok": True, "pedido": _pedido_payload(pedido)}, status=201)


@require_POST
def actualizar_pedido(request, pedido_id):
    puesto = _puesto_demo()
    pedido = get_object_or_404(PedidoMero, pk=pedido_id, puesto=puesto)
    try:
        data = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "La actualización no es válida."}, status=400)

    estado = data.get("estado")
    metodo_pago = data.get("metodo_pago")
    campos = []
    if estado is not None:
        estados_validos = {value for value, _ in PedidoMero.ESTADOS}
        if estado not in estados_validos:
            return JsonResponse({"ok": False, "error": "Estado inválido."}, status=400)
        pedido.estado = estado
        campos.append("estado")
    if metodo_pago is not None:
        metodos_validos = {value for value, _ in PedidoMero.METODOS_PAGO}
        if metodo_pago not in metodos_validos:
            return JsonResponse({"ok": False, "error": "Método de pago inválido."}, status=400)
        pedido.metodo_pago = metodo_pago
        campos.append("metodo_pago")
    if not campos:
        return JsonResponse({"ok": False, "error": "No hay cambios para guardar."}, status=400)
    pedido.save(update_fields=campos + ["fecha_actualizacion"])
    pedido = PedidoMero.objects.prefetch_related("lineas__producto").get(pk=pedido.pk)
    return JsonResponse({"ok": True, "pedido": _pedido_payload(pedido)})


@require_GET
def manifest(request):
    return JsonResponse({
        "name": "Mero - Tu negocio al mero toque",
        "short_name": "Mero",
        "start_url": "/mero/demo/",
        "display": "standalone",
        "background_color": "#fff9ef",
        "theme_color": "#111827",
        "icons": [],
    }, content_type="application/manifest+json")


@require_GET
def service_worker(request):
    response = HttpResponse(
        """const CACHE='mero-demo-v1';const ASSETS=['/mero/demo/','/mero/manifest.webmanifest'];self.addEventListener('install',e=>e.waitUntil(caches.open(CACHE).then(c=>c.addAll(ASSETS))));self.addEventListener('fetch',e=>{if(e.request.method==='GET')e.respondWith(fetch(e.request).then(r=>{const copy=r.clone();caches.open(CACHE).then(c=>c.put(e.request,copy));return r}).catch(()=>caches.match(e.request)));});""",
        content_type="application/javascript",
    )
    response["Service-Worker-Allowed"] = "/mero/"
    return response
