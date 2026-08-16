(() => {
  "use strict";

  const form = document.getElementById("registration-form");
  if (!form || !window.FormValidation) return;

  const controller = window.FormValidation.enhance(form);
  const birthDateInput = document.getElementById("birth_date");
  const shiftSelection = document.getElementById("shift-selection");
  const shiftChoices = [...form.querySelectorAll(".shift-choice")];

  const selectedShifts = () => shiftChoices.filter((choice) => choice.checked);

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
    if (!birthDateInput.value) return "Bitte gib ein gültiges Geburtsdatum an.";
    const birthDate = new Date(`${birthDateInput.value}T00:00:00`);
    if (Number.isNaN(birthDate.getTime())) return "Bitte gib ein gültiges Geburtsdatum an.";
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    if (birthDate > today) return "Das Geburtsdatum darf nicht in der Zukunft liegen.";
    const referenceDate = form.dataset.eventDate
      ? new Date(`${form.dataset.eventDate}T00:00:00`)
      : today;
    const age = ageOn(birthDate, referenceDate);
    if (age < 18 && form.dataset.allowsMinors !== "true") {
      return "Bei dieser Veranstaltung ist eine Teilnahme erst ab 18 Jahren möglich.";
    }
    if (age < 18 && selectedShifts().some((shift) => shift.dataset.allowsMinors !== "true")) {
      return "Mindestens eine ausgewählte Schicht ist erst ab 18 Jahren freigegeben.";
    }
    return null;
  };

  const validateShiftSelection = () => {
    const shifts = selectedShifts();
    if (!shifts.length) return "Bitte wähle mindestens eine Schicht aus.";
    const ordered = shifts
      .map((shift) => ({ start: new Date(shift.dataset.start), end: new Date(shift.dataset.end) }))
      .sort((left, right) => left.start - right.start);
    for (let index = 1; index < ordered.length; index += 1) {
      if (ordered[index - 1].end > ordered[index].start) {
        return "Die ausgewählten Schichten überschneiden sich zeitlich.";
      }
    }
    return null;
  };

  controller
    .addValidator({
      name: "shift_ids",
      controls: shiftChoices,
      control: shiftChoices[0] || shiftSelection,
      anchor: shiftSelection,
      focusTarget: shiftChoices[0] || shiftSelection,
      validate: validateShiftSelection,
    })
    .addValidator({
      name: "birth_date",
      control: birthDateInput,
      validate: validateBirthDate,
    });

  const updateShiftCards = () => {
    for (const choice of shiftChoices) {
      const card = choice.closest(".shift-option");
      const button = card.querySelector(".shift-toggle");
      card.classList.toggle("is-selected", choice.checked);
      button.classList.toggle("btn-primary", choice.checked);
      button.classList.toggle("btn-outline-primary", !choice.checked);
      button.textContent = choice.disabled
        ? "Nicht verfügbar"
        : choice.checked
          ? "Ausgewählt ✓"
          : "Auswählen";
    }
  };

  const updateReview = () => {
    const first = document.getElementById("first_name").value.trim();
    const last = document.getElementById("last_name").value.trim();
    const email = document.getElementById("email").value.trim();
    document.getElementById("review-person").textContent =
      first || last || email ? `${first} ${last} · ${email}` : "Kontaktdaten bitte ausfüllen.";
    const labels = selectedShifts().length
      ? selectedShifts().map((item) => item.dataset.label)
      : ["Noch keine Schicht gewählt."];
    const list = document.getElementById("review-shifts");
    list.replaceChildren(...labels.map((label) => {
      const item = document.createElement("li");
      item.textContent = label;
      return item;
    }));
  };

  form.addEventListener("input", () => {
    updateShiftCards();
    updateReview();
  });
  form.addEventListener("change", () => {
    updateShiftCards();
    updateReview();
  });
  updateShiftCards();
  updateReview();
})();
