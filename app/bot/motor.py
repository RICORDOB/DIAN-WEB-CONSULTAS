"""bot — Asistente de chat web para clientes de ExoRenta.

Motor de conversación en memoria por chat anónimo (cookie firmada por el
servidor). El cliente escribe su número de cédula; el asistente busca sus
credenciales DIAN en la tabla `clientes_autorizados` (cifradas) y, si existe,
ofrece obtener la copia del RUT (PDF) o la Consulta ExoRenta (libro xls). El
lanzamiento real del job y la entrega del archivo los orquesta la API
(opcional: CPU del runner sujeta al mismo `_job_lock` de los demás jobs).

Ninguna credencial sale de este módulo en texto plano: solo se abren en el
instante de construir la orden para el runner y se descartan de inmediato.
"""

from __future__ import annotations

import re
import time
import uuid

from .. import auth
from . import cifrado

# Estados del diálogo
ESPERA_CEDULA = "espera_cedula"
NO_CLIENTE = "no_cliente"
ESPERA_OPCION = "espera_opcion"
PROCESANDO = "procesando"
# Pasos del asistente para el recibo de pago de declaración de renta
ESPERA_ANIO = "espera_anio"
ESPERA_FECHA = "espera_fecha"

# Tiempo de inactividad antes de limpiar una conversación (15 min)
INACTIVIDAD_SEG = 15 * 60

# Acciones disponibles para un cliente identificado
ACCION_RUT = "rut"
ACCION_CONSULTA = "consulta"
ACCION_RECIBO = "recibo"

# ---------------------------------------------------------------------------
# Almacén en memoria de conversaciones
# ---------------------------------------------------------------------------
_chats: dict[str, dict] = {}


def nuevo_chat() -> str:
    """Crea una conversación nueva y devuelve su chat_id (cookie firmada)."""
    chat_id = uuid.uuid4().hex
    _chats[chat_id] = {
        "estado": ESPERA_CEDULA,
        "cedula": None,
        "cliente": None,
        "opcion": None,
        "anio": None,
        "fecha_pago": None,
        "ultimo_acceso": time.time(),
    }
    return chat_id


def estado(chat_id: str) -> dict | None:
    """Devuelve el estado de una conversación (o None si expiró/no existe)."""
    c = _chats.get(chat_id)
    if not c:
        return None
    if time.time() - c["ultimo_acceso"] > INACTIVIDAD_SEG:
        _chats.pop(chat_id, None)
        return None
    c["ultimo_acceso"] = time.time()
    return c


def limpiar_inactivos() -> int:
    """Elimina conversaciones sin actividad. Devuelve cuántas se limpiaron."""
    ahora = time.time()
    viejos = [
        k for k, c in _chats.items()
        if ahora - c["ultimo_acceso"] > INACTIVIDAD_SEG
    ]
    for k in viejos:
        _chats.pop(k, None)
    return len(viejos)


def _mascara(cedula: str) -> str:
    """Máscara corta de un documento: *1234 si es largo, el número si es corto."""
    return ("*" + cedula[-4:]) if len(cedula) > 4 else cedula


_SALUDOS = ("hola", "buenas", "buenos dias", "buenas tardes", "buenas noches",
            "hi", "hello", "hey", "menu", "inicio")


def _es_saludo(texto: str) -> bool:
    t = (texto or "").strip().lower()
    base = t.split()[0] if t.split() else ""
    return (t in _SALUDOS) or (base in ("hola", "hello", "hi", "hey"))


def _respuesta(mensajes: list[str], acciones: dict | None = None,
               lanzar: dict | None = None, destino: str | None = None) -> dict:
    return {
        "mensajes": mensajes,
        "acciones": acciones or {},
        "lanzar": lanzar,
        "destino": destino,
    }


def bienvenida() -> dict:
    """Mensaje inicial del asistente (chat recién abierto)."""
    return _respuesta([
        "¡Hola! 👋 Soy el asistente de ExoRenta.",
        "Escríbeme tu número de cédula para verificar si estás registrado y "
        "te ayudo con la copia del RUT o la consulta de información exógena.",
    ])


def manejar_mensaje(chat_id: str, texto: str) -> dict:
    """Procesa un texto del usuario y devuelve la respuesta del asistente."""
    c = estado(chat_id)
    if c is None:
        c = {"estado": ESPERA_CEDULA, "cedula": None, "cliente": None,
             "opcion": None, "anio": None, "fecha_pago": None,
             "ultimo_acceso": time.time()}
        _chats[chat_id] = c

    if c["estado"] == PROCESANDO:
        return _respuesta(["Estoy procesando tu solicitud, un momento por favor. ⏳"])

    if c["estado"] == ESPERA_OPCION:
        return _respuesta(
            ["Selecciona una opción para continuar, por favor."],
            acciones=_opciones_cliente(),
        )

    if c["estado"] == ESPERA_ANIO:
        anio = (texto or "").strip()
        if not (anio.isdigit() and len(anio) == 4 and 2000 <= int(anio) <= 2100):
            return _respuesta(["Escribe el año con 4 dígitos (ej. 2025)."])
        c["anio"] = anio
        c["estado"] = ESPERA_FECHA
        return _respuesta([
            "2) ¿En qué fecha quieres el recibo de pago?",
            "Escribe la fecha en formato AAAA-MM-DD (ej. 2026-09-30).",
        ])

    if c["estado"] == ESPERA_FECHA:
        fecha = (texto or "").strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", fecha):
            return _respuesta(["Usa el formato AAAA-MM-DD (ej. 2026-09-30)."])
        c["fecha_pago"] = fecha
        c["estado"] = PROCESANDO
        cliente = auth.buscar_cliente(c["cedula"])
        if cliente is None:
            c["estado"] = NO_CLIENTE
            return _respuesta(["Ya no encuentro tu registro. Escríbeme tu cédula de nuevo."])
        try:
            contrasena = cifrado.descifrar(cliente["contrasena_cifrada"])
        except ValueError:
            c["estado"] = NO_CLIENTE
            return _respuesta([
                "No pude leer tu credencial guardada, probablemente por un cambio "
                "de configuración del servicio. Contacta al administrador.",
            ])
        return _respuesta(
            [f"¡Perfecto! Descargando el recibo de pago de tu declaración de "
             f"renta {c['anio']} con fecha {fecha}... ⏳"],
            lanzar={
                "tipo": ACCION_RECIBO,
                "tipo_documento": c["cliente"]["tipo_documento"],
                "numero_documento": c["cedula"],
                "contrasena": contrasena,
                "anio": c["anio"],
                "fecha_pago": fecha,
            },
        )

    if _es_saludo((texto or "").strip()):
        return bienvenida()

    limpo = (texto or "").replace(" ", "").strip()
    if not limpo.isdigit():
        return _respuesta([
            "El número de cédula debe contener solo dígitos. 📱",
            "Escríbelo nuevamente (ej. 123456789).",
        ])

    cliente = auth.buscar_cliente(limpo)
    if cliente is None:
        c["estado"] = NO_CLIENTE
        c["cedula"] = limpo
        return _respuesta(
            [
                f"No encuentro la cédula {_mascara(limpo)} en nuestros registros. 🤔",
                "Si tienes las credenciales del portal DIAN puedes hacer tu "
                "consulta de forma manual. ¿Cómo prefieres continuar?",
            ],
            acciones={
                "manual": {"texto": "🔑 Tengo credenciales (hacerlo manual)"},
                "reintentar": {"texto": "↩️ Reintentar con otra cédula"},
                "registro": {"texto": "📝 Solicitar una cuenta"},
            },
        )

    c["estado"] = ESPERA_OPCION
    c["cedula"] = limpo
    c["cliente"] = {
        "numero_documento": cliente["numero_documento"],
        "tipo_documento": cliente["tipo_documento"],
    }
    return _respuesta(
        [
            f"¡Hola! Veo que la cédula {_mascara(limpo)} está registrada. ✅",
            "¿Qué necesitas hacer hoy?",
        ],
        acciones=_opciones_cliente(),
    )


def _opciones_cliente() -> dict:
    return {
        ACCION_RUT: {"texto": "🪪 Copia del RUT (PDF)"},
        ACCION_CONSULTA: {"texto": "📋 Consulta ExoRenta (libro xls)"},
        ACCION_RECIBO: {"texto": "🧾 Recibo de pago de declaración de renta (PDF)"},
    }


def manejar_accion(chat_id: str, accion: str) -> dict:
    """Procesa el clic en un botón (respuesta rápida) del asistente."""
    c = estado(chat_id)
    if c is None:
        return _respuesta(["La conversación expiró. Recarga la página e inténtalo de nuevo."])

    if c["estado"] == PROCESANDO:
        return _respuesta(["Estoy procesando tu solicitud, un momento por favor. ⏳"])

    # Acciones del flujo "no cliente"
    if c["estado"] == NO_CLIENTE:
        if accion == "reintentar":
            c["estado"] = ESPERA_CEDULA
            return _respuesta(["Perfecto. Escríbeme tu número de cédula. 📱"])
        if accion == "manual":
            return _respuesta(
                [
                    "El proceso manual está disponible en tu panel de ExoRenta: "
                    "ingresa con tu usuario y contraseña web y allí podrás "
                    "registrar las credenciales DIAN para consultar.",
                ],
                destino="/",
            )
        if accion == "registro":
            return _respuesta(
                [
                    "Puedes solicitar tu cuenta desde la página de acceso. El "
                    "administrador aprobará tu solicitud.",
                ],
                destino="/",
            )
        return _respuesta(["Elige una de las opciones, por favor."],
                          acciones={
                              "manual": {"texto": "🔑 Tengo credenciales"},
                              "reintentar": {"texto": "↩️ Reintentar"},
                          })

    if c["estado"] != ESPERA_OPCION or not c["cliente"]:
        return _respuesta(["Escríbeme tu número de cédula para empezar. 📱"])

    if accion not in (ACCION_RUT, ACCION_CONSULTA, ACCION_RECIBO):
        return _respuesta(
            ["Elige una de las opciones, por favor."],
            acciones=_opciones_cliente(),
        )

    c["opcion"] = accion
    cliente = auth.buscar_cliente(c["cedula"])
    if cliente is None:
        c["estado"] = NO_CLIENTE
        return _respuesta(["Ya no encuentro tu registro. Escríbeme tu cédula de nuevo."])
    try:
        contrasena = cifrado.descifrar(cliente["contrasena_cifrada"])
    except ValueError:
        c["estado"] = NO_CLIENTE
        return _respuesta([
            "No pude leer tu credencial guardada, probablemente por un cambio "
            "de configuración del servicio. Contacta al administrador.",
        ])

    if accion == ACCION_RECIBO:
        c["estado"] = ESPERA_ANIO
        return _respuesta(
            [
                "El recibo de pago de tu declaración de renta se descarga del "
                "portal DIAN en PDF (formulario 490).",
                "1) ¿En qué año gravable presentaste la declaración? (ej. 2025)",
            ],
        )

    c["estado"] = PROCESANDO
    etiqueta = "la copia del RUT (PDF)" if accion == ACCION_RUT else "la consulta ExoRenta"
    return _respuesta(
        [f"¡Perfecto! Iniciando {etiqueta} con tus datos... ⏳"],
        lanzar={
            "tipo": accion,
            "tipo_documento": c["cliente"]["tipo_documento"],
            "numero_documento": c["cedula"],
            "contrasena": contrasena,
        },
    )


def vincular_job(chat_id: str, job_id: str, contrasena: str) -> None:
    """Guarda el job lanzado en la conversación (para consultar progreso)."""
    c = estado(chat_id)
    if c is None:
        return
    # La contraseña solo tiene vida en el dict del job en memoria; aquí no se
    # guarda nada que permita reutilizarla después.
    c["job_id"] = job_id


def finalizar(chat_id: str, error: str | None = None) -> dict:
    """Se llama al terminar el job. Devuelve la respuesta con el estado final."""
    c = estado(chat_id)
    if c is None:
        return _respuesta(["La conversación expiró."])
    c["estado"] = ESPERA_CEDULA
    c["opcion"] = None
    c["anio"] = None
    c["fecha_pago"] = None
    if error:
        return _respuesta([
            "Lo siento, no pude completar el proceso. ⚠️",
            f"Detalle: {error}",
            "Escríbeme tu cédula de nuevo si quieres reintentarlo.",
        ])
    return _respuesta([
        "¡Listo! Tu archivo está preparado. ✅",
        "Presiona el botón de abajo para descargarlo (el enlace vence en 15 minutos).",
    ])