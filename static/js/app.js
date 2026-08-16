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
    var buttons = document.querySelectorAll("[data-filter]");
    var rows = document.querySelectorAll(".queue tbody tr[data-severity]");
    if (!buttons.length || !rows.length) { return; }

    var active = "";

    function matches(row) {
      if (!active || active === "all") { return true; }
      if (active === "overdue") { return row.getAttribute("data-overdue") === "1"; }
      return row.getAttribute("data-severity") === active;
    }

    function apply() {
      for (var i = 0; i < rows.length; i++) {
        rows[i].hidden = !matches(rows[i]);
      }
      for (var j = 0; j < buttons.length; j++) {
        var value = buttons[j].getAttribute("data-filter");
        var on = value === active || (active === "" && value === "all");
        buttons[j].setAttribute("aria-pressed", on ? "true" : "false");
      }
    }

    for (var k = 0; k < buttons.length; k++) {
      buttons[k].addEventListener("click", function (event) {
        var value = event.currentTarget.getAttribute("data-filter");
        active = (active === value || value === "all") ? "" : value;
        apply();
      });
    }

    apply();
  }

  /* Live "due in" counters on the owner attention queue. The deadline is
     rendered server-side as an ISO timestamp; this only formats it. */
  function countdowns() {
    var cells = document.querySelectorAll("time[data-deadline]");
    if (!cells.length) { return; }

    function pad(n) { return n < 10 ? "0" + n : String(n); }

    function tick() {
      var now = Date.now();
      for (var i = 0; i < cells.length; i++) {
        var due = new Date(cells[i].getAttribute("data-deadline")).getTime();
        if (isNaN(due)) { continue; }
        var remaining = due - now;
        var overdue = remaining < 0;
        var diff = Math.abs(remaining);
        var hours = Math.floor(diff / 3600000);
        var minutes = Math.floor((diff % 3600000) / 60000);
        var seconds = Math.floor((diff % 60000) / 1000);
        cells[i].textContent = (overdue ? "−" : "") + pad(hours) + ":" + pad(minutes) + ":" + pad(seconds);
        cells[i].classList.toggle("overdue", overdue);
        cells[i].title = overdue ? "Overdue" : "Time remaining";
      }
    }

    tick();
    setInterval(tick, 1000);
  }

  markActiveNav();
  confirmDestructive();
  severityFilter();
  countdowns();
})();
