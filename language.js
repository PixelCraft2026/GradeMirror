(() => {
  "use strict";

  const preferenceKey = "grademirror.preferredLanguage";
  const current = new URL(window.location.href);
  const explicitLanguage = current.searchParams.get("lang");
  const pageLanguage = document.documentElement.lang;
  const englishPage = document.currentScript.dataset.englishPage;

  if (explicitLanguage === pageLanguage) {
    try {
      window.localStorage.setItem(preferenceKey, pageLanguage);
      current.searchParams.delete("lang");
      window.history.replaceState(window.history.state, "", current);
    } catch {
      // Keep the explicit URL when storage or history is unavailable.
    }
    return;
  }

  // An English URL already names a specific edition of the page.
  if (!englishPage) return;

  let preferredLanguage;
  try {
    preferredLanguage = window.localStorage.getItem(preferenceKey);
  } catch {
    // Use browser preferences when storage is unavailable.
  }
  if (preferredLanguage !== "zh-CN" && preferredLanguage !== "en") {
    const preferences = navigator.languages?.length ? navigator.languages : [navigator.language];
    const supported = preferences.find(language => /^(zh|en)(-|$)/i.test(language));
    preferredLanguage = /^zh(-|$)/i.test(supported) ? "zh-CN" : "en";
  }

  if (preferredLanguage === "en") {
    const target = new URL(englishPage, current);
    target.search = current.search;
    target.hash = current.hash;
    window.location.replace(target.href);
  }
})();
