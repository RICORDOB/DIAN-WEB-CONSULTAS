# Imagen de producción (Render)
# IMPORTANTE: la etiqueta de la imagen base DEBE coincidir con la versión de
# `playwright` en requirements.txt (p. ej. imagen v1.49.1 <-> playwright==1.49.1).
# Si no coinciden, Playwright no encontrará el navegador en /ms-playwright.
FROM mcr.microsoft.com/playwright/python:v1.49.1-jammy AS build

WORKDIR /app

# Google Chrome real (anti-detección DIAN): el portal MUISCA solo habilita sus
# formularios con un Chrome sin banderas de automatización; mismo binario
# validado con evidencia. El Chromium de la imagen queda como respaldo.
RUN apt-get update \
 && apt-get install -y --no-install-recommends wget ca-certificates \
 && wget -q -O /tmp/google-chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
 && apt-get install -y --no-install-recommends /tmp/google-chrome.deb \
 && rm -f /tmp/google-chrome.deb \
 && rm -rf /var/lib/apt/lists/*

# Copiamos primero requirements para cachear la capa de instalación.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiamos el código de la app
COPY app ./app

# Usuario no root (seguridad): contenido ya instalado.
ENV APP_DATA_DIR=/data \
    APP_JOBS_DIR=/data/jobs

RUN mkdir -p /data && chown -R pwuser:pwuser /data /app
USER pwuser

# Puerto requerido por Render (variable PORT la inyecta la plataforma)
EXPOSE 8000
ENV PORT=8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
