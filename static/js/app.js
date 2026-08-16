/* Emerald Rozalia command centre — progressive enhancement only.
   No build step and no dependencies: this file is served straight to the
   browser, so it must be plain script syntax (the previous version used an
   `import` statement, which threw on every page and disabled all of it). */

(function () {
  "use strict";

  /* Mark the current page in the sidebar. The shell is shared by every view,
     so this is the only signal telling the owner where they are. */
  function markActiveNav() {
    var here = window.location.pathname;
    var links = document.querySelectorAll(".nav a");
    for (var i = 0; i < links.length; i++) {
      if (links[i].getAttribute("href") === here) {
        links[i].classList.add("active");
        links[i].setAttribute("aria-current", "page");
      }
    }
  }

  /* Destructive submits ask first. Records here are deleted immediately and
     there is no undo, so the confirmation is the only safety net. */
  function confirmDestructive() {
    document.addEventListener("submit", function (event) {
      var form = event.target;
      var trigger = form.querySelector("[data-confirm]");
      var submitter = event.submitter;
      if (submitter && submitter.hasAttribute("data-confirm")) {
        trigger = submitter;
      } else if (submitter && !submitter.hasAttribute("data-confirm")) {
        return;
      }
      if (!trigger) { return; }
      if (!window.confirm(trigger.getAttribute("data-confirm"))) {
        event.preventDefault();
      }
    });
  }

  /* Severity tabs filter the owner attention queue. They looked interactive
     but did nothing before; this makes the control honest. */
  function severityFilter() {
    var tabs = document.querySelectorAll(".severity-tabs button[data-severity]");
    var rows = document.querySelectorAll(".queue tbody tr[data-severity]");
    if (!tabs.length || !rows.length) { return; }

    var active = "";

    function apply() {
      for (var i = 0; i < rows.length; i++) {
        var match = !active || rows[i].getAttribute("data-severity") === active;
        rows[i].hidden = !match;
      }
      for (var j = 0; j < tabs.length; j++) {
        tabs[j].setAttribute("aria-pressed", tabs[j].getAttribute("data-severity") === active ? "true" : "false");
      }
    }

    for (var k = 0; k < tabs.length; k++) {
      tabs[k].setAttribute("aria-pressed", "false");
      tabs[k].addEventListener("click", function (event) {
        var value = event.currentTarget.getAttribute("data-severity");
        active = active === value ? "" : value;
        apply();
      });
    }
  }

  markActiveNav();
  confirmDestructive();
  severityFilter();
})();
