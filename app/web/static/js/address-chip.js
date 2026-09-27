// Copy-to-clipboard for the address chip (UI_REDESIGN.md §4). Event-delegated on document, so it
// keeps working after HTMX swaps the tokens table in without any re-binding step.
(function () {
  "use strict";

  var COPIED_CLASS = "copied";
  var REVERT_AFTER_MS = 1500;
  var timers = new WeakMap();

  function revert(button) {
    button.classList.remove(COPIED_CLASS);
    timers.delete(button);
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest(".address-copy-btn");
    if (!button) return;

    var text = button.getAttribute("data-copy-text") || "";
    if (!navigator.clipboard || !text) return;

    navigator.clipboard.writeText(text).then(
      function () {
        var pending = timers.get(button);
        if (pending) window.clearTimeout(pending);
        button.classList.add(COPIED_CLASS);
        timers.set(
          button,
          window.setTimeout(function () {
            revert(button);
          }, REVERT_AFTER_MS)
        );
      },
      function () {
        // Clipboard permission denied or unavailable: no error surfaced, the button just does
        // nothing visible, which is a safe failure mode for a convenience feature.
      }
    );
  });
})();
