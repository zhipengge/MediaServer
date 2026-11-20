/* Global bottom player: queue, modes, volume, Media Session, keyboard shortcuts.
 *
 * Pages call `MSPlayer.setQueue(items, startIndex, autoplay)` with item objects
 * shaped like /api/v1/items rows (id, title, artist, album, rawUrl, cover,
 * duration). The queue survives page loads in localStorage; audio itself cannot
 * survive a navigation (no SPA), so a restored queue starts paused.
 */
(function () {
  "use strict";

  const audio = document.getElementById("ms-audio");
  const bar = document.getElementById("ms-player");
  if (!audio || !bar) return;

  const $ = (id) => document.getElementById(id);
  const STORAGE_KEY = "ms.player.v2";
  const MODES = ["order", "repeat", "single", "shuffle"];
  const MODE_META = {
    order: { icon: "➡", label: "顺序播放" },
    repeat: { icon: "🔁", label: "列表循环" },
    single: { icon: "🔂", label: "单曲循环" },
    shuffle: { icon: "🔀", label: "随机播放" },
  };

  let queue = [];
  let index = -1;
  let mode = "order";
  let favorites = new Set();
  let lastSavedAt = 0;
  let markedPlayed = false;
  let restorePosition = 0;
  let restorePending = false;

  const clamp = (value, lo, hi) => Math.min(hi, Math.max(lo, value));

  function strip(item) {
    return {
      id: item.id,
      title: item.title || "未命名",
      artist: item.artist || "",
      album: item.album || "",
      rawUrl: item.rawUrl || item.url,
      cover: item.cover || item.thumb || null,
      duration: item.duration || null,
    };
  }

  function current() {
    return queue[index] || null;
  }

  function persist() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        queue: queue.slice(0, 300),
        index,
        mode,
        volume: audio.volume,
        position: current() ? audio.currentTime : 0,
      }));
    } catch (error) {
      /* storage can be disabled; playback still works */
    }
  }

  function restore() {
    let saved = null;
    try {
      saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
    } catch (error) {
      saved = null;
    }
    if (!saved || !Array.isArray(saved.queue) || !saved.queue.length) return;
    queue = saved.queue.map(strip).filter((item) => item.id && item.rawUrl);
    index = clamp(Number(saved.index) || 0, 0, queue.length - 1);
    mode = MODES.includes(saved.mode) ? saved.mode : "order";
    restorePosition = Number(saved.position) || 0;
    restorePending = true;
    audio.volume = clamp(Number(saved.volume ?? 0.8), 0, 1);
    $("pb-vol").value = String(Math.round(audio.volume * 100));
    audio.src = queue[index].rawUrl;
    render();
  }

  function render() {
    const item = current();
    if (!item) {
      bar.hidden = true;
      return;
    }
    bar.hidden = false;
    $("pb-title").textContent = item.title;
    $("pb-title").href = "/listen/" + encodeURIComponent(item.id);
    $("pb-artist").textContent = [item.artist, item.album].filter(Boolean).join(" · ");
    const cover = $("pb-cover");
    if (item.cover) {
      cover.src = item.cover;
      cover.hidden = false;
    } else {
      cover.hidden = true;
      cover.removeAttribute("src");
    }
    $("pb-expand").href = "/listen/" + encodeURIComponent(item.id);
    $("pb-dur").textContent = formatTime(item.duration || audio.duration || 0);
    $("pb-fav").textContent = favorites.has(item.id) ? "♥" : "♡";
    $("pb-fav").classList.toggle("faved", favorites.has(item.id));
    const meta = MODE_META[mode];
    $("pb-mode").textContent = meta.icon;
    $("pb-mode").title = meta.label;
    updateMediaSession(item);
  }

  function formatTime(seconds) {
    const total = Math.max(0, Math.round(Number(seconds) || 0));
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    if (h) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
    return `${m}:${String(s).padStart(2, "0")}`;
  }

  function updateMediaSession(item) {
    if (!("mediaSession" in navigator)) return;
    try {
      navigator.mediaSession.metadata = new MediaMetadata({
        title: item.title,
        artist: item.artist || "",
        album: item.album || "",
        artwork: item.cover ? [{ src: item.cover, sizes: "512x512" }] : [],
      });
    } catch (error) {
      /* older browsers */
    }
  }

  function load(indexToPlay, autoplay) {
    if (indexToPlay < 0 || indexToPlay >= queue.length) return;
    index = indexToPlay;
    markedPlayed = false;
    restorePosition = 0;
    restorePending = false;
    audio.src = queue[index].rawUrl;
    render();
    persist();
    if (autoplay !== false) {
      audio.play().catch(() => {
        /* autoplay blocked until the user interacts */
        $("pb-play").textContent = "▶";
      });
    }
  }

  function setQueue(items, startIndex, autoplay) {
    queue = (items || []).map(strip).filter((item) => item.id && item.rawUrl);
    if (!queue.length) return;
    load(clamp(startIndex || 0, 0, queue.length - 1), autoplay !== false);
  }

  function toggle() {
    if (!current()) {
      if (queue.length) load(0, true);
      return;
    }
    if (audio.paused) audio.play().catch(() => {});
    else audio.pause();
  }

  function next(auto) {
    if (!queue.length) return;
    if (mode === "single") {
      audio.currentTime = 0;
      audio.play().catch(() => {});
      return;
    }
    if (mode === "shuffle") {
      if (queue.length === 1) {
        load(0, true);
        return;
      }
      let candidate = index;
      while (candidate === index) candidate = Math.floor(Math.random() * queue.length);
      load(candidate, true);
      return;
    }
    if (index + 1 < queue.length) {
      load(index + 1, true);
      return;
    }
    if (mode === "repeat" || !auto) load(0, true);
  }

  function previous() {
    if (!queue.length) return;
    if (audio.currentTime > 3) {
      audio.currentTime = 0;
      return;
    }
    if (index > 0) load(index - 1, true);
    else load(queue.length - 1, true);
  }

  function cycleMode() {
    mode = MODES[(MODES.indexOf(mode) + 1) % MODES.length];
    persist();
    render();
    window.MS.toast(MODE_META[mode].label);
  }

  function saveProgress(played) {
    const item = current();
    if (!item || !window.MS) return;
    window.MS.saveProgress(item.id, audio.currentTime, audio.duration || item.duration, played);
  }

  async function loadFavorites() {
    try {
      const response = await fetch("/api/v1/favorites?limit=500", { credentials: "same-origin" });
      if (!response.ok) return;
      const payload = await response.json();
      favorites = new Set((payload.items || []).map((item) => item.id));
      render();
    } catch (error) {
      /* favorites are optional */
    }
  }

  async function toggleFavorite() {
    const item = current();
    if (!item) return;
    const wanted = !favorites.has(item.id);
    try {
      const response = await fetch("/api/v1/favorites/" + encodeURIComponent(item.id), {
        method: wanted ? "PUT" : "DELETE",
        credentials: "same-origin",
      });
      if (!response.ok) throw new Error("HTTP " + response.status);
      if (wanted) favorites.add(item.id);
      else favorites.delete(item.id);
      render();
      window.MS.toast(wanted ? "已收藏" : "已取消收藏");
    } catch (error) {
      window.MS.toast("收藏失败：" + error.message, true);
    }
  }

  // ── UI events ────────────────────────────────────────────────────
  $("pb-play").addEventListener("click", toggle);
  $("pb-next").addEventListener("click", () => next(false));
  $("pb-prev").addEventListener("click", previous);
  $("pb-mode").addEventListener("click", cycleMode);
  $("pb-fav").addEventListener("click", toggleFavorite);

  $("pb-seek").addEventListener("input", (event) => {
    const ratio = Number(event.target.value) / 1000;
    if (audio.duration) audio.currentTime = ratio * audio.duration;
  });

  $("pb-vol").addEventListener("input", (event) => {
    audio.volume = clamp(Number(event.target.value) / 100, 0, 1);
    persist();
  });

  audio.addEventListener("play", () => {
    $("pb-play").textContent = "⏸";
  });
  audio.addEventListener("pause", () => {
    $("pb-play").textContent = "▶";
    saveProgress(false);
    persist();
  });
  audio.addEventListener("loadedmetadata", () => {
    if (restorePending && restorePosition > 3 && audio.duration - restorePosition > 3) {
      audio.currentTime = restorePosition;
    }
    restorePending = false;
    $("pb-dur").textContent = formatTime(audio.duration || 0);
  });
  audio.addEventListener("timeupdate", () => {
    const duration = audio.duration || 0;
    $("pb-cur").textContent = formatTime(audio.currentTime);
    if (duration) $("pb-seek").value = String(Math.round((audio.currentTime / duration) * 1000));
    if (!audio.paused && audio.currentTime - lastSavedAt >= 10) {
      lastSavedAt = audio.currentTime;
      saveProgress(false);
    }
    if (!markedPlayed && duration && audio.currentTime / duration > 0.9) {
      markedPlayed = true;
      saveProgress(true);
    }
  });
  audio.addEventListener("ended", () => {
    saveProgress(true);
    next(true);
  });
  audio.addEventListener("error", () => {
    if (!audio.currentSrc) return;
    window.MS.toast("这首无法播放：" + (current() ? current().title : ""), true);
    next(true);
  });

  // ── keyboard shortcuts (Listen1-style) ───────────────────────────
  document.addEventListener("keydown", (event) => {
    const tag = (event.target.tagName || "").toLowerCase();
    if (["input", "textarea", "select", "button", "a"].includes(tag)
        || event.target.isContentEditable) return;
    if (event.code === "Space") {
      event.preventDefault();
      toggle();
    } else if (event.code === "ArrowRight") {
      event.preventDefault();
      if (event.shiftKey) next(false);
      else if (audio.duration) audio.currentTime = Math.min(audio.duration, audio.currentTime + 5);
    } else if (event.code === "ArrowLeft") {
      event.preventDefault();
      if (event.shiftKey) previous();
      else if (audio.duration) audio.currentTime = Math.max(0, audio.currentTime - 5);
    }
  });

  if ("mediaSession" in navigator) {
    try {
      navigator.mediaSession.setActionHandler("play", () => toggle());
      navigator.mediaSession.setActionHandler("pause", () => toggle());
      navigator.mediaSession.setActionHandler("nexttrack", () => next(false));
      navigator.mediaSession.setActionHandler("previoustrack", previous);
      navigator.mediaSession.setActionHandler("seekbackward", () => {
        audio.currentTime = Math.max(0, audio.currentTime - 10);
      });
      navigator.mediaSession.setActionHandler("seekforward", () => {
        audio.currentTime = Math.min(audio.duration || 0, audio.currentTime + 10);
      });
    } catch (error) {
      /* unsupported action */
    }
  }

  window.addEventListener("beforeunload", () => {
    persist();
    saveProgress(false);
  });

  function setFavoriteState(itemId, favorite) {
    if (favorite) favorites.add(itemId);
    else favorites.delete(itemId);
    if (current() && current().id === itemId) render();
  }

  window.MSPlayer = {
    setQueue,
    setFavorite: setFavoriteState,
    playIndex: load,
    toggle,
    next: () => next(false),
    previous,
    current,
    get queue() { return queue.slice(); },
    get index() { return index; },
  };

  restore();
  loadFavorites();
})();
