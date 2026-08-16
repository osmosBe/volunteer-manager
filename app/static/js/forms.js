(() => {
  "use strict";

  const controllers = new WeakMap();
  let generatedId = 0;

  const visibleControls = (form) => [
    ...form.querySelectorAll("input, select, textarea"),
  ].filter((control) => !["hidden", "submit", "button", "reset"].includes(control.type));

  const controlLabel = (control) => {
    const explicit = control.id
      ? document.querySelector(`label[for="${CSS.escape(control.id)}"]`)
      : null;
    const label = explicit || control.closest("label");
    return (label?.childNodes[0]?.textContent
      || label?.textContent
      || control.getAttribute("aria-label")
      || control.placeholder
      || control.title
      || control.name
      || "Dieses Feld")
      .trim()
      .replace(/\s+/g, " ");
  };

  const requirementText = (required) => required ? "Pflichtfeld" : "optional";

  const appendRequirement = (target, required) => {
    if (!target || target.querySelector(":scope > [data-field-requirement]")) return;
    const marker = document.createElement("span");
    marker.className = required ? "text-danger small" : "text-muted small";
    marker.dataset.fieldRequirement = required ? "required" : "optional";
    marker.textContent = ` (${requirementText(required)})`;
    target.appendChild(marker);
  };

  const decorateRequirements = (form) => {
    const groupedNames = new Set();
    for (const group of form.querySelectorAll("[data-required-group]")) {
      const name = group.dataset.requiredGroup;
      if (name) groupedNames.add(name);
      appendRequirement(group.querySelector("legend"), true);
    }
    const radioNames = new Set();
    for (const control of visibleControls(form)) {
      if (!control.name || control.disabled || control.dataset.fieldIndicator === "off") continue;
      if (groupedNames.has(control.name)) continue;
      if (control.type === "radio") {
        if (radioNames.has(control.name)) continue;
        radioNames.add(control.name);
        const legend = control.closest("fieldset")?.querySelector("legend");
        if (legend) {
          appendRequirement(legend, control.required);
          continue;
        }
      }
      const explicit = control.id
        ? form.querySelector(`label[for="${CSS.escape(control.id)}"]`)
        : null;
      const label = explicit || control.closest("label");
      if (label && !label.classList.contains("visually-hidden")) {
        appendRequirement(label, control.required);
        continue;
      }
      const marker = document.createElement("span");
      marker.className = control.required
        ? "form-text text-danger d-block"
        : "form-text text-muted d-block";
      marker.dataset.fieldRequirement = control.required ? "required" : "optional";
      marker.textContent = requirementText(control.required);
      const anchor = control.closest(".input-group") || control;
      anchor.insertAdjacentElement("afterend", marker);
    }
  };

  const nativeMessage = (control) => {
    const afterName = control.dataset.after;
    if (afterName && control.value) {
      const previous = control.form?.elements.namedItem(afterName);
      if (previous?.value && control.value <= previous.value) {
        return control.dataset.afterMessage || "Der spätere Zeitpunkt muss nach dem früheren liegen.";
      }
    }
    const validity = control.validity;
    const label = controlLabel(control);
    if (validity.valueMissing) return `Bitte fülle das Feld „${label}“ aus.`;
    if (validity.typeMismatch && control.type === "email") {
      return "Bitte gib eine gültige E-Mail-Adresse ein.";
    }
    if (validity.typeMismatch && control.type === "url") {
      return "Bitte gib eine vollständige HTTP(S)-URL ein.";
    }
    if (validity.badInput) return `Bitte gib für „${label}“ einen gültigen Wert ein.`;
    if (validity.patternMismatch) return `Bitte gib „${label}“ im erwarteten Format ein.`;
    if (validity.rangeUnderflow) return `Der Wert für „${label}“ muss mindestens ${control.min} sein.`;
    if (validity.rangeOverflow) return `Der Wert für „${label}“ darf höchstens ${control.max} sein.`;
    if (validity.stepMismatch) return `Bitte gib für „${label}“ einen gültigen Wert ein.`;
    if (validity.tooShort) return `„${label}“ ist zu kurz.`;
    if (validity.tooLong) return `„${label}“ ist zu lang.`;
    if (validity.customError) return control.validationMessage;
    return control.validity.valid ? null : `Bitte überprüfe das Feld „${label}“.`;
  };

  const acceptedFile = (file, accept) => {
    if (!accept) return true;
    return accept.split(",").map((value) => value.trim().toLowerCase()).some((rule) => {
      if (rule.startsWith(".")) return file.name.toLowerCase().endsWith(rule);
      if (rule.endsWith("/*")) return file.type.toLowerCase().startsWith(rule.slice(0, -1));
      return file.type.toLowerCase() === rule;
    });
  };

  const fileMessage = (control) => {
    if (control.type !== "file" || !control.files?.length) return null;
    const maxBytes = Number(control.dataset.maxFileSize || 0);
    for (const file of control.files) {
      if (!acceptedFile(file, control.accept)) {
        return "Bitte wähle eine Datei in einem erlaubten Format aus.";
      }
      if (maxBytes && file.size > maxBytes) {
        return `Die Datei darf höchstens ${Math.ceil(maxBytes / 1024 / 1024)} MB groß sein.`;
      }
    }
    return null;
  };

  class FormController {
    constructor(form) {
      this.form = form;
      this.touched = new Set();
      this.customValidators = new Map();
      decorateRequirements(form);
      this.form.noValidate = true;
      this.summary = this.ensureSummary();
      this.bind();
      if (this.summary.querySelector("[data-server-error]")) {
        queueMicrotask(() => this.summary.focus());
      }
    }

    ensureSummary() {
      let summary = this.form.previousElementSibling?.matches("[data-form-error-summary]")
        ? this.form.previousElementSibling
        : this.form.querySelector(":scope > [data-form-error-summary]");
      const legacy = this.form.previousElementSibling?.matches(".alert-danger")
        ? this.form.previousElementSibling
        : null;
      if (!summary && legacy) {
        const message = legacy.textContent.trim();
        legacy.replaceChildren();
        const heading = document.createElement("h2");
        heading.className = "h5";
        heading.textContent = "Bitte überprüfe deine Eingaben.";
        const list = document.createElement("ul");
        list.className = "mb-0";
        list.dataset.formErrorList = "";
        const item = document.createElement("li");
        item.dataset.serverError = "";
        item.textContent = message;
        list.appendChild(item);
        legacy.append(heading, list);
        legacy.setAttribute("role", "alert");
        legacy.setAttribute("aria-live", "assertive");
        legacy.tabIndex = -1;
        legacy.dataset.formErrorSummary = "";
        summary = legacy;
      }
      if (!summary) {
        summary = document.createElement("section");
        summary.className = "alert alert-danger d-none";
        summary.hidden = true;
        summary.setAttribute("role", "alert");
        summary.setAttribute("aria-live", "assertive");
        summary.tabIndex = -1;
        summary.dataset.formErrorSummary = "";
        summary.innerHTML = '<h2 class="h5">Bitte überprüfe deine Eingaben.</h2><ul class="mb-0" data-form-error-list></ul>';
        this.form.before(summary);
      }
      return summary;
    }

    ensureId(control) {
      if (!control.id) {
        generatedId += 1;
        control.id = `form-field-${generatedId}`;
      }
      return control.id;
    }

    errorNode(item) {
      const key = item.name || item.control.name || this.ensureId(item.control);
      let node = this.form.querySelector(`[data-field-error-for="${CSS.escape(key)}"]`);
      if (!node) {
        node = document.createElement("div");
        node.className = "invalid-feedback d-block";
        node.dataset.fieldErrorFor = key;
        const anchor = item.anchor || item.control;
        const container = anchor.closest(".form-check") || anchor;
        container.insertAdjacentElement("afterend", node);
      }
      if (!node.id) node.id = `${this.ensureId(item.control)}-error`;
      return node;
    }

    setState(item, message) {
      const controls = item.controls || [item.control];
      const anchor = item.anchor || item.control;
      const node = this.errorNode(item);
      node.textContent = message || "";
      node.hidden = !message;
      node.classList.toggle("d-none", !message);
      anchor.classList.toggle("is-invalid", Boolean(message));
      for (const control of controls) {
        if (message) {
          control.setAttribute("aria-invalid", "true");
          const ids = new Set((control.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean));
          ids.add(node.id);
          control.setAttribute("aria-describedby", [...ids].join(" "));
        } else {
          control.removeAttribute("aria-invalid");
          const ids = (control.getAttribute("aria-describedby") || "")
            .split(/\s+/)
            .filter((id) => id && id !== node.id);
          if (ids.length) control.setAttribute("aria-describedby", ids.join(" "));
          else control.removeAttribute("aria-describedby");
        }
      }
    }

    nativeItems() {
      return visibleControls(this.form).filter((control) => control.name).map((control) => ({
        name: control.name,
        control,
        controls: [control],
        validate: () => fileMessage(control) || nativeMessage(control),
      }));
    }

    groupItems() {
      return [...this.form.querySelectorAll("[data-required-group]")].map((anchor) => {
        const name = anchor.dataset.requiredGroup;
        const controls = [...anchor.querySelectorAll(`[name="${CSS.escape(name)}"]`)];
        return {
          name,
          control: controls[0] || anchor,
          controls,
          anchor,
          focusTarget: controls[0] || anchor,
          validate: () => controls.some((control) => control.checked)
            ? null
            : anchor.dataset.requiredMessage || "Bitte wähle mindestens eine Option aus.",
        };
      });
    }

    addValidator({ name, control, controls, anchor, focusTarget, validate }) {
      const resolvedControls = controls || (control ? [control] : []);
      this.customValidators.set(name, {
        name,
        control: control || resolvedControls[0],
        controls: resolvedControls,
        anchor,
        focusTarget: focusTarget || control || resolvedControls[0],
        validate,
      });
      return this;
    }

    items() {
      const customNames = new Set(this.customValidators.keys());
      const groups = this.groupItems().filter((item) => !customNames.has(item.name));
      const groupNames = new Set(groups.map((item) => item.name));
      return [
        ...this.nativeItems().filter((item) => !customNames.has(item.name) && !groupNames.has(item.name)),
        ...groups,
        ...this.customValidators.values(),
      ];
    }

    clearServerError(name) {
      for (const node of this.summary.querySelectorAll(`[data-server-error][data-error-for="${CSS.escape(name)}"]`)) node.remove();
      for (const node of this.form.querySelectorAll(`[data-server-field-error][data-field-error-for="${CSS.escape(name)}"]`)) {
        node.removeAttribute("data-server-field-error");
      }
    }

    validate(showAll = false) {
      const errors = [];
      for (const item of this.items()) {
        if (!showAll && !this.touched.has(item.name)) continue;
        const message = item.validate() || null;
        this.setState(item, message);
        if (message && !errors.some((error) => error.name === item.name)) {
          errors.push({ ...item, message });
        }
      }
      this.renderSummary(errors);
      return errors;
    }

    renderSummary(errors) {
      const list = this.summary.querySelector("[data-form-error-list]");
      const serverItems = [...list.querySelectorAll("[data-server-error]")];
      list.replaceChildren(...serverItems);
      for (const error of errors) {
        const item = document.createElement("li");
        const link = document.createElement("a");
        const target = error.focusTarget || error.control;
        link.href = `#${this.ensureId(target)}`;
        link.textContent = error.message;
        link.addEventListener("click", (event) => {
          event.preventDefault();
          target.focus();
        });
        item.appendChild(link);
        list.appendChild(item);
      }
      const hasErrors = serverItems.length > 0 || errors.length > 0;
      this.summary.hidden = !hasErrors;
      this.summary.classList.toggle("d-none", !hasErrors);
    }

    bind() {
      this.form.addEventListener("focusout", (event) => {
        const control = event.target.closest("input, select, textarea");
        if (!control?.name) return;
        this.touched.add(control.name);
        this.clearServerError(control.name);
        this.validate();
      });
      this.form.addEventListener("change", (event) => {
        const control = event.target.closest("input, select, textarea");
        if (!control?.name) return;
        this.touched.add(control.name);
        this.clearServerError(control.name);
        this.validate();
      });
      this.form.addEventListener("input", (event) => {
        const control = event.target.closest("input, select, textarea");
        if (!control?.name || !this.touched.has(control.name)) return;
        this.clearServerError(control.name);
        this.validate();
      });
      this.form.addEventListener("submit", (event) => {
        for (const item of this.items()) this.touched.add(item.name);
        const errors = this.validate(true);
        if (errors.length) {
          event.preventDefault();
          this.summary.focus();
          return;
        }
        if (this.form.dataset.confirm && !window.confirm(this.form.dataset.confirm)) {
          event.preventDefault();
        }
      });
    }
  }

  const enhance = (form) => {
    if (!form || form.dataset.validation === "off") return null;
    if (!controllers.has(form)) controllers.set(form, new FormController(form));
    return controllers.get(form);
  };

  window.FormValidation = { enhance };
  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll('form:not([data-validation="off"])').forEach((form) => {
      enhance(form);
    });
    const serverSummary = [...document.querySelectorAll("[data-form-error-summary]")]
      .find((summary) => summary.querySelector("[data-server-error]"));
    if (serverSummary) serverSummary.focus();
  });
})();
