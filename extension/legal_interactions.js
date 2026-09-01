(() => {
  "use strict";

  const MAX_REVEALED_TEXT = 30000;
  const clean = (value, maximum = 2000) =>
    String(value || "").replace(/\s+/g, " ").trim().slice(0, maximum);
  const cleanMultiline = (value, maximum = MAX_REVEALED_TEXT) =>
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
  const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

  const purposeFor = (text) => {
    const normalized = clean(text, 3000).toLowerCase();
    if (/tietosuoja|privacy|henkilötieto|data protection/.test(normalized)) return "privacy";
    if (/käyttöeh|osallistumiseh|kilpailun säänn|arvonnan säänn|terms|rules/.test(normalized)) return "rules";
    if (/suostu|markkinointi|uutiskirje|consent|marketing|sähköpost|email|sms|tekstiviest|puhel/.test(normalized)) return "consent";
    return "generic";
  };
  const directText = (element) => clean(
    element.innerText || element.value || element.getAttribute("aria-label"), 2000
  );
  const contextText = (element) => clean(
    element.closest("label")?.innerText || element.parentElement?.innerText, 3000
  );
  const purposeForElement = (element) => {
    if (element.hasAttribute("data-privacy-link")) return "privacy";
    if (element.hasAttribute("data-terms-link")) return "rules";
    if (element.classList.contains("consent-link")) return "consent";
    const directPurpose = purposeFor(directText(element));
    return directPurpose === "generic" ? purposeFor(contextText(element)) : directPurpose;
  };
  const isSameDocumentLink = (element) => {
    if (element.tagName !== "A") return true;
    const rawHref = clean(element.getAttribute("href"), 4000);
    if (!rawHref || rawHref.startsWith("#")) return true;
    if (/^javascript:/i.test(rawHref)) return false;
    try {
      const target = new URL(rawHref, location.href);
      return Boolean(target.hash)
        && target.origin === location.origin
        && target.pathname === location.pathname
        && target.search === location.search;
    } catch (_error) {
      return false;
    }
  };
  const clickableSignal = (element) => {
    const tag = element.tagName;
    const role = clean(element.getAttribute("role"), 50).toLowerCase();
    const className = clean(element.className, 1000).toLowerCase();
    return ["A", "BUTTON", "SUMMARY"].includes(tag)
      || (tag === "INPUT" && clean(element.type, 50).toLowerCase() === "button")
      || ["button", "link"].includes(role)
      || element.hasAttribute("onclick")
      || element.hasAttribute("aria-controls")
      || element.hasAttribute("aria-expanded")
      || element.getAttribute("aria-haspopup") === "dialog"
      || element.hasAttribute("data-terms-link")
      || element.hasAttribute("data-privacy-link")
      || /(?:terms|privacy|consent|legal|modal|clickable)[-_ ]?link/.test(className)
      || getComputedStyle(element).cursor === "pointer";
  };
  const isSafeCandidate = (element, purpose) => {
    if (!["rules", "privacy", "consent"].includes(purpose)) return false;
    if (!visible(element) || !clickableSignal(element)) return false;
    if (element.tagName === "LABEL" || element.isContentEditable) return false;
    if (element.disabled || element.getAttribute("aria-disabled") === "true") return false;
    if (["SELECT", "TEXTAREA"].includes(element.tagName)) return false;
    if (element.tagName === "INPUT" && clean(element.type, 50).toLowerCase() !== "button") {
      return false;
    }
    const declaredType = clean(element.getAttribute("type"), 50).toLowerCase();
    const associatedForm = Boolean(element.form || element.closest("form"));
    const isSubmit = associatedForm && (
      declaredType === "submit" || (element.tagName === "BUTTON" && !declaredType)
    );
    return !isSubmit && isSameDocumentLink(element);
  };
  const candidateScore = (element, purpose) => {
    const text = directText(element).toLowerCase();
    const className = clean(element.className, 1000).toLowerCase();
    let score = 0;
    if (element.hasAttribute("data-terms-link") && purpose === "rules") score += 120;
    if (element.hasAttribute("data-privacy-link") && purpose === "privacy") score += 120;
    if (element.classList.contains("consent-link") && purpose === "consent") score += 110;
    if (/käyttöeh|kilpailun säänn|arvonnan säänn|terms|rules/.test(text)) score += 80;
    if (/tietosuoja|privacy|data protection/.test(text)) score += 80;
    if (/sähköpost|email|sms|tekstiviest|puhel/.test(text)) score += 70;
    if (element.hasAttribute("aria-controls") || element.getAttribute("aria-haspopup") === "dialog") score += 50;
    if (element.hasAttribute("aria-expanded")) score += 25;
    if (["BUTTON", "A", "SUMMARY"].includes(element.tagName)) score += 35;
    if (["button", "link"].includes(clean(element.getAttribute("role"), 50))) score += 30;
    if (/(?:terms|privacy|consent|legal|modal|clickable)[-_ ]?link/.test(className)) score += 35;
    if (getComputedStyle(element).cursor === "pointer") score += 20;
    if (element.hasAttribute("tabindex")) score += 10;
    if (element.closest("label")) score -= 35;
    else score += 10;
    return score;
  };
  const triggerType = (element) => {
    if (element.hasAttribute("data-terms-link")) return "data_terms_link";
    if (element.hasAttribute("data-privacy-link")) return "data_privacy_link";
    if (element.classList.contains("consent-link")) return "consent_link";
    if (element.tagName === "SUMMARY") return "details_summary";
    if (element.hasAttribute("aria-controls")) return "aria_controls";
    if (element.hasAttribute("aria-expanded")) return "aria_expander";
    if (element.getAttribute("aria-haspopup") === "dialog") return "aria_dialog";
    if (element.hasAttribute("onclick")) return "click_handler";
    if (element.tagName === "A") return "same_page_link";
    if (["button", "link"].includes(clean(element.getAttribute("role"), 50))) {
      return `role_${clean(element.getAttribute("role"), 50)}`;
    }
    return "pointer_element";
  };

  const captureFormState = () => new Map(queryDeep("input, select, textarea").map((field) => [
    field,
    {
      checked: "checked" in field ? Boolean(field.checked) : null,
      selectedIndex: field.tagName === "SELECT" ? field.selectedIndex : null,
      value: "value" in field ? field.value : null
    }
  ]));
  const restoreAndDetectFormChanges = (before) => {
    let changed = false;
    for (const [field, state] of before.entries()) {
      if (!field.isConnected) continue;
      if (state.checked !== null && Boolean(field.checked) !== state.checked) {
        changed = true;
        field.checked = state.checked;
      }
      if (state.selectedIndex !== null && field.selectedIndex !== state.selectedIndex) {
        changed = true;
        field.selectedIndex = state.selectedIndex;
      }
      if (state.value !== null && field.value !== state.value) {
        changed = true;
        field.value = state.value;
      }
    }
    return changed;
  };
  const visibleDialogTexts = () => queryDeep(
    "dialog, [role=dialog], [aria-modal=true], .modal.show, .modal[open]"
  ).filter(visible).map((element) => cleanMultiline(element.innerText || element.textContent))
    .filter((text) => text.length >= 40);
  const visibleLines = () => cleanMultiline(document.body?.innerText, 50000)
    .split("\n").map((line) => clean(line, 5000)).filter(Boolean);
  const revealedText = (beforeDialogs, beforeLines) => {
    const previousDialogs = new Set(beforeDialogs);
    const changedDialogs = visibleDialogTexts().filter((text) => !previousDialogs.has(text));
    if (changedDialogs.length) {
      return changedDialogs.sort((left, right) => right.length - left.length)[0]
        .slice(0, MAX_REVEALED_TEXT);
    }
    const previousLines = new Set(beforeLines.map((line) => line.toLowerCase()));
    return visibleLines().filter((line) => !previousLines.has(line.toLowerCase()))
      .join("\n").slice(0, MAX_REVEALED_TEXT);
  };
  const activate = (element) => {
    if (element.closest("label") && !["A", "BUTTON", "SUMMARY"].includes(element.tagName)) {
      // Run the element's own framework handler without bubbling into the label.
      element.dispatchEvent(new MouseEvent("click", {
        bubbles: false, cancelable: true, composed: false, view: window
      }));
      return;
    }
    element.click();
  };

  return (async () => {
    const selector = [
      "a", "button", "input[type=button]", "summary", "[role=button]",
      "[role=link]", "[onclick]", "[aria-controls]", "[aria-expanded]",
      "[aria-haspopup=dialog]",
      "[data-terms-link]", "[data-privacy-link]", "[data-bs-target]",
      "[data-target]", "[data-modal]", "[data-dialog]", "[data-modal-target]",
      "[data-dialog-target]", "[data-micromodal-trigger]", "[class*='link']",
      "span", "[tabindex]"
    ].join(", ");
    const candidates = queryDeep(selector).map((element) => {
      const purpose = purposeForElement(element);
      return {
        element,
        purpose,
        score: candidateScore(element, purpose),
        text: directText(element),
        trigger: triggerType(element),
        insideLabel: Boolean(element.closest("label"))
      };
    }).filter((candidate) =>
      candidate.score >= 50 && isSafeCandidate(candidate.element, candidate.purpose)
    );

    const selected = [];
    for (const purpose of ["rules", "privacy", "consent"]) {
      const matches = candidates.filter((candidate) => candidate.purpose === purpose)
        .sort((left, right) => right.score - left.score);
      if (matches[0]) selected.push(matches[0]);
    }

    const interactions = [];
    let sequence = 1;
    for (const candidate of selected.slice(0, 3)) {
      const beforeDialogs = visibleDialogTexts();
      const beforeLines = visibleLines();
      const beforeForm = captureFormState();
      try {
        activate(candidate.element);
        await sleep(1200);
        const newText = revealedText(beforeDialogs, beforeLines);
        const formChanged = restoreAndDetectFormChanges(beforeForm);
        interactions.push({
          element_ref: `legal_candidate_${sequence++}`,
          frame_url: location.href,
          text: candidate.text,
          document_type: candidate.purpose,
          score: candidate.score,
          trigger_type: candidate.trigger,
          inside_label: candidate.insideLabel,
          result: formChanged
            ? "form_state_changed"
            : newText ? "content_revealed" : "clicked_no_readable_change",
          revealed_text: newText
        });
      } catch (_error) {
        restoreAndDetectFormChanges(beforeForm);
        interactions.push({
          element_ref: `legal_candidate_${sequence++}`,
          frame_url: location.href,
          text: candidate.text,
          document_type: candidate.purpose,
          score: candidate.score,
          trigger_type: candidate.trigger,
          inside_label: candidate.insideLabel,
          result: "click_failed",
          revealed_text: ""
        });
      }
    }
    return { url: location.href, interactions };
  })();
})();
