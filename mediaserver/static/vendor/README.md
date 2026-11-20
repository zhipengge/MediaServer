# Vendored assets

## hls.js 1.7.4 (light build)

- File: `hls.min.js` (from `hls.js@1.7.4/dist/hls.light.min.js`)
- License: Apache-2.0, see `hls.LICENSE.txt`
- Source: <https://github.com/video-dev/hls.js>
- Why vendored: the web client must work on a LAN with no internet access, and the
  Content-Security-Policy only allows `script-src 'self'`. Safari plays HLS natively;
  Chrome/Firefox use this build.
- Update procedure: replace the file with the same build from a new release, update
  the version above, and re-run the browser smoke test (`/watch/<id>` on a
  non-H.264 file).

## QRCode.js 1.0.0

- File: `qrcode.min.js` (from `qrcodejs@1.0.0/qrcode.min.js`)
- License: MIT, see `qrcode.LICENSE.txt`
- Source: <https://github.com/davidshimjs/qrcodejs>
- Why vendored: `/connect` renders QR codes client-side, so no data (including
  one-time login links) is sent to a third-party QR image service, and the page
  works with no internet access.
- Update procedure: replace `qrcode.min.js`, update the version above, and verify
  `/connect` still renders both the web URL and the manifest URL.
