/* Share upload form with progress reporting. */
(function () {
  "use strict";

  const form = document.getElementById("share-form");
  if (!form) return;
  const status = document.getElementById("share-status");
  const progress = document.getElementById("share-progress");
  const submit = form.querySelector("button[type=submit]");

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const formData = new FormData(form);
    if (!formData.get("file") || !formData.get("file").name) {
      if (status) status.textContent = "请选择一个文件";
      return;
    }
    formData.set("kind", "file");
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/v1/share");
    xhr.withCredentials = true;
    if (status) status.textContent = "上传中…";
    if (progress) {
      progress.hidden = false;
      progress.value = 0;
    }
    if (submit) submit.disabled = true;

    xhr.upload.addEventListener("progress", (progressEvent) => {
      if (progressEvent.lengthComputable && progress) {
        progress.value = Math.round((progressEvent.loaded / progressEvent.total) * 100);
      }
    });

    xhr.addEventListener("load", () => {
      if (submit) submit.disabled = false;
      let payload = null;
      try {
        payload = JSON.parse(xhr.responseText || "{}");
      } catch (error) {
        payload = null;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        if (status) status.textContent = "分享成功，正在刷新…";
        window.location.reload();
      } else {
        const message = payload && payload.error ? payload.error : `HTTP ${xhr.status}`;
        if (status) status.textContent = `失败：${message}`;
        window.MS.toast(`分享失败：${message}`, true);
      }
    });

    xhr.addEventListener("error", () => {
      if (submit) submit.disabled = false;
      if (status) status.textContent = "网络错误";
      window.MS.toast("网络错误", true);
    });

    xhr.send(formData);
  });
})();
