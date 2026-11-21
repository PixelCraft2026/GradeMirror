"use strict";

const photoGallery = document.querySelector("[data-photo-gallery]");
if (photoGallery) {
  const scenes = {
    ridge: { title: "雾中山峰", position: "49%", original: "assets/grademirror/ridge-original.webp",
      result: "assets/grademirror/ridge-result.webp", reference: "assets/grademirror/ridge-reference.webp" },
    cloud: { title: "云海与崖壁", position: "43%", original: "assets/grademirror/cloud-original.webp",
      result: "assets/grademirror/cloud-result.webp", reference: "assets/grademirror/cloud-reference.webp" },
    night: { title: "银河", position: "43%", original: "assets/grademirror/night-original.webp",
      result: "assets/grademirror/night-result.webp", reference: "assets/grademirror/night-reference.webp" },
  };
  const viewLabels = { original: "原图", reference: "调色参考", result: "仿色结果" };
  const selectLeft = photoGallery.dataset.compareSide === "left";
  const figure = photoGallery.querySelector("figure");
  const frame = photoGallery.querySelector("[data-compare-frame]");
  const images = [frame.querySelector("[data-original]"), frame.querySelector("[data-adjusted]")];
  const divider = frame.querySelector("[data-photo-range]");
  const view = photoGallery.querySelector("[data-compare-view]");
  const buttons = [...photoGallery.querySelectorAll("[data-scene]")];
  const caption = document.querySelector("#gm-compare-caption");
  const selectedLabel = frame.querySelector("[data-photo-label]");
  const leftLabel = frame.querySelector(".gm-original-tag");
  let sceneKey = "ridge";
  let renderedView = view.value;
  let request = 0;
  let animation;

  function moveDivider() {
    frame.style.setProperty("--gm-split", divider.value + "%");
    divider.setAttribute("aria-valuetext", leftLabel.textContent + "占 " + divider.value + "%");
  }

  async function updatePhotos() {
    const token = ++request;
    const scene = scenes[sceneKey];
    const selectedView = view.value;
    const displayedViews = selectLeft ? [selectedView, "result"] : ["original", selectedView];
    const nextImages = displayedViews.map(key => {
      const image = new Image();
      image.src = scene[key];
      return image;
    });
    try {
      await Promise.all(nextImages.map(image => image.decode()));
    } catch {
      if (token === request) {
        const renderedButton = buttons.find(button => button.getAttribute("aria-pressed") === "true");
        sceneKey = renderedButton.dataset.scene;
        view.value = renderedView;
        caption.textContent = "图像暂未读到，请重试。";
      }
      return;
    }
    if (token !== request) return;
    images.forEach((element, index) => {
      const preview = nextImages[index];
      element.width = preview.naturalWidth;
      element.height = preview.naturalHeight;
      element.alt = scene.title + viewLabels[displayedViews[index]];
      element.src = preview.src;
    });
    renderedView = selectedView;
    selectedLabel.textContent = viewLabels[selectedView];
    moveDivider();
    const referenceWidth = nextImages[displayedViews.indexOf("reference")]?.naturalWidth;
    caption.textContent = scene.title + (referenceWidth ? " · 调色参考原宽 " + referenceWidth + " px" : " · 高清研究样例");
    if (referenceWidth) frame.style.setProperty("--gm-view-max", referenceWidth + "px");
    else frame.style.removeProperty("--gm-view-max");
    frame.style.setProperty("--gm-pos", scene.position);
    buttons.forEach(button => button.setAttribute("aria-pressed", String(button.dataset.scene === sceneKey)));
    figure.classList.add("is-changing");
    clearTimeout(animation);
    animation = setTimeout(() => figure.classList.remove("is-changing"), 270);
  }

  buttons.forEach(button => button.addEventListener("click", () => {
    sceneKey = button.dataset.scene;
    updatePhotos();
  }));
  view.addEventListener("change", updatePhotos);
  divider.addEventListener("input", moveDivider);
  moveDivider();
}

const engineBoard = document.querySelector("[data-engine-board]");
if (engineBoard) {
  const modeLabel = engineBoard.querySelector("[data-mode-label]");
  const lastTitle = engineBoard.querySelector("[data-engine-title]");
  const lastSubline = engineBoard.querySelector("[data-engine-output]");
  const fineExtension = engineBoard.querySelector("[data-fine-extension]");
  const buttons = [...document.querySelectorAll("[data-engine-mode]")];
  const descriptions = [...document.querySelectorAll("[data-mode-copy]")];

  function showMode(mode) {
    const fine = mode === "fine";
    engineBoard.dataset.mode = mode;
    modeLabel.textContent = fine ? "精细路线" : "快速路线";
    if (lastTitle) lastTitle.textContent = fine ? "基础结果 O" : "原尺寸结果";
    if (lastSubline) lastSubline.textContent = fine ? "送入精细后处理 ↓" : "基础仿色 O";
    fineExtension.hidden = !fine;
    buttons.forEach(button => button.setAttribute("aria-pressed", String(button.dataset.engineMode === mode)));
    descriptions.forEach(description => { description.hidden = description.dataset.modeCopy !== mode; });
  }

  buttons.forEach(button => button.addEventListener("click", () => showMode(button.dataset.engineMode)));
  if ("IntersectionObserver" in window) {
    const observer = new IntersectionObserver(([entry]) => {
      engineBoard.classList.toggle("gm-inview", entry.isIntersecting);
    }, { threshold: .15 });
    observer.observe(engineBoard);
  }
}

const copyButton = document.querySelector("[data-copy-code]");
if (copyButton) {
  copyButton.addEventListener("click", async () => {
    const code = document.querySelector("#gm-cli-example");
    const status = document.querySelector("[data-copy-status]");
    try {
      await navigator.clipboard.writeText(code.textContent);
      status.textContent = "命令已复制";
    } catch {
      const range = document.createRange();
      range.selectNodeContents(code);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      status.textContent = "命令已选中，可以手动复制";
    }
  });
}
