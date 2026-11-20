/* File manager: upload files or whole folders, keep the directory structure. */
(function () {
  "use strict";

  const dropZone = document.getElementById("drop-zone");
  const fileInput = document.getElementById("file-input");
  const folderInput = document.getElementById("folder-input");
  const statusEl = document.getElementById("upload-status");
  const progressEl = document.getElementById("upload-progress");
  const errorsEl = document.getElementById("upload-errors");
  if (!dropZone || !fileInput || !folderInput) return;

  const pickFiles = document.getElementById("pick-files");
  const pickFolder = document.getElementById("pick-folder");
  let busy = false;

  function relativeOf(file) {
    return file.relativePath || file.webkitRelativePath || file.name;
  }

  function setStatus(text) {
    if (statusEl) statusEl.textContent = text || "";
  }

  function addError(text) {
    if (!errorsEl) return;
    const item = document.createElement("li");
    item.textContent = text;
    errorsEl.appendChild(item);
  }

  function setBusy(value) {
    busy = value;
    [pickFiles, pickFolder].forEach((button) => {
      if (button) button.disabled = value;
    });
  }

  function readAllEntries(reader) {
    return new Promise((resolve) => {
      const all = [];
      const next = () => {
        reader.readEntries((entries) => {
          if (!entries.length) return resolve(all);
          all.push(...entries);
          next();
        }, () => resolve(all));
      };
      next();
    });
  }

  async function walkEntry(entry, prefix, output) {
    if (entry.isFile) {
      const file = await new Promise((resolve, reject) => entry.file(resolve, reject));
      file.relativePath = prefix + entry.name;
      output.push(file);
      return;
    }
    if (entry.isDirectory) {
      const entries = await readAllEntries(entry.createReader());
      for (const child of entries) {
        await walkEntry(child, prefix + entry.name + "/", output);
      }
    }
  }

  async function collectDropped(dataTransfer) {
    const items = Array.from(dataTransfer.items || []);
    const entries = items
      .map((item) => (item.webkitGetAsEntry ? item.webkitGetAsEntry() : null))
      .filter(Boolean);
    if (!entries.length) return Array.from(dataTransfer.files || []);
    const output = [];
    for (const entry of entries) await walkEntry(entry, "", output);
    return output;
  }

  function uploadOne(file) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/v1/upload");
      xhr.withCredentials = true;
      xhr.onload = () => {
        let payload = null;
        try {
          payload = JSON.parse(xhr.responseText || "{}");
        } catch (error) {
          payload = null;
        }
        if (xhr.status >= 200 && xhr.status < 300) resolve(payload);
        else reject(new Error((payload && payload.error) || ("HTTP " + xhr.status)));
      };
      xhr.onerror = () => reject(new Error("网络错误"));
      const form = new FormData();
      form.append("file", file, file.name);
      form.append("relativePath", relativeOf(file));
      xhr.send(form);
    });
  }

  async function uploadFiles(inputFiles) {
    if (busy) return;
    const files = inputFiles.filter((file) => file && !file.name.startsWith("."));
    if (!files.length) {
      setStatus("没有可上传的文件");
      return;
    }
    setBusy(true);
    if (errorsEl) errorsEl.innerHTML = "";
    if (progressEl) {
      progressEl.hidden = false;
      progressEl.value = 0;
    }
    let done = 0;
    let failed = 0;
    const queue = files.slice();

    const worker = async () => {
      while (queue.length) {
        const file = queue.shift();
        try {
          await uploadOne(file);
        } catch (error) {
          failed += 1;
          addError(relativeOf(file) + "：" + error.message);
        }
        done += 1;
        if (progressEl) progressEl.value = Math.round((done / files.length) * 100);
        setStatus(`已上传 ${done - failed} / ${files.length}，失败 ${failed}`);
      }
    };

    await Promise.all([worker(), worker()]);
    try {
      await fetch("/api/v1/rescan", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
    } catch (error) {
      /* the periodic scan will catch up */
    }
    setStatus(`完成：成功 ${files.length - failed}，失败 ${failed}`);
    setBusy(false);
    if (!failed) {
      setTimeout(() => window.location.reload(), 700);
    }
  }

  if (pickFiles) {
    pickFiles.addEventListener("click", () => fileInput.click());
  }
  if (pickFolder) {
    pickFolder.addEventListener("click", () => folderInput.click());
  }
  fileInput.addEventListener("change", () => {
    uploadFiles(Array.from(fileInput.files || []));
    fileInput.value = "";
  });
  folderInput.addEventListener("change", () => {
    uploadFiles(Array.from(folderInput.files || []));
    folderInput.value = "";
  });

  ["dragenter", "dragover"].forEach((eventName) => {
    dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropZone.classList.add("dragover");
    });
  });
  ["dragleave", "dragend"].forEach((eventName) => {
    dropZone.addEventListener(eventName, () => dropZone.classList.remove("dragover"));
  });
  dropZone.addEventListener("drop", async (event) => {
    event.preventDefault();
    dropZone.classList.remove("dragover");
    const files = await collectDropped(event.dataTransfer);
    uploadFiles(files);
  });
  // Prevent the browser from opening a dropped file outside the drop zone.
  ["dragover", "drop"].forEach((eventName) => {
    document.addEventListener(eventName, (event) => {
      if (!dropZone.contains(event.target)) event.preventDefault();
    });
  });

  document.querySelectorAll(".file-delete").forEach((button) => {
    button.addEventListener("click", async () => {
      const rel = button.getAttribute("data-rel");
      if (!rel || !window.confirm(`删除 ${rel}？`)) return;
      const encoded = rel.split("/").map(encodeURIComponent).join("/");
      try {
        const response = await fetch("/api/v1/files/" + encoded, {
          method: "DELETE",
          credentials: "same-origin",
        });
        if (!response.ok) {
          const payload = await response.json().catch(() => ({}));
          throw new Error(payload.error || "HTTP " + response.status);
        }
        const row = button.closest("tr");
        if (row) row.remove();
        if (window.MS) window.MS.toast("已删除");
      } catch (error) {
        if (window.MS) window.MS.toast("删除失败：" + error.message, true);
      }
    });
  });
})();
