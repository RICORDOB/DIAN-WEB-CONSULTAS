# Build stage
# IMPORTANTE: la etiqueta de la imagen base DEBE coincidir con la versión de
# `playwright` en requirements.txt (p. ej. imagen v1.49.1 <-> playwright==1.49.1).
# Si no coinciden, Playwright no encontrará el navegador en /ms-playwright.
FROM mcr.microsoft.com/playwright/python:v1.49.1-jammy AS build

WORKDIR /app

# Dependencias del sistema para Chromium (ya incluidas por la imagen Playwright).
# Copiamos primero requirements para cachear la capa de instalación.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiamos el código de la app
COPY app ./app

# Los layouts/ojo: la imagen Playwright ya tiene los navegadores en /ms-playwright.
# Creamos un symlink genérico de Chrome por si alguna dependencia lo busca por PATH.
RUN find /ms-playwright -type f \( -name chrome -o -name chrome-headless-shell \) -executable 2>/dev/null | head -1 | xargs -I{} ln -sf {} /usr/local/bin/chrome || true

# Usuario no root (seguridad): contenido ya instalado.
ENV APP_DATA_DIR=/data \
    APP_JOBS_DIR=/data/jobs

RUN mkdir -p /data && chown -R pwuser:pwuser /data /app
USER pwuser

# Puerto requerido por Render (variable PORT la inyecta la plataforma)
EXPOSE 8000
ENV PORT=8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]