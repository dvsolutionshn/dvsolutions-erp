(function () {
  "use strict";

  function initializeBalance(root) {
    if (root.dataset.balanceReady === "true") return;
    const panels = new WeakMap();

    function localUrl(value) {
      const url = new URL(value, window.location.href);
      if (url.origin !== window.location.origin) throw new Error("Destino no permitido");
      return url.href;
    }

    function showStatus(state, failed) {
      const status = document.createElement("div");
      status.className = "bgm-estado" + (failed ? " bgm-error" : " bgm-cargando");
      const message = document.createElement("p");
      message.setAttribute("role", failed ? "alert" : "status");
      message.textContent = failed
        ? "No se pudieron cargar los movimientos. Puedes reintentar o consultar el Mayor."
        : "Cargando movimientos de la cuenta…";
      status.appendChild(message);
      if (failed) {
        const retry = document.createElement("button");
        retry.type = "button";
        retry.className = "ef-btn ef-btn--quiet";
        retry.setAttribute("data-balance-reintentar", "");
        retry.textContent = "Reintentar";
        status.appendChild(retry);
      }
      if (state.fallback) status.appendChild(state.fallback.cloneNode(true));
      state.content.replaceChildren(status);
    }

    async function load(state, requestedUrl, pagination) {
      if (state.pending) return;
      state.pending = true;
      state.loaded = false;
      state.lastUrl = requestedUrl;
      state.content.setAttribute("aria-busy", "true");
      showStatus(state, false);
      try {
        const response = await fetch(localUrl(requestedUrl), {
          method: "GET",
          credentials: "same-origin",
          mode: "same-origin",
          redirect: "error",
          headers: {Accept: "text/html"},
        });
        if (!response.ok || response.redirected || response.headers.get("X-Balance-Movimientos") !== "1") {
          throw new Error("Respuesta de movimientos no disponible");
        }
        const template = document.createElement("template");
        template.innerHTML = await response.text();
        const fragment = template.content.querySelector("[data-balance-fragmento]");
        if (!fragment || fragment.querySelector("script")) throw new Error("Detalle no disponible");
        state.content.replaceChildren(fragment);
        state.loaded = true;
        if (pagination && !state.panel.hidden) {
          fragment.setAttribute("tabindex", "-1");
          fragment.focus({preventScroll: true});
        }
      } catch (_error) {
        showStatus(state, true);
      } finally {
        state.pending = false;
        state.content.setAttribute("aria-busy", "false");
      }
    }

    root.querySelectorAll("[data-balance-toggle]").forEach(function (button) {
      const panel = document.getElementById(button.getAttribute("aria-controls"));
      const content = panel && panel.querySelector("[data-balance-contenido]");
      if (!panel || !root.contains(panel) || !content) return;
      const fallback = content.querySelector("[data-balance-mayor]");
      const state = {
        button: button, panel: panel, content: content,
        fallback: fallback ? fallback.cloneNode(true) : null,
        loaded: false, pending: false, lastUrl: button.dataset.movimientosUrl,
      };
      panels.set(panel, state);
      button.addEventListener("click", function () {
        const expanded = button.getAttribute("aria-expanded") !== "true";
        button.setAttribute("aria-expanded", String(expanded));
        button.setAttribute("aria-label", (expanded ? "Ocultar" : "Ver") + " movimientos de " + button.dataset.cuentaNombre);
        panel.hidden = !expanded;
        const indicator = button.querySelector("[data-balance-indicador]");
        if (indicator) indicator.textContent = expanded ? "▼" : "▶";
        if (expanded && !state.loaded && !state.pending) load(state, state.lastUrl, false);
      });
      button.hidden = false;
    });

    root.addEventListener("click", function (event) {
      const action = event.target.closest("[data-balance-pagina], [data-balance-reintentar]");
      if (!action || !root.contains(action)) return;
      const panel = action.closest("[data-balance-panel]");
      const state = panel && panels.get(panel);
      if (!state) return;
      if (event.button > 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      if (state.pending) return;
      const pagination = action.hasAttribute("data-balance-pagina");
      load(state, pagination ? action.getAttribute("href") : state.lastUrl, pagination);
    });
    root.dataset.balanceReady = "true";
  }

  function initialize() {
    document.querySelectorAll("[data-balance-interactivo]").forEach(initializeBalance);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initialize, {once: true});
  else initialize();
})();
