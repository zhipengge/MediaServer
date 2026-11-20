/* Audio player page: queue, lyrics sync, resume position, playlist add. */
(function () {
  "use strict";

  const data = window.MS.readJSONBlock("player-data");
  if (!data || !data.item) return;

  const audio = document.getElementById("player");
  const lyricsBox = document.getElementById("lyrics");
  const queueList = document.getElementById("queue-list");
  if (!audio) return;

  let queue = Array.isArray(data.queue) && data.queue.length ? data.queue.slice() : [data.item];
  let index = queue.findIndex((entry) => entry.id === data.item.id);
  if (index < 0) {
    queue = [data.item].concat(queue);
    index = 0;
  }

  let lyrics = [];
  let lyricNodes = [];
  let lastSavedAt = 0;
  let markedPlayed = false;
  let pendingSeek = Number(data.position) || 0;

  function parseLRC(text) {
    const lines = [];
    const stamp = /^\[(\d+):(\d{1,2})(?:[.:](\d{1,3}))?\]/;
    (text || "").split(/\r?\n/).forEach((raw) => {
      let rest = raw.trim();
      if (!rest) return;
      const times = [];
      let match = stamp.exec(rest);
      while (match) {
        const minutes = Number(match[1]);
        const seconds = Number(match[2]);
        const fraction = match[3] || "0";
        let millis = 0;
        if (fraction.length === 1) millis = Number(fraction) * 100;
        else if (fraction.length === 2) millis = Number(fraction) * 10;
        else millis = Number(fraction.slice(0, 3));
        times.push(minutes * 60 + seconds + millis / 1000);
        rest = rest.slice(match[0].length);
        match = stamp.exec(rest);
      }
      const content = rest.trim();
      if (!content) return;
      if (!times.length) {
        lines.push({ time: null, text: content });
      } else {
        times.forEach((time) => lines.push({ time, text: content }));
      }
    });
    lines.sort((a, b) => (a.time == null ? 0 : a.time) - (b.time == null ? 0 : b.time));
    return lines.filter((line) => line.time != null);
  }

  function renderLyrics(source) {
    lyrics = parseLRC(source);
    lyricNodes = [];
    if (!lyrics.length) {
      lyricsBox.innerHTML = "";
      const note = document.createElement("p");
      note.className = "muted";
      note.textContent = source ? "(歌词没有时间轴，无法同步高亮)" : "这首曲目没有歌词。";
      lyricsBox.appendChild(note);
      return;
    }
    lyricsBox.innerHTML = "";
    lyrics.forEach((line) => {
      const node = document.createElement("p");
      node.textContent = line.text;
      lyricsBox.appendChild(node);
      lyricNodes.push(node);
    });
  }

  function highlight(time) {
    if (!lyrics.length) return;
    let current = -1;
    for (let i = 0; i < lyrics.length; i += 1) {
      if (lyrics[i].time <= time + 0.15) current = i;
      else break;
    }
    lyricNodes.forEach((node, i) => node.classList.toggle("active", i === current));
    if (current >= 0) {
      const node = lyricNodes[current];
      const box = lyricsBox.getBoundingClientRect();
      const spot = node.getBoundingClientRect();
      if (spot.top < box.top + 24 || spot.bottom > box.bottom - 24) {
        node.scrollIntoView({ block: "center", behavior: "smooth" });
      }
    }
  }

  function updateHeader(item) {
    const title = document.querySelector(".np-meta h1");
    if (title) title.textContent = item.title;
    const meta = document.querySelector(".np-meta p.muted");
    if (meta) {
      const parts = [item.artist, item.album, item.year].filter(Boolean);
      meta.textContent = parts.join(" · ");
    }
    const cover = document.querySelector(".cover-big");
    if (cover) {
      if (item.cover || item.thumb) {
        const image = document.createElement("img");
        image.className = "poster";
        image.src = item.cover || item.thumb;
        image.alt = "";
        cover.replaceChildren(image);
      } else {
        const fallback = document.createElement("div");
        fallback.className = "poster poster-fallback";
        fallback.textContent = "♪";
        cover.replaceChildren(fallback);
      }
    }
    document.title = item.title;
  }

  async function loadLyrics(item) {
    if (!item.lyricsUrl) {
      renderLyrics("");
      return;
    }
    try {
      const response = await fetch(item.lyricsUrl, { credentials: "same-origin" });
      if (!response.ok) {
        renderLyrics("");
        return;
      }
      renderLyrics(await response.text());
    } catch (error) {
      renderLyrics("");
    }
  }

  function playIndex(newIndex, autoplay) {
    if (newIndex < 0 || newIndex >= queue.length) return;
    index = newIndex;
    const item = queue[index];
    audio.src = item.rawUrl || item.url;
    updateHeader(item);
    loadLyrics(item);
    queueList.querySelectorAll("button").forEach((button, i) => {
      button.classList.toggle("current", i === index);
    });
    markedPlayed = false;
    pendingSeek = item.id === data.item.id ? (Number(data.position) || 0) : 0;
    if (autoplay !== false) {
      audio.play().catch(() => window.MS.toast("浏览器阻止了自动播放，点播放键继续", true));
    }
  }

  function renderQueue() {
    if (!queueList) return;
    queueList.innerHTML = "";
    queue.forEach((item, i) => {
      const li = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = i === index ? "current" : "";
      const title = document.createElement("span");
      title.className = "q-title";
      title.textContent = item.title;
      const artist = document.createElement("span");
      artist.className = "q-artist";
      artist.textContent = item.artist || "";
      button.append(title, artist);
      button.addEventListener("click", () => playIndex(i));
      li.appendChild(button);
      queueList.appendChild(li);
    });
  }

  audio.addEventListener("timeupdate", () => {
    highlight(audio.currentTime);
    if (!markedPlayed && audio.duration && audio.currentTime / audio.duration > 0.9) {
      markedPlayed = true;
      window.MS.saveProgress(queue[index].id, audio.currentTime, audio.duration, true);
    }
  });

  audio.addEventListener("loadedmetadata", () => {
    if (pendingSeek > 5 && pendingSeek < audio.duration - 3) {
      audio.currentTime = pendingSeek;
    }
    pendingSeek = 0;
  });

  audio.addEventListener("pause", () => {
    if (audio.duration) window.MS.saveProgress(queue[index].id, audio.currentTime, audio.duration, false);
  });

  audio.addEventListener("ended", () => {
    window.MS.saveProgress(queue[index].id, audio.currentTime, audio.duration, true);
    if (index + 1 < queue.length) {
      playIndex(index + 1, true);
    }
  });

  setInterval(() => {
    if (!audio.paused && audio.duration && audio.currentTime - lastSavedAt >= 10) {
      lastSavedAt = audio.currentTime;
      window.MS.saveProgress(queue[index].id, audio.currentTime, audio.duration, false);
    }
  }, 5000);

  window.addEventListener("beforeunload", () => {
    if (audio.duration) window.MS.saveProgress(queue[index].id, audio.currentTime, audio.duration, false);
  });

  const addButton = document.getElementById("add-to-playlist");
  const playlistSelect = document.getElementById("playlist-select");
  if (addButton && playlistSelect) {
    addButton.addEventListener("click", async () => {
      try {
        await window.MS.fetchJSON(`/api/v1/playlists/${encodeURIComponent(playlistSelect.value)}/items`, {
          method: "POST",
          body: { itemId: queue[index].id },
        });
        window.MS.toast("已加入播放列表");
      } catch (error) {
        window.MS.toast(`加入失败：${error.message}`, true);
      }
    });
  }

  renderLyrics(data.lyrics || "");
  renderQueue();
  playIndex(index, false);
})();
