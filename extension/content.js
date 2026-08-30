(() => {
  "use strict";

  const MAX_TEXT = 50000;
  const MAX_ITEMS = 1000;
  const clean = (value, maximum = 2000) =>
    String(value || "").replace(/\s+/g, " ").trim().slice(0, maximum);
  const queryDeep = (selector) => {
    const results = [];
    const visit = (root) => {
      results.push(...root.querySelectorAll(selector));
      for (const element of root.querySelectorAll("*")) {
        if (element.shadowRoot) visit(element.shadowRoot);
      }
    };
    visit(document);
    return [...new Set(results)];
  };
  const visible = (element) => {
    const style = getComputedStyle(element);
    const box = element.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" &&
      style.opacity !== "0" && box.width > 0 && box.height > 0 &&
      element.getAttribute("aria-hidden") !== "true";
  };
  const labelledBy = (element) => clean(
    (element.getAttribute("aria-labelledby") || "").split(/\s+/)
      .map((id) => document.getElementById(id)?.innerText || "").join(" ")
  );
  const labelFor = (element) => {
    if (element.id) {
      const root = element.getRootNode();
      const label = root.querySelector?.(`label[for="${CSS.escape(element.id)}"]`);
      if (label) return clean(label.innerText);
    }
    return clean(element.getAttribute("aria-label")) || labelledBy(element) ||
      clean(element.closest("label")?.innerText) ||
      clean(element.getAttribute("placeholder")) || clean(element.getAttribute("title"));
  };
  const contextFor = (element) => {
    const container = element.closest(
      "label, fieldset, [role=group], [role=radiogroup], .form-group, .field, .input-group"
    ) || element.parentElement;
    return clean(container?.innerText, 1500) || null;
  };
  const purposeFor = (text) => {
    const normalized = clean(text).toLowerCase();
    if (/suostu|markkinointi|yhteyttä|uutiskirje|consent|marketing/.test(normalized)) return "consent";
    if (/tietosuoja|privacy|henkilötieto|data protection/.test(normalized)) return "privacy";
    if (/käyttöeh|osallistumiseh|kilpailun säänn|arvonnan säänn|terms|rules/.test(normalized)) return "rules";
    return "generic";
  };

  let nextRef = 1;
  const reference = () => `e${nextRef++}`;
  const nativeFields = queryDeep("input, select, textarea");
  const customFields = queryDeep(
    "[role=checkbox], [role=radio], [role=combobox], [role=listbox], " +
    "[role=switch], [role=textbox], [contenteditable=true]"
  ).filter((element) => !nativeFields.includes(element));
  const fields = [...nativeFields, ...customFields].filter((element) => {
    if (visible(element)) return true;
    const type = clean(element.type || element.getAttribute("role"), 50).toLowerCase();
    // Styled checkboxes and radios commonly hide the native control while
    // keeping a visible associated label. Preserve that semantic control.
    return ["checkbox", "radio"].includes(type) && Boolean(labelFor(element));
  }).slice(0, MAX_ITEMS)
    .map((element) => {
      const role = clean(element.getAttribute("role"), 50).toLowerCase();
      const type = clean(element.type || role || element.tagName, 50).toLowerCase();
      const checkable = ["checkbox", "radio", "switch"].includes(type);
      const checkedAttribute = element.getAttribute("aria-checked");
      const label = labelFor(element);
      const context = contextFor(element);
      const options = element.tagName === "SELECT"
        ? [...element.options].slice(0, 200).map((option) => clean(option.text, 500))
        : [];
      return {
        element_ref: reference(), frame_url: location.href,
        tag: element.tagName.toLowerCase(), field_type: type, role: role || null,
        name: clean(element.getAttribute("name"), 500) || null,
        label: label || null, context,
        purpose: purposeFor(`${label || ""} ${context || ""}`),
        required: Boolean(element.required || element.getAttribute("aria-required") === "true"),
        disabled: Boolean(element.disabled || element.getAttribute("aria-disabled") === "true"),
        checked: checkable
          ? (checkedAttribute === null ? Boolean(element.checked) : checkedAttribute === "true")
          : null,
        value_present: nativeFields.includes(element) ? Boolean(element.value) : false,
        options
      };
    });
  const links = queryDeep("a[href], [role=link]").filter(visible).slice(0, 2000)
    .map((element) => {
      const text = clean(element.innerText || element.getAttribute("aria-label"));
      return {
        element_ref: reference(), frame_url: location.href, text,
        url: clean(element.href || element.getAttribute("data-href"), 4000),
        purpose: purposeFor(`${text} ${element.getAttribute("title") || ""}`)
      };
    });
  const buttons = queryDeep(
    "button, input[type=button], input[type=submit], [role=button], a:not([href])"
  )
    .filter(visible).slice(0, MAX_ITEMS).map((element) => {
      const text = clean(element.innerText || element.value || element.getAttribute("aria-label"));
      return {
        element_ref: reference(), frame_url: location.href, text,
        button_type: clean(element.type || element.getAttribute("role") || "button", 50),
        disabled: Boolean(element.disabled || element.getAttribute("aria-disabled") === "true"),
        purpose: purposeFor(`${text} ${contextFor(element) || ""}`)
      };
    });
  const text = clean(document.body?.innerText, MAX_TEXT);
  const challengeText = `${document.title} ${text}`.toLowerCase();
  const manualVerification = [
    "just a moment", "verify you are human", "checking your browser",
    "performing security verification", "captcha", "turnstile",
    "vahvista, että olet ihminen", "tarkistetaan selaintasi"
  ].some((marker) => challengeText.includes(marker));
  return {
    url: location.href, title: clean(document.title), visible_text: text,
    fields, links, buttons,
    iframe_urls: queryDeep("iframe[src]").map((frame) => clean(frame.src, 4000))
      .filter((url) => url.startsWith("http://") || url.startsWith("https://")),
    manual_verification_required: manualVerification
  };
})();
