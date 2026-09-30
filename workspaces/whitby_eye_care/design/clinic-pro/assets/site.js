// Clinic Pro enhancements: theme toggle, opening status, today's hours, FAQ search,
// scroll reveal, navbar shadow and local dates. Everything degrades to plain content
// without JS (light theme, no status badge).
(() => {
  document.documentElement.classList.remove("no-js");
  const DAYS = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"];

  // --- light / dark theme ------------------------------------------------------------
  const root = document.documentElement;
  const toggles = document.querySelectorAll("[data-theme-toggle]");
  const label = () => (root.getAttribute("data-bs-theme") === "dark" ? "Switch to light theme" : "Switch to dark theme");
  toggles.forEach((b) => b.setAttribute("aria-label", label()));
  toggles.forEach((b) =>
    b.addEventListener("click", () => {
      const next = root.getAttribute("data-bs-theme") === "dark" ? "light" : "dark";
      root.setAttribute("data-bs-theme", next);
      try { localStorage.setItem("clinic-theme", next); } catch (e) { /* private mode */ }
      toggles.forEach((t) => t.setAttribute("aria-label", label()));
    }),
  );

  // --- opening status: "Open now · until …" or when the clinic opens next -----------
  const hoursEl = document.getElementById("clinic-hours");
  if (hoursEl) {
    const hours = JSON.parse(hoursEl.textContent);
    const now = new Date();
    const minutes = now.getHours() * 60 + now.getMinutes();
    const toMin = (t) => Number(t.slice(0, 2)) * 60 + Number(t.slice(3, 5));
    const fmt = (t) => {
      const h = Number(t.slice(0, 2));
      return `${((h + 11) % 12) + 1}:${t.slice(3, 5)} ${h >= 12 ? "PM" : "AM"}`;
    };
    const spansOn = (day) =>
      hours.filter((h) => h.day === day && !h.closed && h.open && h.close).sort((a, b) => toMin(a.open) - toMin(b.open));
    const cap = (d) => d.charAt(0).toUpperCase() + d.slice(1);

    const today = DAYS[now.getDay()];
    const current = spansOn(today).find((h) => minutes >= toMin(h.open) && minutes < toMin(h.close));
    let text = null;
    if (current) {
      text = `Open now · until ${fmt(current.close)}`;
    } else {
      // the next opening in the coming week: later today, tomorrow, or a weekday
      for (let ahead = 0; ahead < 7 && !text; ahead += 1) {
        const day = DAYS[(now.getDay() + ahead) % 7];
        const next = spansOn(day).find((h) => ahead > 0 || toMin(h.open) > minutes);
        if (next) {
          const when = ahead === 0 ? "today" : ahead === 1 ? "tomorrow" : cap(day);
          text = `Opens ${when} at ${fmt(next.open)}`;
        }
      }
    }
    if (text) {
      document.querySelectorAll("[data-open-status]").forEach((el) => {
        el.textContent = text;
        el.classList.add(current ? "is-open" : "opens-next");
      });
    }
    document.querySelectorAll(`.hours-table tr[data-day="${today}"]`).forEach((tr) => tr.classList.add("today"));
  }

  // --- FAQ search ------------------------------------------------------------------------
  const search = document.getElementById("faq-search");
  if (search) {
    const items = [...document.querySelectorAll(".faq-item")];
    const count = document.getElementById("faq-count");
    const empty = document.getElementById("faq-empty");
    search.addEventListener("input", () => {
      const q = search.value.trim().toLowerCase();
      let shown = 0;
      for (const item of items) {
        const hit = q === "" || item.textContent.toLowerCase().includes(q);
        item.classList.toggle("d-none", !hit);
        if (hit) shown += 1;
      }
      count.textContent = q ? `${shown} of ${items.length} questions` : `${items.length} questions`;
      empty.classList.toggle("d-none", shown > 0);
    });
  }

  // --- navbar shadow once scrolled ----------------------------------------------------
  const nav = document.querySelector(".site-nav");
  const onScroll = () => nav && nav.classList.toggle("scrolled", window.scrollY > 8);
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  // --- scroll reveal (reduced motion is handled in CSS) --------------------------------
  const reveals = document.querySelectorAll(".reveal");
  if ("IntersectionObserver" in window) {
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (e.isIntersecting) { e.target.classList.add("is-visible"); io.unobserve(e.target); }
      }
    }, { rootMargin: "0px 0px -40px 0px" });
    reveals.forEach((el) => io.observe(el));
  } else {
    reveals.forEach((el) => el.classList.add("is-visible"));
  }

  // --- dates in the reader's locale ----------------------------------------------------
  document.querySelectorAll("time.js-local-date").forEach((t) => {
    const d = new Date(t.getAttribute("datetime"));
    if (!Number.isNaN(d.getTime())) t.textContent = d.toLocaleDateString(undefined, { dateStyle: "medium" });
  });
})();
