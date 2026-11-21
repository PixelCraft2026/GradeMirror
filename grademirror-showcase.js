(() => {
  "use strict";

  const hero = document.querySelector("[data-hero]");
  const stage = hero?.querySelector(".hero-stage");
  const methodTitle = document.querySelector("#method-title");
  const masthead = document.querySelector(".masthead");
  if (hero && stage && methodTitle && masthead) {
    const motion = window.matchMedia("(prefers-reduced-motion: no-preference)");
    const limit = value => Math.max(0, Math.min(1, value));
    const ease = value => value * value * (3 - 2 * value);
    let frame = 0;

    function updateReveal() {
      frame = 0;
      if (!motion.matches) return;
      const height = stage.offsetHeight;
      const travel = hero.offsetHeight - height;
      if (travel <= 0) return;

      const distance = -hero.getBoundingClientRect().top;
      const progress = limit(distance / (travel * 0.86));
      // End the fade as the next heading reaches the area below the fixed navigation.
      const fadeStart = travel * 0.94;
      const titleTop = methodTitle.getBoundingClientRect().top;
      const fadeEnd = distance + titleTop - masthead.offsetHeight - 36;
      const fade = limit((distance - fadeStart) / (fadeEnd - fadeStart));
      stage.style.setProperty("--reveal", (ease(progress) * 100).toFixed(2) + "%");
      stage.style.setProperty("--divider-opacity", Math.min(1, progress * 12, (1 - progress) * 12).toFixed(2));
      stage.style.setProperty("--whiteout", String(fade * fade));
    }

    function queueReveal() {
      if (!frame && motion.matches) frame = requestAnimationFrame(updateReveal);
    }

    function updateMotion() {
      hero.toggleAttribute("data-scroll-reveal", motion.matches);
      if (motion.matches) {
        updateReveal();
      } else {
        stage.style.removeProperty("--reveal");
        stage.style.removeProperty("--divider-opacity");
        stage.style.removeProperty("--whiteout");
      }
    }

    motion.addEventListener("change", updateMotion);
    window.addEventListener("scroll", queueReveal, {passive: true});
    window.addEventListener("resize", queueReveal);
    updateMotion();
  }

  const experiments = {
    same: {
      summary: "相同原片、相同的 ChatGPT 同景调色稿，两款软件分别仿色。",
      reading: "在这张样例里，GradeMirror 的暖色岩面和蓝灰雾影更接近同景参考的冷暖关系。",
      photos: [
        {file: "reference", title: "共同参考", note: "ChatGPT 生成的同景稿", alt: "雾中岩峰的共同调色参考，由 ChatGPT 生成"},
        {file: "pixel-same", title: "像素蛋糕", note: "使用同一张同景稿", alt: "像素蛋糕使用共同同景稿的仿色结果"},
        {file: "result", title: "GradeMirror", note: "使用同一张同景稿", alt: "GradeMirror 使用共同同景稿的仿色结果"},
      ],
    },
    style: {
      summary: "从同一张异景风格图出发。像素蛋糕直接仿色，另一条流程由 ChatGPT 先做同景稿，再由 GradeMirror 仿色。",
      reading: "在这组流程对照里，像素蛋糕的成片整体偏橙，GradeMirror 的暖橙亮部与蓝灰暗部保留了更清楚的冷暖层次。",
      photos: [
        {file: "source", title: "共同原片", note: "雾中岩峰原片", alt: "两条调色流程使用的共同雾中岩峰原片"},
        {file: "pixel-style", title: "像素蛋糕", note: "用异景风格图直接仿色", alt: "像素蛋糕使用异景风格图直接仿色的结果"},
        {file: "result", title: "ChatGPT ＋ GradeMirror", note: "先生成同景稿，再仿色", alt: "ChatGPT 生成同景调色稿后由 GradeMirror 仿色的结果"},
      ],
    },
  };

  const viewer = document.querySelector("[data-compare]");
  if (viewer) {
    const controls = viewer.querySelector("[data-controls]");
    const caseButtons = [...controls.querySelectorAll("[data-case]")];
    const viewButtons = [...controls.querySelectorAll("[data-view]")];
    const grid = viewer.querySelector("[data-grid]");
    const summary = viewer.querySelector("[data-summary]");
    const reading = viewer.querySelector("[data-reading]");
    const status = viewer.querySelector("[data-status]");
    const error = viewer.querySelector("[data-error]");
    const tiles = [...grid.querySelectorAll("figure")].map(tile => ({
      full: tile.querySelector(".full-photo"),
      detail: tile.querySelector(".detail-photo"),
      link: tile.querySelector("a"),
      title: tile.querySelector("strong"),
      note: tile.querySelector("figcaption span"),
    }));
    let request = 0;
    const initial = {condition: "style", view: "full"};
    let state = initial;
    let requested = initial;
    const imagePath = (photo, view) => "assets/showcase/" + photo.file + "-" + view + ".webp";

    async function select(condition, view) {
      requested = {condition, view};
      const token = ++request;
      const experiment = experiments[condition];
      const detail = view === "detail";
      const changedCondition = condition !== state.condition;
      const images = experiment.photos.map(photo => ({
        full: imagePath(photo, "full"),
        detail: imagePath(photo, "detail"),
      }));
      const pending = [];
      if (changedCondition) pending.push(...images.map(image => image.full));
      if (detail) pending.push(...images.map(image => image.detail));
      grid.setAttribute("aria-busy", "true");
      grid.classList.toggle("is-switching", changedCondition);
      error.hidden = true;
      try {
        await Promise.all(pending.map(path => {
          const image = new Image();
          image.src = path;
          return image.decode();
        }));
        if (token !== request) return;
        tiles.forEach((tile, i) => {
          const photo = experiment.photos[i];
          if (changedCondition) {
            tile.full.src = images[i].full;
            tile.detail.src = images[i].detail;
            tile.title.textContent = photo.title;
            tile.note.textContent = photo.note;
          }
          tile.link.href = images[i][view];
          tile.full.alt = photo.alt + (detail ? "。中央山峰同位置局部" : "");
        });
        grid.classList.toggle("is-detail", detail);
        if (changedCondition) {
          summary.textContent = experiment.summary;
          reading.textContent = experiment.reading;
        }
        caseButtons.forEach(button => button.setAttribute("aria-pressed", String(button.dataset.case === condition)));
        viewButtons.forEach(button => button.setAttribute("aria-pressed", String(button.dataset.view === view)));
        const caseLabel = caseButtons.find(button => button.dataset.case === condition).textContent.trim();
        status.textContent = caseLabel + "，已切换至" + (detail ? "山峰局部" : "全图") + "。";
        state = requested;
      } catch {
        if (token === request) {
          error.hidden = false;
          status.textContent = error.textContent;
          requested = state;
        }
      } finally {
        if (token === request) {
          grid.removeAttribute("aria-busy");
          grid.classList.remove("is-switching");
        }
      }
    }

    caseButtons.forEach(button => button.addEventListener("click", () => select(button.dataset.case, requested.view)));
    viewButtons.forEach(button => button.addEventListener("click", () => select(requested.condition, button.dataset.view)));
    controls.hidden = false;
  }

})();
