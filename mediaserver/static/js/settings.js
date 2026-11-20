/* Settings page: trigger a rescan and poll until the scan finishes. */
(function () {
  "use strict";

  const button = document.getElementById("rescan");
  const status = document.getElementById("rescan-status");
  if (!button) return;

  async function waitForScan() {
    for (let i = 0; i < 120; i += 1) {
      try {
        const payload = await window.MS.fetchJSON("/api/v1/status");
        if (!payload.scan || !payload.scan.scanning) {
          if (status) status.textContent = `完成，共 ${payload.counts.total} 个文件`;
          window.MS.toast("扫描完成");
          return;
        }
      } catch (error) {
        if (status) status.textContent = `查询状态失败：${error.message}`;
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
    if (status) status.textContent = "扫描仍在进行，可稍后刷新页面";
  }

  button.addEventListener("click", async () => {
    button.disabled = true;
    if (status) status.textContent = "正在扫描…";
    try {
      await window.MS.fetchJSON("/api/v1/rescan", { method: "POST", body: {} });
      await waitForScan();
    } catch (error) {
      if (status) status.textContent = `失败：${error.message}`;
    } finally {
      button.disabled = false;
    }
  });
})();
