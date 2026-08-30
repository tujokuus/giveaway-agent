(() => {
  "use strict";

  const MAX_TEXT = 50000;
  const MAX_ITEMS = 1000;
  const clean = (value, maximum = 2000) =>
    String(value || "").replace(/\s+/g, " ").trim().slice(0, maximum);
  const visible = (element) => {
    const style = getComputedStyle(element);
    const box = element.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" &&
      style.opacity !== "0" && box.width > 0 && box.height > 0 &&
      element.getAttribute("aria-hidden") !== "true";
  };
  const labelFor = (element) => {
    if (element.id) {
      const label = document.querySelector(`label[for="${CSS.escape(element.id)}"]`);
      if (label) return clean(label.innerText);
    }
    return clean(element.getAttribute("aria-label")) ||
      clean(element.closest("label")?.innerText) ||
      clean(element.getAttribute("placeholder"));
  };

  let nextRef = 1;
  const reference = () => `e${nextRef++}`;
  const fields = [...document.querySelectorAll("input, select, textarea")]
    .filter(visible)
    .slice(0, MAX_ITEMS)
    .map((element) => {
      const type = clean(element.type || element.tagName, 50).toLowerCase();
      const sensitive = type === "password" || type === "hidden";
      return {
        element_ref: reference(),
        frame_url: location.href,
        tag: element.tagName.toLowerCase(),
        field_type: type,
        name: clean(element.getAttribute("name"), 500) || null,
        label: labelFor(element) || null,
        required: Boolean(element.required || element.getAttribute("aria-required") === "true"),
        disabled: Boolean(element.disabled),
        checked: ["checkbox", "radio"].includes(type) ? Boolean(element.checked) : null,
        value_present: sensitive ? Boolean(element.value) : Boolean(clean(element.value, 1)),
        options: element.tagName === "SELECT"
          ? [...element.options].slice(0, 200).map((option) => clean(option.text, 500))
          : []
      };
    });

  const links = [...document.querySelectorAll("a[href]")]
    .filter(visible)
    .slice(0, 2000)
    .map((element) => ({
      element_ref: reference(),
      frame_url: location.href,
      text: clean(element.innerText),
      url: clean(element.href, 4000)
    }));

  const buttons = [...document.querySelectorAll("button, input[type=button], input[type=submit]")]
    .filter(visible)
    .slice(0, MAX_ITEMS)
    .map((element) => ({
      element_ref: reference(),
      frame_url: location.href,
      text: clean(element.innerText || element.value || element.getAttribute("aria-label")),
      button_type: clean(element.type || "button", 50),
      disabled: Boolean(element.disabled)
    }));

  const text = clean(document.body?.innerText, MAX_TEXT);
  const challengeText = `${document.title} ${text}`.toLowerCase();
  const manualVerification = [
    "just a moment", "verify you are human", "checking your browser",
    "performing security verification", "captcha", "turnstile"
  ].some((marker) => challengeText.includes(marker));

  return {
    url: location.href,
    title: clean(document.title),
    visible_text: text,
    fields,
    links,
    buttons,
    iframe_urls: [...document.querySelectorAll("iframe[src]")]
      .map((frame) => clean(frame.src, 4000))
      .filter((url) => url.startsWith("http://") || url.startsWith("https://")),
    manual_verification_required: manualVerification
  };
})();
