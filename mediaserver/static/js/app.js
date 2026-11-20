/* Shared helpers for the web client. Plain ES module-free script, no build step. */
(function () {
  "use strict";

  const MS = (window.MS = window.MS || {});

  MS.escapeHTML = function (value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[ch]));
  };

  MS.formatTime = function (seconds) {
    const total = Math.max(0, Math.round(Number(seconds) || 0));
    const hours = Math.floor(total / 3600);
    const mins = Math.floor((total % 3600) / 60);
    const secs = total % 60;
    if (hours) return `${hours}:${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
    return `${mins}:${String(secs).padStart(2, "0")}`;
  };

  MS.toast = function (message, isError) {
    const node = document.createElement("div");
    node.className = "toast" + (isError ? " error" : "");
    node.textContent = message;
    document.body.appendChild(node);
    setTimeout(() => node.remove(), 4200);
  };

  MS.fetchJSON = async function (url, options) {
    const settings = Object.assign({ credentials: "same-origin" }, options || {});
    if (settings.body && !(settings.body instanceof FormData)) {
      settings.headers = Object.assign({ "Content-Type": "application/json" }, settings.headers || {});
      if (typeof settings.body !== "string") settings.body = JSON.stringify(settings.body);
    }
    const response = await fetch(url, settings);
    let payload = null;
    try {
      payload = await response.json();
    } catch (error) {
      payload = null;
    }
    if (!response.ok) {
      const detail = payload && (payload.error || payload.detail) ? (payload.error || payload.detail) : response.status;
      throw new Error(detail);
    }
    return payload;
  };

  MS.saveProgress = function (itemId, position, duration, played) {
    if (!itemId) return;
    const body = { itemId, position, duration };
    if (played) body.played = true;
    fetch("/api/v1/progress", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      keepalive: true,
    }).catch(() => {});
  };

  MS.readJSONBlock = function (id) {
    const node = document.getElementById(id);
    if (!node) return null;
    try {
      return JSON.parse(node.textContent || "{}");
    } catch (error) {
      return null;
    }
  };

  MS.debounce = function (fn, wait) {
    let timer = null;
    return function () {
      const args = arguments;
      clearTimeout(timer);
      timer = setTimeout(() => fn.apply(this, args), wait);
    };
  };

  // Forms with a select/checkbox marked data-autosubmit submit on change.
  document.querySelectorAll("[data-autosubmit]").forEach((field) => {
    field.addEventListener("change", () => field.form && field.form.submit());
  });
})();
