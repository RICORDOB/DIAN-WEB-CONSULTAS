#!/bin/bash
# Doble clic desde Finder: restablecimiento de la contraseña DIAN por API.
# No abre navegador ni pide captcha: consulta el documento, envía el código al
# medio elegido (correo/celular), valida y cambia la clave. La clave nueva se
# puede generar segura y se copia al portapapeles.
#
# Respaldo (si la API se endurece): herramientas/recuperar_dian.py
# (ventana de Chrome + captcha resuelto por una persona).
cd "$(dirname "$0")" || exit 1

echo "=== Restablecimiento de la contraseña DIAN (API, sin navegador) ==="
echo
read -r -p "Cédula / documento del cliente: " DOC
if [ -z "$DOC" ]; then
  echo "Sin documento no se puede continuar."
  read -r -p "Pulsa Enter para cerrar esta ventana..."
  exit 1
fi

.venv/bin/python herramientas/recuperar_dian_api.py "$DOC"
CODIGO=$?

echo
if [ "$CODIGO" -eq 0 ]; then
  echo "Proceso completado. Guarda la clave nueva en el panel si aún no está."
else
  echo "El proceso terminó con código $CODIGO (revisa el mensaje de arriba)."
fi
read -r -p "Pulsa Enter para cerrar esta ventana..."
