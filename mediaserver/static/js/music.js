/* Music hub: play-all, per-row play, favorites. Playback is owned by player-core. */
(function () {
  "use strict";

  const data = window.MS.readJSONBlock("music-data");
  const songs = (data && Array.isArray(data.songs)) ? data.songs : [];
  if (!songs.length) return;

  function playFrom(index) {
    if (!window.MSPlayer) return;
    window.MSPlayer.setQueue(songs, index, true);
  }

  const playAll = document.getElementById("play-all");
  if (playAll) playAll.addEventListener("click", () => playFrom(0));

  document.querySelectorAll(".track-play").forEach((button) => {
    button.addEventListener("click", () => playFrom(Number(button.getAttribute("data-index")) || 0));
  });

  document.querySelectorAll(".fav-btn").forEach((button) => {
    button.addEventListener("click", async () => {
      const itemId = button.getAttribute("data-id");
      const wanted = button.getAttribute("data-fav") !== "1";
      try {
        const response = await fetch("/api/v1/favorites/" + encodeURIComponent(itemId), {
          method: wanted ? "PUT" : "DELETE",
          credentials: "same-origin",
        });
        if (!response.ok) throw new Error("HTTP " + response.status);
        document.querySelectorAll(`.fav-btn[data-id="${itemId}"]`).forEach((node) => {
          node.setAttribute("data-fav", wanted ? "1" : "0");
          node.classList.toggle("faved", wanted);
        });
        if (window.MSPlayer && window.MSPlayer.setFavorite) {
          window.MSPlayer.setFavorite(itemId, wanted);
        }
        if (!wanted && new URLSearchParams(window.location.search).get("tab") === "favorites") {
          const row = button.closest("tr");
          if (row) row.remove();
        }
        if (window.MS) window.MS.toast(wanted ? "已收藏" : "已取消收藏");
      } catch (error) {
        if (window.MS) window.MS.toast("收藏失败：" + error.message, true);
      }
    });
  });
})();
