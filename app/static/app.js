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
  registrarServiceWorker();
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
  registrarServiceWorker();
  configurarPush(document.getElementById("btnPush"), document.getElementById("pushEstado"));
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
let _chartDias = null;
let _chartEstados = null;
let _filtrosConsultas = { estado: "", usuario: "" };

const ETIQUETA_ESTADO = {
  pendiente: "Pendiente",
  aprobado: "Aprobado",
  rechazado: "Rechazado",
  bloqueado: "Bloqueado",
  queued: "En cola",
  running: "En curso",
  done: "Exitoso",
  error: "Error",
};

function iniciarDev() {
  configurarSesion();
  registrarServiceWorker();
  const mensaje = document.getElementById("mensaje");
  const btnTabU = document.getElementById("tabUsuarios");
  const btnTabD = document.getElementById("tabDashboard");
  const vistaU = document.getElementById("vistaUsuarios");
  const vistaD = document.getElementById("vistaDashboard");

  function activarTab(vista) {
    btnTabU.classList.toggle("activo", vista === "usuarios");
    btnTabD.classList.toggle("activo", vista === "dashboard");
    vistaU.classList.toggle("oculto", vista !== "usuarios");
    vistaD.classList.toggle("oculto", vista !== "dashboard");
    if (vista === "dashboard") cargarDashboard();
  }
  btnTabU.addEventListener("click", () => activarTab("usuarios"));
  btnTabD.addEventListener("click", () => activarTab("dashboard"));
  activarTab("usuarios");

  /* -------- Tab Usuarios -------- */
  const tbody = document.querySelector("#tablaUsuarios tbody");

  function cargar() {
    peticion("/api/admin/pendientes").then((resp) => {
      if (!resp.ok) {
        if (resp.status === 403) { window.location.href = "/panel"; }
        return manejarError(resp, mensaje);
      }
      return resp.json().then((data) => {
        tbody.innerHTML = "";
        data.pendientes.forEach((u) => {
          const fila = document.createElement("tr");
          fila.innerHTML =
            "<td>" + escapar(u.usuario) + "</td>" +
            "<td>" + escapar(u.rol) + "</td>" +
            '<td><span class="etiqueta etiqueta-' + u.estado + '">' +
              (ETIQUETA_ESTADO[u.estado] || escapar(u.estado)) + "</span></td>" +
            "<td>" + escapar(u.creado_en) + "</td>" +
            "<td></td>";

          const acciones = fila.lastElementChild;
          if (u.rol !== "admin") {
            if (u.estado === "pendiente") {
              acciones.appendChild(btnAccion("Aprobar", "ok", () => decidir(u.usuario, true)));
              acciones.appendChild(btnAccion("Rechazar", "peligro", () => decidir(u.usuario, false)));
            } else if (u.estado === "aprobado") {
              acciones.appendChild(btnAccion("Bloquear", "peligro", () => bloquear(u.usuario, true)));
            } else if (u.estado === "bloqueado") {
              acciones.appendChild(btnAccion("Desbloquear", "ok", () => bloquear(u.usuario, false)));
            } else if (u.estado === "rechazado") {
              acciones.appendChild(btnAccion("Rehabilitar", "ok", () => decidir(u.usuario, true)));
            }
            acciones.appendChild(btnAccion("Eliminar", "peligro", () => eliminar(u.usuario)));
          }
          tbody.appendChild(fila);
        });
      });
    });
  }

  function btnAccion(texto, clase, onClic) {
    const b = document.createElement("button");
    b.textContent = texto;
    b.className = "boton boton-mini " + clase;
    b.addEventListener("click", onClic);
    return b;
  }

  function decidir(usuario, aprobar) {
    peticion("/api/admin/decidir", {
      method: "POST",
      body: JSON.stringify({ usuario, aprobar }),
    }).then((resp) => {
      if (resp.ok) {
        mostrarMensaje(mensaje, aprobar ? "Alta aprobada." : "Solicitud rechazada.", "ok");
        cargar();
      } else { manejarError(resp, mensaje); }
    });
  }

  function bloquear(usuario, bloquear) {
    peticion("/api/admin/bloquear", {
      method: "POST",
      body: JSON.stringify({ usuario, bloquear }),
    }).then((resp) => {
      if (resp.ok) {
        mostrarMensaje(mensaje,
          bloquear ? "Acceso bloqueado. La sesión del usuario se derribó." : "Acceso restablecido.",
          "ok");
        cargar();
      } else { manejarError(resp, mensaje); }
    });
  }

  function eliminar(usuario) {
    if (!window.confirm(
      "¿Eliminar al usuario '" + usuario + "'?\n\n" +
      "Se borrará su cuenta y su historial de consultas. Esta acción no se puede deshacer."
    )) return;
    peticion("/api/admin/eliminar", {
      method: "POST",
      body: JSON.stringify({ usuario }),
    }).then((resp) => {
      if (resp.ok) {
        mostrarMensaje(mensaje, "Usuario eliminado.", "ok");
        cargar();
      } else { manejarError(resp, mensaje); }
    });
  }

  cargar();

  /* -------- Tab Dashboard -------- */
  function cargarDashboard() {
    cagarKPIs();
    cargarConsultas();
  }

  function cagarKPIs() {
    peticion("/api/admin/estadisticas").then((resp) => {
      if (!resp.ok) { return manejarError(resp, mensaje); }
      return resp.json().then((data) => {
        renderizarKPIsUsuarios(data.usuarios);
        renderizarKPIsConsultas(data.consultas);
        dibujarGraficas(data);
        llenarFiltroUsuarios(data.por_usuario);
      });
    });
  }

  function renderizarKPIsUsuarios(u) {
    document.getElementById("kpisUsuarios").innerHTML =
      kpi("Usuarios", u.total, "azul") +
      kpi("Aprobados", u.aprobados, "ok") +
      kpi("Pendientes", u.pendientes, "info") +
      kpi("Bloqueados", u.bloqueados, "peligro") +
      kpi("Rechazados", u.rechazados, "gris");
  }

  function renderizarKPIsConsultas(c) {
    const tasa = c.total ? Math.round((c.done / c.total) * 100) + "%" : "—";
    document.getElementById("kpisConsultas").innerHTML =
      kpi("Consultas totales", c.total, "azul") +
      kpi("Exitosas", c.done, "ok", "done") +
      kpi("Errores", c.error, "peligro", "error") +
      kpi("En curso", c.running + c.queued, "info", "running") +
      kpi("Tasa de éxito", tasa, "gris");
  }

  function kpi(titulo, valor, tipo, filtro) {
    const clase = filtro ? " kpi-filtrable kpi-" + filtro : "";
    return '<div class="kpi-card' + clase + '" data-filtro="' + (filtro || "") + '">' +
      '<span class="kpi-titulo">' + titulo + '</span>' +
      '<span class="kpi-valor kpi-' + tipo + '">' + escapar(String(valor)) + "</span></div>";
  }

  function dibujarGraficas(data) {
    const dias = data.por_dia || [];
    if (window.Chart) {
      const labels = dias.map((d) => d.fecha.slice(5));
      if (_chartDias) _chartDias.destroy();
      _chartDias = new Chart(document.getElementById("chartDias"), {
        type: "bar",
        data: {
          labels: labels,
          datasets: [
            { label: "Exitosas", data: dias.map((d) => d.done), backgroundColor: "#2e7d32" },
            { label: "Errores", data: dias.map((d) => d.error), backgroundColor: "#c62828" },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true } },
        },
      });

      if (_chartEstados) _chartEstados.destroy();
      const c = data.consultas;
      _chartEstados = new Chart(document.getElementById("chartEstados"), {
        type: "doughnut",
        data: {
          labels: ["Exitosas", "Errores", "En curso"],
          datasets: [{
            data: [c.done, c.error, c.running + c.queued],
            backgroundColor: ["#2e7d32", "#c62828", "#0d47a1"],
          }],
        },
        options: { responsive: true, maintainAspectRatio: false },
      });
    }
  }

  function llenarFiltroUsuarios(porUsuario) {
    const sel = document.getElementById("filtroUsuario");
    const actual = sel.value;
    sel.innerHTML = '<option value="">Usuario: todos</option>' +
      (porUsuario || []).map(function (p) {
        return '<option value="' + escapar(p.usuario) + '">' + escapar(p.usuario) + " (" + p.total + ")</option>";
      }).join("");
    sel.value = actual;
  }

  function cargarConsultas() {
    const params = new URLSearchParams();
    if (_filtrosConsultas.estado) params.set("estado", _filtrosConsultas.estado);
    if (_filtrosConsultas.usuario) params.set("usuario", _filtrosConsultas.usuario);
    peticion("/api/admin/consultas?" + params.toString()).then((resp) => {
      if (!resp.ok) { return manejarError(resp, mensaje); }
      return resp.json().then((data) => {
        renderizarConsultas(data.consultas || []);
      });
    });
  }

  function renderizarConsultas(lista) {
    const tb = document.querySelector("#tablaConsultas tbody");
    if (!lista.length) {
      tb.innerHTML = '<tr><td colspan="5" class="sin-datos">Sin consultas registradas.</td></tr>';
      return;
    }
    tb.innerHTML = lista.map((c) =>
      "<tr>" +
      "<td>" + escapar(c.usuario) + "</td>" +
      "<td>" + escapar(c.tipo_documento || "—") + "</td>" +
      "<td>" + escapar(c.fecha_creacion || "—") + "</td>" +
      '<td><span class="etiqueta etiqueta-' + c.estado + '">' +
        (ETIQUETA_ESTADO[c.estado] || escapar(c.estado)) + "</span></td>" +
      "<td>" + escapar(c.estado === "done" ? (c.resultado || "listo") : (c.error || "—")) + "</td>" +
      "</tr>"
    ).join("");
  }

  /* -------- Interacción del dashboard -------- */
  document.getElementById("kpisConsultas").addEventListener("click", (e) => {
    const card = e.target.closest(".kpi-filtrable");
    if (!card) return;
    const filtro = card.dataset.filtro;
    _filtrosConsultas.estado = (_filtrosConsultas.estado === filtro) ? "" : filtro;
    document.getElementById("filtroEstado").value = _filtrosConsultas.estado;
    cargarConsultas();
  });

  document.getElementById("filtroEstado").addEventListener("change", (e) => {
    _filtrosConsultas.estado = e.target.value;
    cargarConsultas();
  });

  document.getElementById("filtroUsuario").addEventListener("change", (e) => {
    _filtrosConsultas.usuario = e.target.value;
    cargarConsultas();
  });

  document.getElementById("btnLimpiarFiltros").addEventListener("click", () => {
    _filtrosConsultas = { estado: "", usuario: "" };
    document.getElementById("filtroEstado").value = "";
    document.getElementById("filtroUsuario").value = "";
    cargarConsultas();
    cagarKPIs();
  });

  setInterval(() => {
    if (!vistaD.classList.contains("oculto")) cargarDashboard();
  }, 10000);
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