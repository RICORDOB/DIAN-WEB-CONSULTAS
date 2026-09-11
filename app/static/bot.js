/* Asistente de chat web (bot ExoRenta).
   Widget flotante: el cliente escribe su cédula, el bot valida contra el
   catálogo cifrado de clientes y ofrece copia del RUT o Consulta ExoRenta.
   Navega por respuestas JSON de /api/bot/mensaje y /api/bot/accion. */
(function () {
  if (window.__exorentaBotNucleo) return;
  window.__exorentaBotNucleo = true;

  var URL_MENSAJE = "/api/bot/mensaje";
  var URL_ACCION = "/api/bot/accion";
  var COOKIE = "bot";

  function construirDom() {
    var abrir = document.createElement("button");
    abrir.className = "bot-abrir";
    abrir.type = "button";
    abrir.title = "Asistente ExoRenta";
    abrir.textContent = "💬";

    var caja = document.createElement("div");
    caja.className = "bot-caja";
    caja.innerHTML =
      '<div class="bot-cabecera"><span>Asistente ExoRenta</span>' +
      '<button type="button" class="bot-cerrar" aria-label="Cerrar">&times;</button></div>' +
      '<div class="bot-mensajes"></div>' +
      '<div class="bot-entrada"><input type="text" autocomplete="off" ' +
      'placeholder="Escribe tu mensaje..." />' +
      '<button type="button">Enviar</button></div>';

    document.body.appendChild(abrir);
    document.body.appendChild(caja);
    return { abrir: abrir, caja: caja };
  }

  var dom = construirDom();
  var mensajes = dom.caja.querySelector(".bot-mensajes");
  var input = dom.caja.querySelector("input");
  var enviar = dom.caja.querySelector("button.bot-entrada button");
  var cerrado = dom.caja.querySelector(".bot-cerrar");
  var ocupado = false;

  function guardarCookie(valor) {
    try {
      document.cookie = COOKIE + "=" + encodeURIComponent(valor) +
        ";path=/;max-age=43200;SameSite=Lax";
    } catch (e) { /* sin cookies: el chat reinicia */ }
  }

  function contenido(msj) {
    var burbuja = document.createElement("div");
    burbuja.className = "bot-burbuja bot-bot";
    burbuja.textContent = msj;
    mensajes.appendChild(burbuja);
    mensajes.scrollTop = mensajes.scrollHeight;
    return burbuja;
  }

  function burbujaUsuario(msj) {
    var b = document.createElement("div");
    b.className = "bot-burbuja bot-usuario";
    b.textContent = msj;
    mensajes.appendChild(b);
    mensajes.scrollTop = mensajes.scrollHeight;
  }

  function mostrarEscribiendo() {
    var s = document.createElement("div");
    s.className = "bot-escribiendo";
    s.textContent = "…";
    mensajes.appendChild(s);
    mensajes.scrollTop = mensajes.scrollHeight;
    return s;
  }

  function accionesHtml(acciones) {
    var cont = document.createElement("div");
    cont.className = "bot-acciones";
    Object.keys(acciones).forEach(function (clave) {
      var a = acciones[clave];
      var b = document.createElement("button");
      b.type = "button";
      b.className = "bot-boton-accion";
      b.textContent = a.texto || clave;
      b.addEventListener("click", function () {
        b.disabled = true;
        enviarAccion(clave);
      });
      cont.appendChild(b);
    });
    mensajes.appendChild(cont);
    mensajes.scrollTop = mensajes.scrollHeight;
  }

  function esDescarga(url) {
    return url && url.indexOf("/api/bot/descargar/") === 0;
  }

  function manejarRespuesta(resp) {
    if (!resp) return;
    if (resp.cookie) guardarCookie(resp.cookie);
    if (resp.mensajes && resp.mensajes.length) {
      resp.mensajes.forEach(function (m) {
        var burbuja = contenido(m);
        if (resp.descarga) {
          var enlace = document.createElement("a");
          enlace.className = "bot-descarga";
          enlace.href = resp.descarga;
          enlace.textContent = "⬇️ DESCARGAR ARCHIVO";
          enlace.setAttribute("download", "");
          burbuja.appendChild(document.createElement("br"));
          burbuja.appendChild(enlace);
        }
      });
    }
    if (resp.acciones && Object.keys(resp.acciones).length) {
      accionesHtml(resp.acciones);
    }
    if (resp.destino === "/") {
      setTimeout(function () { window.location.href = "/"; }, 1800);
    }
    if (resp.job_id) {
      seguirJob(resp.job_id);
    }
  }

  function peticion(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(body),
    }).then(function (r) { return r.json(); });
  }

  function enviarMensaje(texto) {
    if (ocupado || !texto.trim()) return;
    burbujaUsuario(texto.trim());
    input.value = "";
    ocupado = true;
    var espera = mostrarEscribiendo();
    var cont = document.createElement("div");
    cont.className = "bot-acciones";
    mensajes.appendChild(cont);
    peticion(URL_MENSAJE, { mensaje: texto.trim() })
      .then(function (resp) {
        if (espera && espera.parentNode) espera.parentNode.removeChild(espera);
        manejarRespuesta(resp);
        ocupado = false;
      })
      .catch(function () {
        if (espera && espera.parentNode) espera.parentNode.removeChild(espera);
        contenido("Ocurrió un error de conexión. Intenta de nuevo en un momento.");
        ocupado = false;
      });
  }

  function enviarAccion(clave) {
    if (ocupado) return;
    ocupado = true;
    var espera = mostrarEscribiendo();
    peticion(URL_ACCION, { accion: clave })
      .then(function (resp) {
        if (espera && espera.parentNode) espera.parentNode.removeChild(espera);
        manejarRespuesta(resp);
        ocupado = false;
      })
      .catch(function () {
        if (espera && espera.parentNode) espera.parentNode.removeChild(espera);
        contenido("Ocurrió un error de conexión. Intenta de nuevo en un momento.");
        ocupado = false;
      });
  }

  function seguirJob(jobId) {
    var espera = mostrarEscribiendo();
    var iter = setInterval(function () {
      fetch("/api/bot/job/" + jobId, { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.estado === "done") {
            clearInterval(iter);
            if (espera && espera.parentNode) espera.parentNode.removeChild(espera);
            if (data.descarga) manejarRespuesta({ mensajes: ["¡Listo! Tu archivo está preparado. ✅"], descarga: data.descarga });
            ocupado = false;
          } else if (data.estado === "error") {
            clearInterval(iter);
            if (espera && espera.parentNode) espera.parentNode.removeChild(espera);
            manejarRespuesta({ mensajes: ["Lo siento, no pude completar el proceso. ⚠️"] });
            ocupado = false;
          } else if (data.progreso && data.progreso.length) {
            espera.textContent = data.progreso[data.progreso.length - 1].slice(0, 60) + "…";
            mensajes.scrollTop = mensajes.scrollHeight;
          }
        })
        .catch(function () { /* reintenta en el próximo tick */ });
    }, 1800);
  }

  function abrirChat() {
    if (!dom.caja.classList.contains("abierta")) {
      dom.caja.classList.add("abierta");
      dom.abrir.style.display = "none";
      if (!mensajes.children.length) {
        enviarMensaje("hola");
      }
      input.focus();
    }
  }

  function cerrarChat() {
    dom.caja.classList.remove("abierta");
    dom.abrir.style.display = "flex";
  }

  dom.abrir.addEventListener("click", abrirChat);
  cerrado.addEventListener("click", cerrarChat);
  enviar.addEventListener("click", function () { enviarMensaje(input.value); });
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter") { e.preventDefault(); enviarMensaje(input.value); }
  });

  // Primer contacto automático al abrir la página (burbujón de bienvenida).
  window.addEventListener("load", function () {
    setTimeout(function () {
      if (!mensajes.children.length) enviarMensaje("hola");
    }, 1200);
  });
})();