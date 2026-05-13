from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AssistantResponse:
    title: str
    answer: str
    steps: list[str]
    suggested_questions: list[str]
    context_label: str

    def as_dict(self) -> dict:
        return {
            "title": self.title,
            "answer": self.answer,
            "steps": self.steps,
            "suggested_questions": self.suggested_questions,
            "context_label": self.context_label,
        }


def _normalized(text: str | None) -> str:
    return (text or "").strip().lower()


def _build_response(
    *,
    title: str,
    answer: str,
    steps: list[str],
    suggested_questions: list[str],
    context_label: str,
) -> AssistantResponse:
    return AssistantResponse(
        title=title,
        answer=answer,
        steps=steps,
        suggested_questions=suggested_questions,
        context_label=context_label,
    )


def _response_by_question(question: str, page_path: str) -> AssistantResponse:
    q = _normalized(question)
    path = _normalized(page_path)

    if any(term in q for term in ["pago", "cobro", "recibo"]) or "recibos" in path:
        return _build_response(
            title="Registrar un pago o cobro",
            answer="Para registrar un pago, primero entra al documento correcto y luego usa el botón de pago o recibo según el flujo que estés trabajando. El sistema actualiza saldo, estado de pago y reportes automáticamente.",
            steps=[
                "Abre la factura o compra correspondiente desde su listado.",
                "Verifica el saldo pendiente antes de registrar el monto.",
                "Selecciona método de pago y, si aplica, cuenta bancaria o caja.",
                "Guarda el pago y revisa el recibo o comprobante generado.",
            ],
            suggested_questions=[
                "Como ver los pagos anteriores de una factura",
                "Como registrar un pago parcial",
                "Como enviar el recibo al cliente",
            ],
            context_label="Cobros y pagos",
        )

    if any(term in q for term in ["factura", "facturar", "cai"]) or "/facturacion/" in path:
        return _build_response(
            title="Crear y emitir una factura",
            answer="El flujo ideal es crear la factura, validar cliente y líneas, revisar el resumen fiscal y luego emitirla. Si la empresa usa CAI histórico, el sistema toma el CAI correcto según la fecha de emisión del documento.",
            steps=[
                "Selecciona el cliente y define fecha de emisión y vencimiento.",
                "Agrega productos o servicios y confirma cantidades, descuentos e impuestos.",
                "Revisa el resumen del documento antes de guardar.",
                "Guarda como borrador o valida la factura directamente desde la vista del documento.",
            ],
            suggested_questions=[
                "Como usar numero manual de factura",
                "Como emitir una nota de credito",
                "Como funciona el CAI por fecha",
            ],
            context_label="Facturacion",
        )

    if any(term in q for term in ["asiento", "contabilidad", "banco", "concili"]) or "/contabilidad/" in path:
        return _build_response(
            title="Trabajo contable y bancario",
            answer="En contabilidad el orden recomendado es revisar el período, registrar o importar movimientos, clasificarlos y luego contabilizarlos. Así mantienes libros, bancos y reportes consistentes.",
            steps=[
                "Confirma que el período contable correcto esté abierto.",
                "Registra, importa o revisa movimientos bancarios y compras.",
                "Clasifica el movimiento o genera el asiento que corresponde.",
                "Contabiliza y valida el impacto en reportes contables y bancos.",
            ],
            suggested_questions=[
                "Como importar un estado de cuenta",
                "Como ver el dashboard BI financiero",
                "Como corregir un asiento",
            ],
            context_label="Contabilidad",
        )

    if any(term in q for term in ["planilla", "empleado", "vacaciones", "rrhh"]) or "/rrhh/" in path:
        return _build_response(
            title="Planilla, empleados y vacaciones",
            answer="En RRHH trabajamos por períodos de planilla y detalle por empleado. El módulo también te ayuda con vacaciones, bonos, deducciones y envío del comprobante por correo o WhatsApp.",
            steps=[
                "Crea o abre el período de planilla que corresponde.",
                "Revisa el cálculo general y luego el detalle por empleado.",
                "Ajusta deducciones, bonos u horas extra antes de cerrar.",
                "Genera voucher y compártelo por PDF, correo o WhatsApp.",
            ],
            suggested_questions=[
                "Como calcular la planilla",
                "Como enviar el voucher por WhatsApp",
                "Como registrar vacaciones",
            ],
            context_label="Recursos humanos",
        )

    if any(term in q for term in ["campaña", "campania", "crm", "whatsapp", "cita"]) or "/crm/" in path or "/citas/" in path:
        return _build_response(
            title="CRM, campañas y citas",
            answer="Desde CRM puedes trabajar plantillas, campañas y recordatorios. Si usas WhatsApp API, primero debes tener configurada la empresa y luego lanzar la campaña con la plantilla aprobada.",
            steps=[
                "Configura la integración y las preferencias del canal.",
                "Crea o selecciona una plantilla de mensaje.",
                "Prepara la campaña o la cita con los datos del cliente.",
                "Envía, da seguimiento y revisa el resultado en el módulo.",
            ],
            suggested_questions=[
                "Como enviar una campaña por WhatsApp",
                "Como configurar una plantilla",
                "Como agendar una cita",
            ],
            context_label="CRM y agenda",
        )

    return _build_response(
        title="Asistente ERP listo para ayudarte",
        answer="Puedo guiarte paso a paso dentro del sistema. Si me dices qué quieres hacer o en qué pantalla estás, te doy instrucciones claras para terminar la tarea sin salir del flujo.",
        steps=[
            "Escribe la tarea que quieres resolver, por ejemplo crear factura o generar planilla.",
            "Si ya estás dentro de un módulo, el asistente también toma en cuenta la pantalla actual.",
            "Te responderé con pasos concretos y siguientes acciones recomendadas.",
        ],
        suggested_questions=[
            "Como crear una factura",
            "Como registrar un pago",
            "Como calcular una planilla",
        ],
        context_label="Ayuda general",
    )


def responder_consulta(question: str, page_path: str) -> dict:
    return _response_by_question(question, page_path).as_dict()
