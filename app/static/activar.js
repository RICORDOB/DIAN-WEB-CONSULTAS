/* Activación de la cuenta DIAN desde un enlace personal.
   Lee el token de la URL (?t=…), valida el enlace y guía al cliente por los
   pasos: fecha de expedición (si falta) -> medio de contacto -> código OTP ->
   nueva contraseña -> éxito. Nunca guarda el token ni la contraseña. */
(function () {
  var token = new URLSearchParams(window.location.search).get("t") || "";
  var sesionId = null;
  var medios = [];
  var pasoActual = "";

  var RUTA_BASE = "/api/activar/" + encodeURIComponent(token);

  function el(id) { return document.getElementById(id); }

  function mostrar(paso) {
    pasoActual = paso;
    ["cargando", "error", "fecha", "medios", "codigo", "clave", "exito"].forEach(function (p) {
      var seccion = el("paso-" + p);
      if (seccion) seccion.classList.toggle("oculto", p !== paso);
    });
    var nivel = { fecha: 1, medios: 2, codigo: 3, clave: 4, exito: 4 }[paso] || 0;
    for (var i = 1; i <= 4; i++) {
      var ind = el("ind-" + i);
      if (ind) ind.classList.toggle("activo", i <= nivel);
    }
  }

  function mostrarError(id, texto) {
    var caja = el(id);
    if (!caja) return;
    if (!texto) { caja.classList.add("oculto"); caja.textContent = ""; return; }
    caja.textContent = texto;
    caja.classList.remove("oculto");
  }

  function pedir(url, cuerpo) {
    var opciones = { method: "POST", headers: { "Content-Type": "application/json" } };
    if (cuerpo) opciones.body = JSON.stringify(cuerpo);
    return fetch(url, opciones).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok) {
          var err = new Error(data.detail || "Ocurrió un error inesperado. Intenta de nuevo.");
          err.datos = data;
          err.status = r.status;
          throw err;
        }
        return data;
      });
    });
  }

  function sincronizar(data) {
    if (data.medios && data.medios.length) medios = data.medios;
    if (data.mensaje) el("texto-codigo").textContent = data.mensaje;
  }

  function volverAlPaso(data) {
    // El servidor puede retroceder de paso (p. ej. OTP rechazado en el paso final).
    var paso = data && data.paso;
    if (paso === "codigo") { mostrar("codigo"); return "error-codigo"; }
    if (paso === "menu" || paso === "medios") { mostrar("medios"); return "error-medios"; }
    if (paso === "clave") { mostrar("clave"); return "error-clave"; }
    return null;
  }

  function pintarMedios() {
    var cont = el("lista-medios");
    cont.innerHTML = "";
    medios.forEach(function (m, i) {
      var etiqueta = document.createElement("label");
      var radio = document.createElement("input");
      radio.type = "radio";
      radio.name = "medio";
      radio.value = m.tipo;
      if (i === 0) radio.checked = true;
      var span = document.createElement("span");
      span.textContent = m.etiqueta || m.tipo;
      etiqueta.appendChild(radio);
      etiqueta.appendChild(span);
      cont.appendChild(etiqueta);
    });
  }

  function medioElegido() {
    var radio = document.querySelector('input[name="medio"]:checked');
    return radio ? radio.value : "";
  }

  function mostrarMedios(data) {
    sincronizar(data);
    if (medios.length) pintarMedios();
    if (data.mensaje) el("texto-medios").textContent = data.mensaje;
    mostrar("medios");
  }

  function iniciar(fecha) {
    mostrarError("error-fecha", "");
    var cuerpo = fecha ? { fecha_expedicion: fecha } : {};
    pedir(RUTA_BASE + "/iniciar", cuerpo).then(function (data) {
      sesionId = data.sesion_id;
      mostrarMedios(data);
    }).catch(function (err) {
      if (err.status === 400) {
        // Fecha inválida o rechazada por la DIAN: reintentar con otra fecha.
        mostrar("fecha");
        mostrarError("error-fecha", err.message);
        return;
      }
      mostrar("error");
      el("error-enlace").textContent = err.message;
    });
  }

  function enviarCodigo() {
    var boton = el("boton-enviar");
    boton.disabled = true;
    mostrarError("error-medios", "");
    pedir(RUTA_BASE + "/paso", { sesion_id: sesionId, accion: "enviar_codigo", medio: medioElegido() })
      .then(function (data) {
        sincronizar(data);
        mostrarError("error-codigo", "");
        mostrar("codigo");
      })
      .catch(function (err) {
        sincronizar(err.datos || {});
        mostrarError("error-medios", err.message);
      })
      .finally(function () { boton.disabled = false; });
  }

  function reenviarCodigo() {
    var boton = el("boton-reenviar");
    boton.disabled = true;
    mostrarError("error-codigo", "");
    pedir(RUTA_BASE + "/paso", { sesion_id: sesionId, accion: "reenviar" })
      .then(function (data) {
        sincronizar(data);
        el("texto-codigo").textContent = data.mensaje || "Código reenviado.";
      })
      .catch(function (err) {
        sincronizar(err.datos || {});
        mostrarError("error-codigo", err.message);
      })
      .finally(function () { boton.disabled = false; });
  }

  function validarCodigo(codigo) {
    mostrarError("error-codigo", "");
    pedir(RUTA_BASE + "/paso", { sesion_id: sesionId, accion: "codigo", codigo: codigo })
      .then(function (data) {
        sincronizar(data);
        mostrar("clave");
        el("clave").focus();
      })
      .catch(function (err) {
        sincronizar(err.datos || {});
        var destino = volverAlPaso(err.datos) || "error-codigo";
        mostrarError(destino, err.message);
      });
  }

  function crearClave(contrasena, confirmacion) {
    mostrarError("error-clave", "");
    pedir(RUTA_BASE + "/paso", {
      sesion_id: sesionId, accion: "clave",
      contrasena: contrasena, confirmacion: confirmacion,
    }).then(function (data) {
      el("clave").value = "";
      el("clave2").value = "";
      if (data.mensaje) el("exito-texto").textContent = data.mensaje;
      if (data.aviso) mostrarError("aviso-guardado", data.aviso);
      mostrar("exito");
    }).catch(function (err) {
      sincronizar(err.datos || {});
      var destino = volverAlPaso(err.datos) || "error-clave";
      mostrarError(destino, err.message);
    });
  }

  function validarEnlace() {
    if (!token) {
      mostrar("error");
      el("error-enlace").textContent = "El enlace está incompleto. Ábrelo de nuevo desde el mensaje que recibiste.";
      return;
    }
    fetch(RUTA_BASE, { headers: { "Accept": "application/json" } })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (data) {
          if (!r.ok) {
            var err = new Error(data.detail || "El enlace no es válido.");
            err.status = r.status;
            throw err;
          }
          return data;
        });
      })
      .then(function (data) {
        el("documento").textContent = "Documento " + data.numero_documento + " · " + data.tipo_documento;
        if (data.ya_activada) {
          mostrar("error");
          el("error-enlace").textContent = data.mensaje ||
            "Esta cuenta ya está habilitada. Si olvidaste la contraseña, usa «Restablecer contraseña».";
          return;
        }
        if (data.falta_fecha) {
          mostrar("fecha");
          return;
        }
        iniciar("");
      })
      .catch(function (err) {
        mostrar("error");
        el("error-enlace").textContent = err.message;
      });
  }

  el("form-fecha").addEventListener("submit", function (e) {
    e.preventDefault();
    var fecha = el("fecha").value;
    if (!fecha) { mostrarError("error-fecha", "Selecciona la fecha de expedición."); return; }
    iniciar(fecha);
  });
  el("boton-enviar").addEventListener("click", enviarCodigo);
  el("boton-reenviar").addEventListener("click", reenviarCodigo);
  el("boton-volver").addEventListener("click", function () {
    mostrarError("error-codigo", "");
    mostrar("medios");
  });
  el("form-codigo").addEventListener("submit", function (e) {
    e.preventDefault();
    var codigo = el("codigo").value.trim();
    if (!codigo) { mostrarError("error-codigo", "Escribe el código recibido."); return; }
    validarCodigo(codigo);
  });
  el("form-clave").addEventListener("submit", function (e) {
    e.preventDefault();
    var contrasena = el("clave").value;
    var confirmacion = el("clave2").value;
    if (!el("acepto").checked) {
      mostrarError("error-clave", "Debes aceptar la política de tratamiento de datos para continuar.");
      return;
    }
    if (contrasena !== confirmacion) {
      mostrarError("error-clave", "Las contraseñas no coinciden.");
      return;
    }
    crearClave(contrasena, confirmacion);
  });

  mostrar("cargando");
  validarEnlace();
})();
