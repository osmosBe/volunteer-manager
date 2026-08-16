(() => {
  "use strict";

  document.addEventListener("click", (event) => {
    const link = event.target.closest("[data-history-back]");
    if (!link || event.defaultPrevented) return;
    if (window.history.length <= 1) return;
    event.preventDefault();
    window.history.back();
  });
})();
