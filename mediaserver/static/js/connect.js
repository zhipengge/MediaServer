/* Render QR codes from data-qr attributes and wire copy buttons. */
(function () {
  "use strict";

  const QR = window.QRCode;
  document.querySelectorAll("[data-qr]").forEach((node) => {
    const text = node.getAttribute("data-qr");
    if (!text || !QR) return;
    new QR(node, {
      text,
      width: 220,
      height: 220,
      colorDark: "#0d1014",
      colorLight: "#ffffff",
      correctLevel: QR.CorrectLevel.M,
    });
  });

  document.querySelectorAll("[data-copy]").forEach((button) => {
    button.addEventListener("click", async () => {
      const text = button.getAttribute("data-copy") || "";
      try {
        await navigator.clipboard.writeText(text);
        if (window.MS) window.MS.toast("已复制");
      } catch (error) {
        if (window.MS) window.MS.toast("复制失败，请手动选择文本", true);
      }
    });
  });
})();
