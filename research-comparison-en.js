"use strict";

const explorer = document.querySelector("[data-method-explorer]");
if (explorer) {
  const methods = {
    original: "Original",
    ours: "Full method",
    base: "Base model",
    inretouch: "Original INRetouch implementation",
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
      name: "Peaks in mist", native: [4672, 7008], crop: [1950, 2720, 720],
      full: [1200, 1800], reference: [1024, 1536], errors: ["0.024304", "0.020156", "0.019014"],
      detailNote: "Where three peaks meet the mist. Look at how far the warm color reaches and how it transitions from lit rock into shadow.",
      fullNote: "Look at where warmth reaches the rock and mist, then compare the shaded rock and sky.",
    },
    architecture: {
      name: "Building and lawn", native: [7008, 4672], crop: [2650, 530, 1220],
      full: [1800, 1200], reference: [1536, 1024],
      detailNote: "Where the red wall meets the pale facade. Check the separation between materials, concrete texture, and sharp edges.",
      fullNote: "The reference emphasizes the red facade and green lawn and changes the brightness of the pale wall. Compare how each method handles these areas.",
    },
    cats: {
      name: "Cat and geometric wall", native: [4672, 7008], crop: [1820, 1270, 1440],
      full: [1200, 1800], reference: [1024, 1536],
      detailNote: "A cat on the wall among geometric panels. Compare fur, contours, and the relative brightness of the wall panels in black and white.",
      fullNote: "The two cats sit against different wall surfaces in different light. Compare their brightness and the wall tones after conversion to black and white.",
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
    range.setAttribute("aria-valuetext", `Left image covers ${range.value}%`);
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
      side.image.alt = `${scene.name}${detail ? " detail" : ""} · ${label}`;
      side.label.textContent = label;
    });
    [reference.width, reference.height] = scene.reference;
    reference.alt = `${scene.name} · Same-scene grading reference`;
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
