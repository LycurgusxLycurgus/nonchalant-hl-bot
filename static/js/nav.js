(function () {
  const drawer = document.getElementById("primary-drawer");
  const toggleButtons = Array.from(document.querySelectorAll("[data-nav-toggle]"));
  if (!drawer || toggleButtons.length === 0) {
    return;
  }

  const focusableSelector = [
    "a[href]",
    "button:not([disabled])",
    "textarea:not([disabled])",
    "input[type!='hidden']:not([disabled])",
    "select:not([disabled])",
    "[tabindex]:not([tabindex='-1'])",
  ].join(",");

  let lastActiveElement = null;

  function getFocusable() {
    return Array.from(drawer.querySelectorAll(focusableSelector)).filter((el) =>
      el.offsetParent !== null || drawer === el
    );
  }

  function setAria(expanded) {
    toggleButtons.forEach((btn) => btn.setAttribute("aria-expanded", String(expanded)));
  }

  function openDrawer() {
    if (!drawer.hidden) return;
    lastActiveElement = document.activeElement;
    drawer.hidden = false;
    document.body.classList.add("is-nav-open");
    setAria(true);
    const focusable = getFocusable();
    if (focusable.length) {
      focusable[0].focus();
    }
    document.addEventListener("keydown", handleKeydown);
    document.addEventListener("click", handleClickOutside, true);
  }

  function closeDrawer() {
    if (drawer.hidden) return;
    drawer.hidden = true;
    document.body.classList.remove("is-nav-open");
    setAria(false);
    document.removeEventListener("keydown", handleKeydown);
    document.removeEventListener("click", handleClickOutside, true);
    if (lastActiveElement && typeof lastActiveElement.focus === "function") {
      lastActiveElement.focus();
    }
  }

  function handleKeydown(event) {
    if (event.key === "Escape") {
      event.preventDefault();
      closeDrawer();
      return;
    }
    if (event.key !== "Tab") {
      return;
    }

    const focusable = getFocusable();
    if (focusable.length === 0) {
      event.preventDefault();
      return;
    }

    const first = focusable[0];
    const last = focusable[focusable.length - 1];

    if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    } else if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    }
  }

  function handleClickOutside(event) {
    if (drawer.contains(event.target)) {
      return;
    }
    closeDrawer();
  }

  toggleButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      if (drawer.hidden) {
        openDrawer();
      } else {
        closeDrawer();
      }
    });
  });

  drawer.querySelectorAll("a").forEach((link) => {
    link.addEventListener("click", () => {
      if (window.matchMedia("(max-width: 1024px)").matches) {
        closeDrawer();
      }
    });
  });

  window.addEventListener("resize", () => {
    if (!window.matchMedia("(max-width: 1024px)").matches) {
      closeDrawer();
    }
  });
})();
