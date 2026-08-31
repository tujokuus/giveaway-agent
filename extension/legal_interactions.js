(() => {
  "use strict";

  const clean = (value, maximum = 2000) =>
    String(value || "").replace(/\s+/g, " ").trim().slice(0, maximum);
  const purposeFor = (text) => {
    const normalized = clean(text).toLowerCase();
    if (/suostu|markkinointi|uutiskirje|consent|marketing/.test(normalized)) return "consent";
    if (/tietosuoja|privacy|henkilötieto|data protection/.test(normalized)) return "privacy";
    if (/käyttöeh|osallistumiseh|kilpailun säänn|arvonnan säänn|terms|rules/.test(normalized)) return "rules";
    return "generic";
  };
  const visible = (element) => {
    const style = getComputedStyle(element);
    const box = element.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" &&
      style.opacity !== "0" && box.width > 0 && box.height > 0 &&
      element.getAttribute("aria-hidden") !== "true";
  };
  const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

  return (async () => {
    const selectors = "button, input[type=button], [role=button], a:not([href])";
    const candidates = [...document.querySelectorAll(selectors)].filter((element) => {
      if (!visible(element) || element.disabled || element.getAttribute("aria-disabled") === "true") {
        return false;
      }
      const declaredType = clean(element.getAttribute("type"), 50).toLowerCase();
      const associatedForm = Boolean(element.form || element.closest("form"));
      const isSubmit = associatedForm && (
        declaredType === "submit" || (element.tagName === "BUTTON" && !declaredType)
      );
      return !isSubmit;
    });

    const selected = [];
    for (const documentType of ["rules", "privacy"]) {
      const element = candidates.find((candidate) => {
        const text = clean(
          candidate.innerText || candidate.value || candidate.getAttribute("aria-label")
        );
        const context = clean(candidate.parentElement?.innerText, 1000);
        const directPurpose = purposeFor(text);
        return directPurpose === documentType || (
          directPurpose === "generic" && purposeFor(context) === documentType
        );
      });
      if (element) selected.push({ element, documentType });
    }

    const interactions = [];
    for (const { element, documentType } of selected.slice(0, 2)) {
      const text = clean(
        element.innerText || element.value || element.getAttribute("aria-label")
      );
      const beforeText = clean(document.body?.innerText, 50000);
      try {
        element.click();
        await sleep(1200);
        const afterText = clean(document.body?.innerText, 50000);
        interactions.push({
          frame_url: location.href,
          text,
          document_type: documentType,
          result: beforeText === afterText ? "clicked_no_readable_change" : "content_revealed"
        });
      } catch (error) {
        interactions.push({
          frame_url: location.href,
          text,
          document_type: documentType,
          result: "click_failed"
        });
      }
    }
    return { url: location.href, interactions };
  })();
})();
