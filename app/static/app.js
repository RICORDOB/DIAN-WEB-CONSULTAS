/* JS compartido de la app web DIAN */

function escapar(texto) {
  const div = document.createElement("div");
  div.textContent = texto;
  return div.innerHTML;
}

function mostrarMensaje(el, texto, tipo) {
  el.textContent = texto;
  el.className = "mensaje " + tipo;
  el.classList.remove("oculto");
}

function peticion(url, opciones = {}) {
  const headers = opciones.body ? { "Content-Type": "application/json" } : {};
  return fetch(url, { ...opciones, headers, credentials: "same-origin" });
}

function extraerDetalle(data) {
  if (data == null) return "Ocurrió un error.";
  if (typeof data === "string") return data;
  if (typeof data.detail === "string") return data.detail;
  if (Array.isArray(data.detail)) {
    // Errores de validación de FastAPI: cada item tiene "loc" y "msg"
    return data.detail.map(function (e) {
      const campo = Array.isArray(e.loc) ? e.loc.join(".") : String(e.loc);
      return (campo ? campo + ": " : "") + (e.msg || "error inesperado");
    }).join(" | ");
  }
  if (data.detail && data.detail.message) return data.detail.message;
  return JSON.stringify(data);
}

function manejarError(resp, mensajeEl) {
  return resp.json().then((data) => {
    mostrarMensaje(mensajeEl, extraerDetalle(data), "error");
  }).catch(() => {
    mostrarMensaje(mensajeEl, "Ocurrió un error.", "error");
  });
}

/* ------------------------------------------------------------------ */
/* Pantalla de login / registro (index.html)                           */
/* ------------------------------------------------------------------ */
function iniciarLogin() {
  const loginView = document.getElementById("loginView");
  const registroView = document.getElementById("registroView");

  peticion("/api/me").then((r) => r.json()).then((data) => {
    if (data.autenticado) {
      window.location.href = data.rol === "admin" ? "/dev" : "/panel";
    }
  });

  document.getElementById("btnRegistro").addEventListener("click", (e) => {
    e.preventDefault();
    loginView.classList.add("oculto");
    registroView.classList.remove("oculto");
  });
  document.getElementById("btnVolverLogin").addEventListener("click", (e) => {
    e.preventDefault();
    registroView.classList.add("oculto");
    loginView.classList.remove("oculto");
  });

  document.getElementById("loginForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const mensaje = document.getElementById("mensaje");
    const usuario = document.getElementById("usuario").value.trim();
    const password = document.getElementById("password").value;
    peticion("/api/login", {
      method: "POST",
      body: JSON.stringify({ usuario, password }),
    }).then((resp) => {
      if (resp.ok) {
        return resp.json().then((data) => {
          window.location.href = data.rol === "admin" ? "/dev" : "/panel";
        });
      }
      return manejarError(resp, mensaje);
    });
  });

  document.getElementById("registroForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const mensaje = document.getElementById("mensajeRegistro");
    const usuario = document.getElementById("nuevoUsuario").value.trim();
    const password = document.getElementById("nuevaPassword").value;
    peticion("/api/registro", {
      method: "POST",
      body: JSON.stringify({ usuario, password }),
    }).then((resp) => {
      if (resp.ok) {
        mostrarMensaje(mensaje, "Solicitud de alta creada. Espera la aprobación del administrador.", "ok");
        document.getElementById("registroForm").reset();
      } else {
        manejarError(resp, mensaje);
      }
    });
  });
}

/* ------------------------------------------------------------------ */
/* Utilidades de sesión usadas por panel y dev                         */
/* ------------------------------------------------------------------ */
function configurarSesion() {
  const usuarioSpan = document.getElementById("usuarioActual");
  const btnSalir = document.getElementById("btnSalir");
  const linkDev = document.getElementById("linkDev");

  peticion("/api/me").then((r) => r.json()).then((data) => {
    if (!data.autenticado) { window.location.href = "/"; return; }
    usuarioSpan.textContent = data.usuario;
    if (linkDev && data.rol === "admin") linkDev.classList.remove("oculto");
  });

  if (btnSalir) {
    btnSalir.addEventListener("click", () => {
      peticion("/api/logout", { method: "POST" }).then(() => {
        window.location.href = "/";
      });
    });
  }
}

/* ------------------------------------------------------------------ */
/* Panel de consulta (panel.html)                                      */
/* ------------------------------------------------------------------ */
function iniciarPanel() {
  configurarSesion();
  const form = document.getElementById("consultaForm");
  const mensaje = document.getElementById("mensaje");
  const progresoBox = document.getElementById("progresoBox");
  const logProgreso = document.getElementById("logProgreso");
  const estadoJob = document.getElementById("estadoJob");
  const descargaBox = document.getElementById("descargaBox");
  const linkDescargar = document.getElementById("linkDescargar");
  const btnConsultar = document.getElementById("btnConsultar");

  let jobId = null;
  let pollTimer = null;

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (jobId) { mostrarMensaje(mensaje, "Ya hay una consulta en curso.", "info"); return; }

    const tipoDocumento = document.getElementById("tipoDocumento").value;
    const numeroDocumento = document.getElementById("numeroDocumento").value.trim();
    const contrasena = document.getElementById("contrasena").value;

    progresoBox.classList.remove("oculto");
    descargaBox.classList.add("oculto");
    logProgreso.textContent = "";
    estadoJob.textContent = "Iniciando...";
    btnConsultar.disabled = true;

    peticion("/api/consulta", {
      method: "POST",
      body: JSON.stringify({
        tipo_documento: tipoDocumento,
        numero_documento: numeroDocumento,
        contrasena: contrasena,
      }),
    }).then((resp) => {
      if (!resp.ok) {
        btnConsultar.disabled = false;
        return manejarError(resp, mensaje);
      }
      return resp.json().then((data) => {
        jobId = data.job_id;
        pollTimer = setInterval(consultarProgreso, 1500);
      });
    });
  });

  function consultarProgreso() {
    if (!jobId) return;
    peticion("/api/job/" + jobId).then((resp) => {
      if (!resp.ok) { clearInterval(pollTimer); btnConsultar.disabled = false; return; }
      return resp.json().then((data) => {
        logProgreso.textContent = data.progreso.join("\n");
        logProgreso.scrollTop = logProgreso.scrollHeight;

        if (data.estado === "done") {
          clearInterval(pollTimer);
          estadoJob.textContent = "Completado. Descarga disponible.";
          finalizarJob(data);
        } else if (data.estado === "error") {
          clearInterval(pollTimer);
          estadoJob.textContent = "Error en el proceso.";
          mostrarMensaje(mensaje, data.error || "Falló la consulta.", "error");
          btnConsultar.disabled = false;
        } else if (data.estado === "running") {
          estadoJob.textContent = "Procesando...";
        } else {
          estadoJob.textContent = "En cola...";
        }
      });
    });
  }

  function finalizarJob(data) {
    descargaBox.classList.remove("oculto");
    linkDescargar.href = "/api/job/" + jobId + "/descargar";
    btnConsultar.disabled = false;
    mostrarMensaje(mensaje, "Consulta completada.", "ok");
  }
}

/* ------------------------------------------------------------------ */
/* Panel desarrollador (dev.html)                                      */
/* ------------------------------------------------------------------ */
function iniciarDev() {
  configurarSesion();
  const mensaje = document.getElementById("mensaje");
  const tbody = document.querySelector("#tablaUsuarios tbody");

  function cargar() {
    peticion("/api/admin/pendientes").then((resp) => {
      if (!resp.ok) {
        if (resp.status === 403) { window.location.href = "/panel"; }
        return;
      }
      return resp.json().then((data) => {
        tbody.innerHTML = "";
        data.pendientes.forEach((u) => {
          const fila = document.createElement("tr");
          fila.innerHTML =
            "<td>" + escapar(u.usuario) + "</td>" +
            "<td>" + escapar(u.rol) + "</td>" +
            "<td>" + escapar(u.estado) + "</td>" +
            "<td>" + escapar(u.creado_en) + "</td>" +
            "<td></td>";

          const acciones = fila.lastElementChild;
          if (u.estado === "pendiente") {
            const btnOk = document.createElement("button");
            btnOk.textContent = "Aprobar";
            btnOk.className = "boton boton-mini ok";
            btnOk.addEventListener("click", () => decidir(u.usuario, true));
            const btnNo = document.createElement("button");
            btnNo.textContent = "Rechazar";
            btnNo.className = "boton boton-mini peligro";
            btnNo.addEventListener("click", () => decidir(u.usuario, false));
            acciones.append(btnOk, " ", btnNo);
          }
          tbody.appendChild(fila);
        });
      });
    });
  }

  function decidir(usuario, aprobar) {
    peticion("/api/admin/decidir", {
      method: "POST",
      body: JSON.stringify({ usuario, aprobar }),
    }).then((resp) => {
      if (resp.ok) {
        mostrarMensaje(mensaje, aprobar ? "Alta aprobada." : "Solicitud rechazada.", "ok");
        cargar();
      } else {
        manejarError(resp, mensaje);
      }
    });
  }

  cargar();
}

/* ------------------------------------------------------------------ */
/* Dispatcher por página                                               */
/* ------------------------------------------------------------------ */
document.addEventListener("DOMContentLoaded", () => {
  const path = window.location.pathname;
  if (path === "/" || path === "" || path === "/index.html") {
    iniciarLogin();
  } else if (path === "/panel") {
    iniciarPanel();
  } else if (path === "/dev") {
    iniciarDev();
  }
});