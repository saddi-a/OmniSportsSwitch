// Phase 0 spike: can we read real pixels from this page's <video> elements?
// Paste into the DevTools console (F12 > Console) on a page that is playing a stream.
// If the console blocks pasting, type "allow pasting" first and press Enter.
(() => {
  const videos = [...document.querySelectorAll("video")];
  console.log(`Found ${videos.length} <video> element(s) on ${location.hostname}`);

  videos.forEach((v, i) => {
    const info = {
      index: i,
      size: `${v.videoWidth}x${v.videoHeight}`,
      playing: !v.paused && v.readyState >= 2,
      drm_mediaKeys: v.mediaKeys !== null,
    };

    if (!v.videoWidth) {
      console.log(info, "-> no frame available (not loaded or hidden)");
      return;
    }

    try {
      const c = document.createElement("canvas");
      c.width = 320;
      c.height = Math.round((320 * v.videoHeight) / v.videoWidth);
      const ctx = c.getContext("2d", { willReadFrequently: true });
      ctx.drawImage(v, 0, 0, c.width, c.height);
      const px = ctx.getImageData(0, 0, c.width, c.height).data;

      let sum = 0, sumSq = 0, n = px.length / 4;
      for (let p = 0; p < px.length; p += 4) {
        const lum = 0.299 * px[p] + 0.587 * px[p + 1] + 0.114 * px[p + 2];
        sum += lum;
        sumSq += lum * lum;
      }
      const mean = sum / n;
      const std = Math.sqrt(sumSq / n - mean * mean);
      const verdict = std < 2 ? "BLANK (DRM blocked)" : "REAL PIXELS (capture works)";
      console.log(info, `mean=${mean.toFixed(1)} std=${std.toFixed(1)} ->`, verdict);
    } catch (e) {
      console.log(info, "-> drawImage/getImageData threw:", e.name, e.message);
    }
  });
})();
