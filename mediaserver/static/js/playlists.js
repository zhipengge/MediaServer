/* Playlist creation (list page) and item removal (detail page). */
(function () {
  "use strict";

  const form = document.getElementById("playlist-form");
  const status = document.getElementById("playlist-status");
  if (form) {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const input = document.getElementById("playlist-name");
      const name = (input && input.value || "").trim();
      if (!name) return;
      try {
        await window.MS.fetchJSON("/api/v1/playlists", { method: "POST", body: { name } });
        window.location.reload();
      } catch (error) {
        if (status) status.textContent = `创建失败：${error.message}`;
        window.MS.toast(`创建失败：${error.message}`, true);
      }
    });
  }

  const meta = window.MS.readJSONBlock("playlist-meta");
  if (meta) {
    document.querySelectorAll(".remove-item").forEach((button) => {
      button.addEventListener("click", async () => {
        const itemId = button.getAttribute("data-item");
        try {
          await window.MS.fetchJSON(
            `/api/v1/playlists/${encodeURIComponent(meta.id)}/items/${encodeURIComponent(itemId)}`,
            { method: "DELETE" }
          );
          const row = button.closest(".playlist-row");
          if (row) row.remove();
          window.MS.toast("已移除");
        } catch (error) {
          window.MS.toast(`移除失败：${error.message}`, true);
        }
      });
    });
  }
})();
