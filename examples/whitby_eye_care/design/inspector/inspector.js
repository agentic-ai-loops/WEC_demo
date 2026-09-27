// Filter records (cards, files) by text; the mobile menu; local times.
(() => {
  const sidebar = document.querySelector(".sidebar");
  const toggle = document.querySelector(".menu-toggle");
  const setOpen = (open) => {
    sidebar.classList.toggle("open", open);
    toggle.setAttribute("aria-expanded", String(open));
  };
  toggle.addEventListener("click", () => setOpen(!sidebar.classList.contains("open")));
  // choosing a section closes the menu on small screens
  document.querySelectorAll("nav a").forEach((a) => a.addEventListener("click", () => setOpen(false)));

  const input = document.getElementById("filter");
  const items = [...document.querySelectorAll(".cards > .card, .cards > .asset, .files > .file")];
  input.addEventListener("input", () => {
    const q = input.value.trim().toLowerCase();
    for (const item of items) {
      item.classList.toggle("filtered-out", q !== "" && !item.textContent.toLowerCase().includes(q));
    }
  });

  for (const at of document.querySelectorAll(".local-time")) {
    at.textContent = new Date(at.textContent).toLocaleString();
  }
})();
