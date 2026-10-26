/* ============================================================================
   ui.js · Microinteracciones de ExoRenta (sin dependencias)
   - Tema claro/oscuro (botón + localStorage + sin flash)
   - Destello que sigue el cursor en la tarjeta de acceso
   - Mostrar/ocultar contraseña
   - Revelado al hacer scroll
   - Toast global (window.ExoRenta.toast)
   ============================================================================ */
(function () {
  "use strict";

  var raiz = document.documentElement;
  var sinMovimiento = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---------- Tema ---------- */
  function aplicarTema(tema) {
    raiz.setAttribute("data-theme", tema);
    try { localStorage.setItem("exorenta-tema", tema); } catch (e) { /* modo privado */ }
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", tema === "light" ? "#f3f6fa" : "#070b14");
    document.querySelectorAll(".tema-toggle").forEach(function (b) {
      b.setAttribute("aria-pressed", String(tema === "light"));
      b.setAttribute("aria-label", tema === "light" ? "Cambiar a modo oscuro" : "Cambiar a modo claro");
    });
    // Avisa a otros scripts (p. ej. para recolorear gráficas de Chart.js)
    raiz.dispatchEvent(new CustomEvent("exorenta:tema", { detail: { tema: tema } }));
  }

  document.querySelectorAll(".tema-toggle").forEach(function (b) {
    b.addEventListener("click", function () {
      aplicarTema(raiz.getAttribute("data-theme") === "dark" ? "light" : "dark");
    });
  });

  /* ---------- Destello del cursor (solo escritorio) ---------- */
  var fino = window.matchMedia("(pointer: fine)").matches;
  if (fino && !sinMovimiento) {
    document.querySelectorAll("[data-spotlight]").forEach(function (caja) {
      var pendiente = false, x = 0, y = 0;
      caja.addEventListener("pointermove", function (ev) {
        var r = caja.getBoundingClientRect();
        x = ev.clientX - r.left;
        y = ev.clientY - r.top;
        if (pendiente) return;
        pendiente = true;
        requestAnimationFrame(function () {
          caja.style.setProperty("--mx", x + "px");
          caja.style.setProperty("--my", y + "px");
          pendiente = false;
        });
      });
    });
  }

  /* ---------- Inicial del usuario en la barra ---------- */
  var usuarioSpan = document.getElementById("usuarioActual");
  var avatarSpan = document.getElementById("avatarInicial");
  if (usuarioSpan && avatarSpan) {
    var pintarInicial = function () {
      var n = (usuarioSpan.textContent || "").trim();
      avatarSpan.textContent = n ? n.charAt(0).toUpperCase() : "CS";
    };
    pintarInicial();
    if ("MutationObserver" in window) {
      new MutationObserver(pintarInicial).observe(usuarioSpan, { childList: true, characterData: true, subtree: true });
    }
  }

  /* ---------- Mostrar / ocultar contraseña ---------- */
  document.querySelectorAll(".ver").forEach(function (ojo) {
    ojo.addEventListener("click", function () {
      var campo = ojo.closest(".campo") || ojo.parentElement;
      var input = campo && campo.querySelector("input[type='password'], input[type='text']");
      if (!input) return;
      var mostrar = input.type === "password";
      input.type = mostrar ? "text" : "password";
      ojo.classList.toggle("activo", mostrar);
      ojo.setAttribute("aria-label", mostrar ? "Ocultar contraseña" : "Mostrar contraseña");
    });
  });

  /* ---------- Revelado al hacer scroll ---------- */
  if ("IntersectionObserver" in window && !sinMovimiento) {
    var io = new IntersectionObserver(function (entradas) {
      entradas.forEach(function (e) {
        if (e.isIntersecting) { e.target.classList.add("visible"); io.unobserve(e.target); }
      });
    }, { threshold: 0.08 });
    document.querySelectorAll(".reveal").forEach(function (el) { io.observe(el); });
  } else {
    document.querySelectorAll(".reveal").forEach(function (el) { el.classList.add("visible"); });
  }

  /* ---------- Toast global ---------- */
  var toast = null;
  var temporizador = null;
  function asegurarToast() {
    if (toast) return toast;
    toast = document.createElement("div");
    toast.className = "toast";
    toast.setAttribute("role", "status");
    toast.setAttribute("aria-live", "polite");
    toast.innerHTML = "<i></i><span></span>";
    document.body.appendChild(toast);
    return toast;
  }
  function toast(mensaje) {
    var t = asegurarToast();
    t.querySelector("span").textContent = mensaje;
    t.classList.add("visible");
    clearTimeout(temporizador);
    temporizador = setTimeout(function () { t.classList.remove("visible"); }, 3400);
  }

  /* ---------- Transición entre vistas (login <-> registro) ---------- */
  var transicion = function (mostrar, ocultar) {
    if (!mostrar || !ocultar || mostrar === ocultar) return;
    if (!sinMovimiento && document.startViewTransition) {
      document.startViewTransition(function () {
        ocultar.classList.add("oculto");
        mostrar.classList.remove("oculto");
        if (sinMovimiento) return;
        mostrar.style.animation = "none";
        requestAnimationFrame(function () { mostrar.style.animation = "auth-entra .5s cubic-bezier(.16,1,.3,1)"; });
      });
    } else {
      ocultar.classList.add("oculto");
      mostrar.classList.remove("oculto");
    }
  };

  window.ExoRenta = window.ExoRenta || {};
  window.ExoRenta.toast = toast;
  window.ExoRenta.transicion = transicion;
  window.ExoRenta.aplicarTema = aplicarTema;
})();
