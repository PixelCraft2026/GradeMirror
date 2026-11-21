"use strict";

const explorer = document.querySelector("[data-method-explorer]");
if (explorer) {
  const methods = {
    original: "原片",
    ours: "本文完整组合",
    base: "本文基础模型",
    inretouch: "INRetouch作者原实现",
    "inretouch-point": "INRetouch 1×1",
    "sm-lut": "SM-LUT",
    bgu: "BGU",
    petal: "PETAL",
    bpam: "BPAM",
    "lp-curve": "LP-Curve",
    "ms-grad": "MS-Grad",
    "sa-lut": "SA-LUT",
    pot: "POT",
  };
  const scenes = {
    ridge: {
      name: "雾中岩峰", native: [4672, 7008], crop: [1950, 2720, 720],
      full: [1200, 1800], reference: [1024, 1536], errors: ["0.024304", "0.020156", "0.019014"],
      detailNote: "三座岩峰与云雾交界。观察暖色岩壁的覆盖范围，以及由受光面过渡到暗岩时的变化。",
      fullNote: "看暖色如何落在岩峰与雾气上，再比较暗岩和天空是否也随之变暖。",
    },
    architecture: {
      name: "建筑与草地", native: [7008, 4672], crop: [2650, 530, 1220],
      full: [1800, 1200], reference: [1536, 1024],
      detailNote: "红色外墙与浅色立面的交界。观察两种材质的颜色分离、混凝土纹理和锐利边缘。",
      fullNote: "参考强调红色外墙和绿色草地，并调整浅色墙面明度。比较各方法如何处理这些区域。",
    },
    cats: {
      name: "猫与几何墙面", native: [4672, 7008], crop: [1820, 1270, 1440],
      full: [1200, 1800], reference: [1024, 1536],
      detailNote: "墙头的猫与几何色块。比较转为黑白时的毛发层次、轮廓和墙面的灰阶关系。",
      fullNote: "两只猫分处不同墙面与光照。观察各方法转为黑白后的主体亮度和墙面灰阶。",
    },
  };

  const find = selector => explorer.querySelector(selector);
  const sceneInput = find("[data-scene]");
  const viewInput = find("[data-view]");
  const sides = ["left", "right"].map(side => ({
    select: find(`[data-side="${side}"]`),
    image: find(`[data-photo="${side}"]`),
    label: find(`[data-label="${side}"]`),
  }));
  const frame = find("[data-frame]");
  const range = frame.querySelector("input");
  const reference = find("[data-reference]");
  const visibleImages = [...sides.map(side => side.image), reference];
  const region = find("[data-region]");
  const regionNote = find("[data-region-note]");
  const panel = find("[data-method-panel]");
  const grid = find("[data-method-grid]");
  const loadError = find("[data-load-error]");
  const errors = [...explorer.querySelectorAll("[data-error]")];
  const asset = (scene, method, view) => `assets/comparisons/${scene}-${method}-${view}.webp`;
  const available = (scene, method) => Object.hasOwn(methods, method) && (
    scene === "ridge" || (method !== "pot" && method !== "ours")
  );

  for (const [id, scene] of Object.entries(scenes)) sceneInput.add(new Option(scene.name, id));
  for (const side of sides) {
    for (const [id, label] of Object.entries(methods)) side.select.add(new Option(label, id));
  }
  sides[0].select.value = "inretouch";
  sides[1].select.value = "ours";

  function updateDivider() {
    frame.style.setProperty("--position", `${range.value}%`);
    range.setAttribute("aria-valuetext", `左侧占 ${range.value}%`);
  }

  let panelView = "";
  function drawPanel(sceneId, view, dimensions) {
    if (!panel.open || panelView === `${sceneId}-${view}`) return;
    const scene = scenes[sceneId];
    grid.replaceChildren(...Object.entries(methods).filter(([id]) => available(sceneId, id)).map(([id, label]) => {
      const figure = document.createElement("figure");
      const link = document.createElement("a");
      const image = new Image(...dimensions);
      const caption = document.createElement("figcaption");
      image.loading = "lazy";
      image.alt = `${scene.name} · ${label}`;
      image.src = asset(sceneId, id, view);
      link.href = image.getAttribute("src");
      link.target = "_blank";
      link.rel = "noopener";
      link.append(image);
      caption.textContent = label;
      figure.append(link, caption);
      return figure;
    }));
    panelView = `${sceneId}-${view}`;
  }

  let currentView;
  let updateId = 0;
  async function update() {
    const request = ++updateId;
    const sceneId = sceneInput.value;
    const defaults = ["inretouch", sceneId === "ridge" ? "ours" : "base"];
    sides.forEach((side, index) => {
      for (const option of side.select.options) option.hidden = option.disabled = !available(sceneId, option.value);
      if (!available(sceneId, side.select.value)) side.select.value = defaults[index];
    });
    const scene = scenes[sceneId];
    const view = viewInput.value;
    const detail = view === "detail";
    const [x, y, size] = scene.crop;
    const [nativeWidth, nativeHeight] = scene.native;
    const dimensions = detail ? [size, size] : scene.full;
    const chosen = sides.map(side => side.select.value);
    const urls = [...chosen.map(id => asset(sceneId, id, view)), asset(sceneId, "reference", "full")];
    const pending = urls.filter((url, index) => visibleImages[index].getAttribute("src") !== url);

    frame.setAttribute("aria-busy", "true");
    loadError.hidden = true;
    try {
      await Promise.all(pending.map(url => {
        const image = new Image();
        image.src = url;
        return image.decode();
      }));
    } catch {
      if (request === updateId) {
        frame.removeAttribute("aria-busy");
        loadError.hidden = false;
      }
      return;
    }
    if (request !== updateId) return;

    frame.style.setProperty("--photo-ratio", `${dimensions[0]} / ${dimensions[1]}`);
    frame.dataset.shape = detail ? "square" : dimensions[0] < dimensions[1] ? "portrait" : "landscape";
    sides.forEach((side, index) => {
      const label = methods[chosen[index]];
      [side.image.width, side.image.height] = dimensions;
      side.image.alt = `${scene.name}${detail ? "局部" : ""} · ${label}`;
      side.label.textContent = label;
    });
    [reference.width, reference.height] = scene.reference;
    reference.alt = `${scene.name}的同景调色参考`;
    urls.forEach((url, index) => {
      if (visibleImages[index].getAttribute("src") !== url) visibleImages[index].src = url;
    });
    region.hidden = !detail;
    regionNote.hidden = !detail;
    region.style.inset = `${100 * y / nativeHeight}% ${(1 - (x + size) / nativeWidth) * 100}% ${(1 - (y + size) / nativeHeight) * 100}% ${100 * x / nativeWidth}%`;
    find("[data-reference-size]").textContent = scene.reference.join(" × ");
    find("[data-scene-caption]").textContent = detail ? scene.detailNote : scene.fullNote;
    find("[data-metrics]").hidden = !scene.errors;
    find("[data-base-only]").hidden = Boolean(scene.errors);
    if (scene.errors) errors.forEach((cell, index) => { cell.textContent = scene.errors[index]; });
    frame.removeAttribute("aria-busy");
    currentView = [sceneId, view, dimensions];
    drawPanel(...currentView);
  }

  for (const select of [sceneInput, viewInput, ...sides.map(side => side.select)]) {
    select.addEventListener("change", update);
  }
  panel.addEventListener("toggle", () => { if (currentView) drawPanel(...currentView); });
  range.addEventListener("input", updateDivider);
  updateDivider();
  find("[data-controls]").hidden = false;
  panel.hidden = false;
  update();
}
