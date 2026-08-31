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

function registrarServiceWorker() {
  if (!("serviceWorker" in navigator)) return;
  navigator.serviceWorker
    .register("/sw.js", { scope: "/", updateViaCache: "none" })
    .then((reg) => reg.update())
    .catch(() => {});
}

function _claveVapidUint8(clave) {
  const b64 = clave.replace(/-/g, "+").replace(/_/g, "/");
  const base = b64 + "=".repeat((4 - (b64.length % 4)) % 4);
  const bin = window.atob(base);
  const ui8 = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) ui8[i] = bin.charCodeAt(i);
  return ui8;
}

function configurarPush(btn, estado) {
  if (!btn) return;
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    if (estado) estado.textContent = "Notificaciones no disponibles en este navegador.";
    btn.disabled = true;
    return;
  }
  const pintar = () => {
    navigator.serviceWorker.ready
      .then((reg) => reg.pushManager.getSubscription())
      .then((sus) => {
        const activo = Boolean(sus);
        if (estado) {
          estado.textContent = activo ? "Notificaciones activadas." : "Notificaciones desactivadas.";
        }
        btn.textContent = activo ? "Desactivar notificaciones" : "Activar notificaciones";
      })
      .catch(() => {});
  };
  pintar();
  btn.addEventListener("click", () => {
    navigator.serviceWorker.ready
      .then((reg) => reg.pushManager.getSubscription())
      .then((sus) => {
        if (sus) {
          return peticion("/api/push/eliminar", {
            method: "POST",
            body: JSON.stringify({ endpoint: sus.endpoint }),
          }).then(() => sus.unsubscribe());
        }
        return null;
      })
      .then((finalizado) => {
        if (finalizado !== null) return undefined;
        if (!("Notification" in window)) return undefined;
        return peticion("/api/push/clave").then((r) => {
          if (!r.ok) throw new Error("No autenticado.");
          return r.json();
        }).then((d) =>
          Notification.requestPermission().then((perm) => {
            if (perm !== "granted") return undefined;
            return navigator.serviceWorker.ready.then((reg) =>
              reg.pushManager.subscribe({
                userVisibleOnly: true,
                applicationServerKey: _claveVapidUint8(d.vapid_public_key),
              })
            );
          })
        ).then((sus) => {
          if (!sus) return undefined;
          const clave = sus.toJSON();
          return peticion("/api/push/registrar", {
            method: "POST",
            body: JSON.stringify({
              endpoint: sus.endpoint,
              p256dh: clave.keys.p256dh,
              auth: clave.keys.auth,
            }),
          });
        });
      })
      .then(pintar)
      .catch(() => {});
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
    const linkCont = document.getElementById("linkContador");
    if (linkCont && (data.rol === "admin" || data.acceso_contador)) {
      linkCont.classList.remove("oculto");
    }
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
  const barraFill = document.getElementById("barraFill");
  const barraPct = document.getElementById("barraPct");
  const descargaBox = document.getElementById("descargaBox");
  const linkDescargar = document.getElementById("linkDescargar");
  const btnImprimir = document.getElementById("btnImprimir");
  const btnConsultar = document.getElementById("btnConsultar");

  let jobId = null;
  let pollTimer = null;
  // Estado de la barra de progreso (0-100% con animación suave).
  let pctActual = 0;
  let pctObjetivo = 0;
  let animTimer = null;

  // Etapas conocidas del proceso: cada hito eleva el mínimo de la barra.
  const HITOS = [
    ["login en muisca", 15],
    ["sesión iniciada correctamente", 35],
    ["exógena descargada", 50],
    ["analizando obligación de declarar renta", 58],
    ["declara renta", 62],
    ["facturación electrónica", 75],
    ["archivo cliente generado", 90],
  ];

  function pctPorHitos(lineas) {
    const texto = (lineas.join("\n") || "").toLowerCase();
    let obj = 8;
    HITOS.forEach((par) => {
      if (texto.includes(par[0])) obj = Math.max(obj, par[1]);
    });
    return Math.min(99, obj);
  }

  function pintarPct() {
    barraFill.style.width = pctActual + "%";
    barraPct.textContent = Math.round(pctActual) + "%";
  }

  function iniciarAnimacion() {
    if (animTimer) return;
    animTimer = setInterval(() => {
      const diff = pctObjetivo - pctActual;
      if (diff >= 0.5 && pctActual < 99) {
        pctActual += Math.max(0.5, diff * 0.12);
      } else {
        pctActual = pctObjetivo;
      }
      pintarPct();
    }, 130);
  }

  function pararAnimacion() {
    if (animTimer) { clearInterval(animTimer); animTimer = null; }
  }

  function formatearMonto(n) {
    if (n === null || n === undefined || isNaN(n)) return "—";
    if (n === 0) return "$0";
    return "$" + Math.round(n).toLocaleString("es-CO");
  }

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
    pctActual = 0;
    pctObjetivo = 8;
    barraFill.className = "barra-fill";
    pintarPct();

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
        pararAnimacion();
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
          pararAnimacion();
          pctActual = 100;
          pintarPct();
          barraFill.classList.add("completo");
          estadoJob.textContent = "Consulta completada.";
          finalizarJob(data);
        } else if (data.estado === "error") {
          clearInterval(pollTimer);
          pararAnimacion();
          barraFill.classList.add("error");
          estadoJob.textContent = "Error en el proceso.";
          mostrarMensaje(mensaje, data.error || "Falló la consulta.", "error");
          btnConsultar.disabled = false;
        } else if (data.estado === "running") {
          estadoJob.textContent = "Procesando consulta...";
          pctObjetivo = pctPorHitos(data.progreso);
          iniciarAnimacion();
        } else {
          estadoJob.textContent = "En cola...";
          pctObjetivo = Math.max(pctObjetivo, 5);
          iniciarAnimacion();
        }
      });
    });
  }

  function finalizarJob(data) {
    descargaBox.classList.remove("oculto");
    linkDescargar.href = "/api/job/" + jobId + "/descargar";
    btnConsultar.disabled = false;
    mostrarMensaje(mensaje, "Consulta completada.", "ok");

    const r = data.resultado || {};
    const declara = r.declara_renta === "Sí";
    document.getElementById("resultadoTitulo").textContent =
      declara ? "OBLIGADO A DECLARAR RENTA" : "NO OBLIGADO A DECLARAR RENTA";
    document.getElementById("resultadoIcono").textContent = declara ? "!" : "\u2713";
    document.getElementById("resultadoVeredicto").className =
      "resultado-veredicto " + (declara ? "verdict-si" : "verdict-no");
    document.getElementById("resultadoCliente").textContent =
      r.nombre_cliente ? "Cliente: " + r.nombre_cliente : "";

    const vence = document.getElementById("resultadoVence");
    if (declara && r.fecha_vencimiento) {
      vence.textContent = "Vence: " + r.fecha_vencimiento;
      vence.classList.remove("oculto");
    } else {
      vence.classList.add("oculto");
    }

    document.getElementById("resultadoCabecera").textContent = r.cabecera || "";

    const nota = document.getElementById("resultadoNota");
    if (r.nota) {
      nota.textContent = r.nota;
      nota.classList.remove("oculto");
    } else {
      nota.classList.add("oculto");
    }

    const tb = document.querySelector("#tablaTopes tbody");
    tb.innerHTML = "";
    (r.topes || []).forEach((t) => {
      const fila = document.createElement("tr");
      fila.innerHTML =
        "<td>" + escapar(t.desc) + "</td>" +
        "<td>" + escapar(formatearMonto(t.reportado)) + "</td>" +
        "<td>" + escapar(formatearMonto(t.umbral)) + "</td>" +
        '<td><span class="etiqueta etiqueta-' + (t.excede ? "excede" : "ok") + '">' +
          (t.excede ? "EXCEDE" : "ok") + "</span></td>";
      tb.appendChild(fila);
    });

    document.getElementById("printFecha").textContent =
      "Bogotá, " + new Date().toLocaleDateString("es-CO", {
        day: "numeric", month: "long", year: "numeric",
      });

    if (btnImprimir) {
      btnImprimir.addEventListener("click", () => window.print());
    }
  }
}

/* ------------------------------------------------------------------ */
/* Panel de consultas masivas (contadores.html)                        */
/* ------------------------------------------------------------------ */
const ETIQUETA_BATCH = {
  ok: "Exitoso",
  error_credenciales: "Error credenciales",
  desconocido: "Resultado desconocido",
  excepcion: "Excepción",
};

function iniciarContadores() {
  configurarSesion();
  registrarServiceWorker();
  const form = document.getElementById("masivaForm");
  const archivo = document.getElementById("archivoXlsx");
  const mensaje = document.getElementById("mensaje");
  const progresoBox = document.getElementById("progresoBox");
  const tablaBox = document.getElementById("tablaBox");
  const descargaBox = document.getElementById("descargaBox");
  const linkDescargar = document.getElementById("linkDescargar");
  const logProgreso = document.getElementById("logProgreso");
  const estadoJob = document.getElementById("estadoJob");
  const barraFill = document.getElementById("barraFill");
  const barraPct = document.getElementById("barraPct");
  const btnIniciar = document.getElementById("btnIniciar");

  let batchId = null;
  let pollTimer = null;
  let total = 0;

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (batchId) { mostrarMensaje(mensaje, "Ya hay una consulta masiva en curso.", "info"); return; }
    if (!archivo.files || !archivo.files[0]) { mostrarMensaje(mensaje, "Selecciona un archivo .xlsx.", "error"); return; }

    progresoBox.classList.remove("oculto");
    tablaBox.classList.add("oculto");
    descargaBox.classList.add("oculto");
    logProgreso.textContent = "";
    estadoJob.textContent = "Subiendo archivo...";
    btnIniciar.disabled = true;

    const fd = new FormData();
    fd.append("archivo", archivo.files[0]);

    peticion("/api/masiva/upload", { method: "POST", body: fd }).then((resp) => {
      if (!resp.ok) {
        btnIniciar.disabled = false;
        return resp.json().then((d) => mostrarMensaje(mensaje, extraerDetalle(d), "error"));
      }
      return resp.json().then((data) => {
        batchId = data.batch_id;
        total = data.total || 0;
        estadoJob.textContent = "Procesando 0/" + total + " clientes...";
        pollTimer = setInterval(consultarBatch, 1500);
      });
    });
  });

  function consultarBatch() {
    if (!batchId) return;
    peticion("/api/masiva/" + batchId).then((resp) => {
      if (!resp.ok) { clearInterval(pollTimer); btnIniciar.disabled = false; return; }
      return resp.json().then((data) => {
        logProgreso.textContent = data.progreso.join("\n");
        logProgreso.scrollTop = logProgreso.scrollHeight;
        const pct = data.total ? Math.min(100, Math.round((data.done / data.total) * 100)) : 0;
        barraFill.style.width = pct + "%";
        barraPct.textContent = pct + "%";

        if (data.estado === "done") {
          clearInterval(pollTimer);
          barraFill.classList.add("completo");
          estadoJob.textContent = "Consulta masiva completada.";
          pintarTabla(data.detalle || []);
          descargaBox.classList.remove("oculto");
          linkDescargar.href = "/api/masiva/" + batchId + "/descargar";
          btnIniciar.disabled = false;
          mostrarMensaje(mensaje, "Consulta masiva terminada.", "ok");
        } else if (data.estado === "error") {
          clearInterval(pollTimer);
          barraFill.classList.add("error");
          estadoJob.textContent = "Error en el proceso.";
          mostrarMensaje(mensaje, data.error || "Falló el proceso.", "error");
          btnIniciar.disabled = false;
        } else if (data.estado === "running") {
          estadoJob.textContent = "Procesando " + data.done + "/" + data.total + " clientes...";
        } else {
          estadoJob.textContent = "En cola...";
        }
      });
    });
  }

  function pintarTabla(detalle) {
    const tbody = document.querySelector("#tablaFilas tbody");
    if (!tbody) return;
    tbody.innerHTML = "";
    detalle.forEach((f) => {
      const fila = document.createElement("tr");
      const estado = f.error ? "error_credenciales" : "ok";
      fila.innerHTML =
        "<td>" + f.fila_excel + "</td>" +
        "<td>" + escapar(f.numero_documento) + "</td>" +
        '<td><span class="etiqueta etiqueta-' + (f.error ? "error" : "ok") + '">' +
          (f.error ? ETIQUETA_BATCH.error_credenciales : ETIQUETA_BATCH.ok) + "</span></td>" +
        "<td>" + escapar(f.final || f.error || "—") + "</td>";
      tbody.appendChild(fila);
    });
    tablaBox.classList.remove("oculto");
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
            "<td>" + escapar(u.rol) +
              (u.acceso_contador ? ' <span class="etiqueta etiqueta-aprobado">Contador</span>' : "") +
              "</td>" +
            '<td><span class="etiqueta etiqueta-' + u.estado + '">' +
              (ETIQUETA_ESTADO[u.estado] || escapar(u.estado)) + "</span></td>" +
            "<td>" + escapar(u.creado_en) + "</td>" +
            "<td></td>";

          const acciones = fila.lastElementChild;
          if (u.rol !== "admin") {
            acciones.appendChild(btnAccion(
              u.acceso_contador ? "Quitar Contador" : "Activar Contador",
              u.acceso_contador ? "secundario" : "ok",
              () => alternarContador(u.usuario, !u.acceso_contador)
            ));
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

  function alternarContador(usuario, activar) {
    peticion("/api/admin/contador", {
      method: "POST",
      body: JSON.stringify({ usuario, activar }),
    }).then((resp) => {
      if (resp.ok) {
        mostrarMensaje(mensaje,
          activar ? "Acceso a Consultas Masivas activado." : "Acceso a Consultas Masivas desactivado.",
          "ok");
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
  } else if (path === "/contadores") {
    iniciarContadores();
  }
});