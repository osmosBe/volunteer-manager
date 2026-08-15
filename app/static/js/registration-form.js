(() => {
  const form = document.getElementById("registration-form");
  if (!form) return;

  const errorBox = document.getElementById("registration-errors");
  const errorList = document.getElementById("registration-error-list");
  const touched = new Set();
  let showServerError = Boolean(errorList.querySelector("[data-server-error]"));

  const fieldMessages = {
    first_name: "Bitte gib deinen Vornamen ein.",
    last_name: "Bitte gib deinen Nachnamen ein.",
    email: "Bitte gib eine gültige E-Mail-Adresse ein.",
    birth_date: "Bitte gib ein gültiges Geburtsdatum an.",
    contact_consent: "Bitte stimme der Kontaktaufnahme für diese Veranstaltung zu.",
  };

  const selectedShifts = () => [
    ...form.querySelectorAll(".shift-choice:checked"),
  ];

  const ageOn = (birthDate, referenceDate) => {
    let age = referenceDate.getFullYear() - birthDate.getFullYear();
    const birthdayPending =
      referenceDate.getMonth() < birthDate.getMonth() ||
      (referenceDate.getMonth() === birthDate.getMonth() &&
        referenceDate.getDate() < birthDate.getDate());
    if (birthdayPending) age -= 1;
    return age;
  };

  const validateBirthDate = () => {
    const input = document.getElementById("birth_date");
    if (!input.value) return fieldMessages.birth_date;
    const birthDate = new Date(`${input.value}T00:00:00`);
    if (Number.isNaN(birthDate.getTime())) return fieldMessages.birth_date;

    const today = new Date();
    today.setHours(0, 0, 0, 0);
    if (birthDate > today) {
      return "Das Geburtsdatum darf nicht in der Zukunft liegen.";
    }

    const eventDateValue = form.dataset.eventDate;
    const referenceDate = eventDateValue
      ? new Date(`${eventDateValue}T00:00:00`)
      : today;
    const age = ageOn(birthDate, referenceDate);
    if (age < 18 && form.dataset.allowsMinors !== "true") {
      return "Bei dieser Veranstaltung ist eine Teilnahme erst ab 18 Jahren möglich.";
    }
    if (
      age < 18 &&
      selectedShifts().some((shift) => shift.dataset.allowsMinors !== "true")
    ) {
      return "Mindestens eine ausgewählte Schicht ist erst ab 18 Jahren freigegeben.";
    }
    return null;
  };

  const validateShiftSelection = () => {
    const shifts = selectedShifts();
    if (!shifts.length) return "Bitte wähle mindestens eine Schicht aus.";
    const ordered = shifts
      .map((shift) => ({
        start: new Date(shift.dataset.start),
        end: new Date(shift.dataset.end),
      }))
      .sort((left, right) => left.start - right.start);
    for (let index = 1; index < ordered.length; index += 1) {
      if (ordered[index - 1].end > ordered[index].start) {
        return "Die ausgewählten Schichten überschneiden sich zeitlich.";
      }
    }
    return null;
  };

  const validateField = (name) => {
    const input = document.getElementById(name);
    if (name === "birth_date") return validateBirthDate();
    if (name === "shift_ids") return validateShiftSelection();
    if (!input) return null;
    if (name === "contact_consent") {
      return input.checked ? null : fieldMessages[name];
    }
    if (!input.value.trim()) return fieldMessages[name] || "Dieses Feld ist erforderlich.";
    if (name === "email" && !input.validity.valid) return fieldMessages.email;
    return null;
  };

  const setInvalidState = (name, invalid) => {
    if (name === "shift_ids") {
      document.getElementById("shift-selection").classList.toggle("border-danger", invalid);
      return;
    }
    const input = document.getElementById(name);
    if (!input) return;
    input.classList.toggle("is-invalid", invalid);
    if (invalid) input.setAttribute("aria-invalid", "true");
    else input.removeAttribute("aria-invalid");
  };

  const validationNames = [
    "shift_ids",
    "first_name",
    "last_name",
    "email",
    "birth_date",
    "contact_consent",
  ];

  const renderErrors = (validateAll = false) => {
    const errors = [];
    for (const name of validationNames) {
      if (!validateAll && !touched.has(name)) continue;
      const message = validateField(name);
      setInvalidState(name, Boolean(message));
      if (message && !errors.includes(message)) errors.push(message);
    }

    errorList.replaceChildren();
    if (showServerError) {
      const serverError = document.createElement("li");
      serverError.textContent = errorBox.dataset.serverError;
      errorList.appendChild(serverError);
    }
    for (const message of errors) {
      const item = document.createElement("li");
      item.textContent = message;
      errorList.appendChild(item);
    }
    errorBox.classList.toggle("d-none", !showServerError && !errors.length);
    return errors;
  };

  const updateShiftCards = () => {
    for (const choice of form.querySelectorAll(".shift-choice")) {
      const card = choice.closest(".shift-option");
      const button = card.querySelector(".shift-toggle");
      card.classList.toggle("is-selected", choice.checked);
      button.classList.toggle("btn-primary", choice.checked);
      button.classList.toggle("btn-outline-primary", !choice.checked);
      if (choice.disabled) button.textContent = "Nicht verfügbar";
      else button.textContent = choice.checked ? "Ausgewählt ✓" : "Auswählen";
    }
  };

  const updateReview = () => {
    const first = document.getElementById("first_name").value.trim();
    const last = document.getElementById("last_name").value.trim();
    const email = document.getElementById("email").value.trim();
    document.getElementById("review-person").textContent =
      first || last || email
        ? `${first} ${last} · ${email}`
        : "Kontaktdaten bitte ausfüllen.";

    const choices = selectedShifts();
    const labels = choices.length
      ? choices.map((item) => item.dataset.label)
      : ["Noch keine Schicht gewählt."];
    const list = document.getElementById("review-shifts");
    list.replaceChildren();
    for (const label of labels) {
      const item = document.createElement("li");
      item.textContent = label;
      list.appendChild(item);
    }
  };

  const clearServerError = () => {
    showServerError = false;
    delete errorBox.dataset.serverError;
  };

  const initialServerError = errorList.querySelector("[data-server-error]");
  if (initialServerError) errorBox.dataset.serverError = initialServerError.textContent;

  form.addEventListener("input", (event) => {
    clearServerError();
    if (event.target.classList.contains("shift-choice")) {
      touched.add("shift_ids");
      if (document.getElementById("birth_date").value) touched.add("birth_date");
    } else if (event.target.id) {
      touched.add(event.target.id);
    }
    updateShiftCards();
    updateReview();
    renderErrors();
  });

  form.addEventListener("focusout", (event) => {
    if (!event.target.id || !validationNames.includes(event.target.id)) return;
    clearServerError();
    touched.add(event.target.id);
    renderErrors();
  });

  form.addEventListener("submit", (event) => {
    clearServerError();
    validationNames.forEach((name) => touched.add(name));
    const errors = renderErrors(true);
    if (!errors.length) return;
    event.preventDefault();
    errorBox.focus();
  });

  updateShiftCards();
  updateReview();
})();
