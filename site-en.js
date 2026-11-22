"use strict";

const comparison = document.querySelector("[data-comparison]");
if (comparison) {
  const range = comparison.querySelector("input");
  const updateDivider = () => {
    comparison.style.setProperty("--position", `${range.value}%`);
    range.setAttribute("aria-valuetext", `Original covers ${range.value}%`);
  };
  range.addEventListener("input", updateDivider);
  updateDivider();
}

const gallery = document.querySelector("[data-gallery]");
if (gallery) {
  const list = gallery.querySelector(".gallery-nav");
  const links = Array.from(list.querySelectorAll("[data-panel-link]"));
  const panels = links.map(link => document.getElementById(link.hash.slice(1)));
  list.setAttribute("role", "tablist");

  function select(index) {
    links.forEach((link, i) => {
      link.setAttribute("aria-selected", String(i === index));
      link.tabIndex = i === index ? 0 : -1;
      panels[i].hidden = i !== index;
    });
  }

  links.forEach((link, index) => {
    link.id = `tab-${panels[index].id}`;
    link.setAttribute("role", "tab");
    link.setAttribute("aria-controls", panels[index].id);
    panels[index].setAttribute("role", "tabpanel");
    panels[index].setAttribute("aria-labelledby", link.id);
    link.addEventListener("click", event => {
      if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      select(index);
    });
    link.addEventListener("keydown", event => {
      const next = {
        ArrowRight: (index + 1) % links.length,
        ArrowLeft: (index + links.length - 1) % links.length,
        Home: 0,
        End: links.length - 1,
      }[event.key];
      if (event.key === " ") {
        event.preventDefault();
        select(index);
      } else if (next !== undefined) {
        event.preventDefault();
        select(next);
        links[next].focus();
      }
    });
  });
  const initialIndex = links.findIndex(link => link.hash === location.hash);
  select(initialIndex < 0 ? 0 : initialIndex);
}
