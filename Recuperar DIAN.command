#!/bin/bash
# Doble clic desde Finder: restablecimiento asistido de la contraseña DIAN.
# Abre una ventana de Chrome; resuelve ahí el captcha cuando aparezca y el
# proceso continúa solo (código al correo/celular del cliente + clave nueva).
cd "$(dirname "$0")" || exit 1

echo "=== Restablecimiento asistido de la contraseña DIAN ==="
echo
read -r -p "Cédula / documento del cliente: " DOC
if [ -z "$DOC" ]; then
  echo "Sin documento no se puede continuar."
  read -r -p "Pulsa Enter para cerrar esta ventana..."
  exit 1
fi

.venv/bin/python herramientas/recuperar_dian.py "$DOC"
CODIGO=$?

echo
if [ "$CODIGO" -eq 0 ]; then
  echo "Proceso completado."
else
  echo "El proceso terminó con código $CODIGO (revisa el mensaje de arriba)."
fi
read -r -p "Pulsa Enter para cerrar esta ventana..."
