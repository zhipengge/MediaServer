/* Video page: direct progressive playback, or on-demand HLS transcode. */
(function () {
  "use strict";

  const data = window.MS.readJSONBlock("watch-data");
  if (!data || !data.item) return;
  const item = data.item;
  const video = document.getElementById("video");
  const statusBox = document.getElementById("player-status");
  const transcodeButton = document.getElementById("transcode");
  if (!video) return;

  let hls = null;
  let lastSavedAt = 0;
  let attached = false;

  function setStatus(text) {
    if (statusBox) statusBox.textContent = text;
  }

  function save(played) {
    if (video.duration) {
      window.MS.saveProgress(item.id, video.currentTime, video.duration, played);
    }
  }

  function attachDirect() {
    attached = true;
    video.src = item.rawUrl;
    setStatus("直接播放（Range 流播）");
    if (data.position > 5 && data.position < (item.duration || Infinity) - 5) {
      video.addEventListener("loadedmetadata", () => {
        video.currentTime = data.position;
      }, { once: true });
    }
    video.play().catch(() => {});
  }

  function attachHls(playlistUrl) {
    attached = true;
    const native = video.canPlayType("application/vnd.apple.mpegurl");
    if (native) {
      video.src = playlistUrl;
      setStatus("HLS 播放（浏览器原生）");
      video.play().catch(() => {});
      return;
    }
    if (window.Hls && window.Hls.isSupported()) {
      if (hls) hls.destroy();
      hls = new window.Hls({ maxBufferLength: 30 });
      hls.loadSource(playlistUrl);
      hls.attachMedia(video);
      hls.on(window.Hls.Events.MANIFEST_PARSED, () => {
        setStatus("HLS 播放（hls.js）");
        if (data.position > 5) video.currentTime = data.position;
        video.play().catch(() => {});
      });
      hls.on(window.Hls.Events.ERROR, (_event, detail) => {
        if (detail.fatal) {
          setStatus(`播放失败：${detail.type}`);
          window.MS.toast("HLS 播放出错，可下载原文件后本地播放", true);
        }
      });
      return;
    }
    setStatus("浏览器不支持 HLS，请下载原文件或用 Safari 播放");
    window.MS.toast("这个浏览器没有 HLS 支持", true);
  }

  async function pollJob(jobId, attempts) {
    if (attempts > 240) {
      setStatus("转码超时，请查看服务器日志");
      return;
    }
    try {
      const job = await window.MS.fetchJSON(`/api/v1/jobs/${jobId}`);
      if (job.status === "ready") {
        attachHls(job.playlist);
        return;
      }
      if (job.status === "error") {
        setStatus(`转码失败：${job.error || "未知错误"}`);
        return;
      }
      const percent = Math.round((job.progress || 0) * 100);
      setStatus(`正在转码… ${percent}%`);
      setTimeout(() => pollJob(jobId, attempts + 1), 1500);
    } catch (error) {
      setStatus(`无法查询转码任务：${error.message}`);
    }
  }

  async function startTranscode() {
    if (!data.hlsEnabled) {
      setStatus("服务器未启用转码，可直接下载原文件播放");
      return;
    }
    setStatus("提交转码任务…");
    try {
      const job = await window.MS.fetchJSON(`/api/v1/transcode/${item.id}`, {
        method: "POST",
        body: {},
      });
      if (job.status === "ready") {
        attachHls(job.playlist);
        return;
      }
      pollJob(job.jobId, 0);
    } catch (error) {
      setStatus(`转码不可用：${error.message}`);
    }
  }

  video.addEventListener("timeupdate", () => {
    if (!video.paused && video.currentTime - lastSavedAt >= 10) {
      lastSavedAt = video.currentTime;
      save(false);
    }
  });
  video.addEventListener("pause", () => save(false));
  video.addEventListener("ended", () => save(true));

  if (transcodeButton) {
    transcodeButton.addEventListener("click", () => {
      if (attached && hls) return;
      startTranscode();
    });
  }

  window.addEventListener("beforeunload", () => {
    if (attached) save(false);
  });

  if (data.direct) {
    attachDirect();
  } else {
    startTranscode();
  }
})();
