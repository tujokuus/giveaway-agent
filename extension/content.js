(() => {
  "use strict";

  const MAX_TEXT = 50000;
  const MAX_ITEMS = 1000;
  const clean = (value, maximum = 2000) =>
    String(value || "").replace(/\s+/g, " ").trim().slice(0, maximum);
  const cleanMultiline = (value, maximum = MAX_TEXT) =>
    String(value || "").split(/\r?\n/).map((line) => clean(line, 5000))
      .filter(Boolean).join("\n").slice(0, maximum);
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
  const legalTypesFor = (text) => {
    const normalized = clean(text).toLowerCase();
    const types = [];
    if (/tietosuoja|privacy|henkilötieto|data protection/.test(normalized)) types.push("privacy");
    if (/käyttöeh|osallistumiseh|kilpailun säänn|arvonnan säänn|terms|rules/.test(normalized)) types.push("rules");
    return types;
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
      const declaredType = clean(element.getAttribute("type"), 50).toLowerCase();
      const associatedForm = Boolean(element.form || element.closest("form"));
      const nativeSubmit = associatedForm && (
        declaredType === "submit"
        || (element.tagName === "BUTTON" && !declaredType)
      );
      const buttonType = nativeSubmit
        ? "submit"
        : clean(element.getAttribute("role") || declaredType || "button", 50);
      return {
        element_ref: reference(), frame_url: location.href, text,
        button_type: buttonType,
        disabled: Boolean(element.disabled || element.getAttribute("aria-disabled") === "true"),
        purpose: purposeFor(`${text} ${contextFor(element) || ""}`)
      };
    });
  const seenBlocks = new Set();
  const textBlocks = queryDeep(
    "h1, h2, h3, h4, h5, h6, p, li, legend, [role=heading]"
  ).filter(visible).map((element) => {
    const text = clean(element.innerText, 5000);
    const key = text.toLowerCase();
    if (!text || seenBlocks.has(key)) return null;
    seenBlocks.add(key);
    return {
      element_ref: reference(), frame_url: location.href,
      tag: element.tagName.toLowerCase(), text,
      visibility: "visible", purpose: purposeFor(text)
    };
  }).filter(Boolean).slice(0, 2000);

  const hiddenSeen = new Set();
  const legalControllerSelector = [
    "[aria-controls]", "[data-bs-target]", "[data-target]", "[data-modal]",
    "[data-dialog]", "[data-modal-target]", "[data-dialog-target]",
    "[data-micromodal-trigger]", "a[href^='#']"
  ].join(", ");
  const controlledLegalSections = queryDeep(legalControllerSelector).flatMap((controller) => {
    const controllerText = clean(
      controller.innerText || controller.value || controller.getAttribute("aria-label")
    );
    const context = clean(
      controller.closest("label")?.innerText || controller.parentElement?.innerText,
      1000
    );
    const directPurpose = purposeFor(controllerText);
    if (
      !["rules", "privacy"].includes(directPurpose)
      && !["rules", "privacy"].includes(purposeFor(context))
    ) return [];
    const root = controller.getRootNode();
    const selectors = [];
    for (const id of (controller.getAttribute("aria-controls") || "").split(/\s+/)) {
      if (id) selectors.push(`#${CSS.escape(id)}`);
    }
    for (const attribute of [
      "data-bs-target", "data-target", "data-modal", "data-dialog",
      "data-modal-target", "data-dialog-target", "data-micromodal-trigger", "href"
    ]) {
      const value = clean(controller.getAttribute(attribute), 500);
      if (value.startsWith("#") && value.length > 1) selectors.push(value);
      else if (/^[A-Za-z][\w:-]*$/.test(value)) selectors.push(`#${CSS.escape(value)}`);
    }
    return selectors.map((selector) => {
      try {
        return root.querySelector?.(selector);
      } catch (_error) {
        return null;
      }
    }).filter(Boolean);
  });
  const legalSectionElements = [...new Set([
    ...queryDeep("dialog, [role=dialog], template, details, [hidden], [aria-hidden=true]"),
    ...controlledLegalSections
  ])];
  const embeddedLegalSections = legalSectionElements.map((element) => {
    const rawText = element.tagName === "TEMPLATE"
      ? element.content?.textContent : element.textContent;
    const text = cleanMultiline(rawText, 30000);
    const documentTypes = legalTypesFor(text);
    const key = text.toLowerCase();
    if (text.length < 40 || !documentTypes.length || hiddenSeen.has(key)) return null;
    hiddenSeen.add(key);
    const isVisible = element.tagName === "DETAILS"
      ? Boolean(element.open) && visible(element)
      : visible(element);
    return {
      element_ref: reference(), frame_url: location.href,
      document_types: documentTypes, text, visibility: isVisible ? "visible" : "hidden"
    };
  }).filter(Boolean).slice(0, 50);

  const text = cleanMultiline(document.body?.innerText, MAX_TEXT);
  const challengeText = `${document.title} ${text}`.toLowerCase();
  const manualVerification = [
    "just a moment", "verify you are human", "checking your browser",
    "performing security verification", "captcha", "turnstile",
    "vahvista, että olet ihminen", "tarkistetaan selaintasi"
  ].some((marker) => challengeText.includes(marker));
  return {
    url: location.href, title: clean(document.title), visible_text: text,
    fields, links, buttons, text_blocks: textBlocks,
    embedded_legal_sections: embeddedLegalSections,
    iframe_urls: queryDeep("iframe[src]").map((frame) => clean(frame.src, 4000))
      .filter((url) => url.startsWith("http://") || url.startsWith("https://")),
    manual_verification_required: manualVerification
  };
})();
