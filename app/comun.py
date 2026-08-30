"""
comun — Constantes y configuración compartidas entre el script de consola
(dian_login.py) y el servicio web (app/runner.py).

Una sola fuente de verdad para selectores, tiempos, año gravable, UVT, topes
legales y calendario de vencimientos de renta.
"""

from __future__ import annotations

from datetime import date

# Portal transaccional DIAN
URL_LOGIN = "https://muisca.dian.gov.co/WebArquitectura/DefLogin.faces"

# Tiempos y reintentos
TIMEOUT_LOGIN = 15_000
TIMEOUT_DESCARGA = 30_000
REINTENTOS = 2

# Año gravable a consultar en "Consultar información Exógena"
ANIO_EXOGENO = "2025"

# Determinación de obligación de declarar renta (AG 2025, se presenta en 2026).
# Se usa la UVT 2025 (no la de 2026, que solo aplica a sanciones).
UVT_2025 = 49_799
RESOLUCION_UVT = "Res. DIAN 000193 de 2024"
NORMA_TOPES = "Art. 592 E.T. y Dec. 1625-2016 (art. 1.6.1.13.2.7)"

# Aviso legal sobre el alcance de la Información Exógena (se muestra en el
# panel y se anexa a las razones del libro Excel).
AVISO_INFO_EXOGENA = (
    "IMPORTANTE: Para cumplir con su obligación de declarar, la Información "
    "Exógena Tributaria NO ES INDISPENSABLE y NO REEMPLAZA la información de "
    "su realidad económica, ni lo exonera de declarar los valores totales que "
    "correspondan y que son de su conocimiento exclusivo."
)

# Texto exacto del error "sin datos" de facturación electrónica en la DIAN
TEXTO_SIN_DATOS_FE = "No se encontró información para el año seleccionado"

# Calendario DIAN 2026 — Vencimiento declaración renta personas naturales AG 2025
# Res. DIAN 000238 de 2025. Fechas según los dos últimos dígitos del NIT.
CALENDARIO_RENTA_2026 = {
    (1, 2): date(2026, 8, 12),   (3, 4): date(2026, 8, 13),
    (5, 6): date(2026, 8, 14),   (7, 8): date(2026, 8, 18),
    (9, 10): date(2026, 8, 19),  (11, 12): date(2026, 8, 20),
    (13, 14): date(2026, 8, 21), (15, 16): date(2026, 8, 24),
    (17, 18): date(2026, 8, 25), (19, 20): date(2026, 8, 26),
    (21, 22): date(2026, 8, 27), (23, 24): date(2026, 8, 28),
    (25, 26): date(2026, 8, 31), (27, 28): date(2026, 9, 1),
    (29, 30): date(2026, 9, 2),  (31, 32): date(2026, 9, 3),
    (33, 34): date(2026, 9, 4),  (35, 36): date(2026, 9, 7),
    (37, 38): date(2026, 9, 8),  (39, 40): date(2026, 9, 9),
    (41, 42): date(2026, 9, 10), (43, 44): date(2026, 9, 11),
    (45, 46): date(2026, 9, 14), (47, 48): date(2026, 9, 15),
    (49, 50): date(2026, 9, 16), (51, 52): date(2026, 9, 17),
    (53, 54): date(2026, 9, 18), (55, 56): date(2026, 9, 21),
    (57, 58): date(2026, 9, 22), (59, 60): date(2026, 9, 23),
    (61, 62): date(2026, 9, 24), (63, 64): date(2026, 9, 25),
    (65, 66): date(2026, 9, 28), (67, 68): date(2026, 10, 1),
    (69, 70): date(2026, 10, 2), (71, 72): date(2026, 10, 5),
    (73, 74): date(2026, 10, 6), (75, 76): date(2026, 10, 7),
    (77, 78): date(2026, 10, 8), (79, 80): date(2026, 10, 9),
    (81, 82): date(2026, 10, 13), (83, 84): date(2026, 10, 14),
    (85, 86): date(2026, 10, 15), (87, 88): date(2026, 10, 16),
    (89, 90): date(2026, 10, 19), (91, 92): date(2026, 10, 20),
    (93, 94): date(2026, 10, 21), (95, 96): date(2026, 10, 22),
    (97, 98): date(2026, 10, 23), (99, 0): date(2026, 10, 26),
}

# Cada tope: (categoría en el reporte, UVT, descripción legal, operador)
TOPES = [
    ("Ingresos",   1_400, "Ingresos brutos",                  ">="),
    ("Patrimonio", 4_500, "Patrimonio bruto",                 ">"),
    ("Consumo TC", 1_400, "Consumos con tarjeta de crédito",  ">="),
    ("Movimiento", 1_400, "Consignaciones bancarias",         ">="),
    ("Compras",    1_400, "Compras y consumos totales",       ">="),
]