// Smooth scroll (Lenis) + scroll-driven animation. See .claude/skills/lenis-scroll/SKILL.md.
(function () {
  const root = document.documentElement;
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduce || typeof window.Lenis !== "function") return; // content stays fully visible

  root.classList.add("motion");
  const lenis = new window.Lenis({ lerp: 0.1, smoothWheel: true });
  window.lenis = lenis;
  requestAnimationFrame(function raf(t) { lenis.raf(t); requestAnimationFrame(raf); });

  // In-page links glide to their section. Target is resolved at click time from layout
  // (transforms from reveals don't move layout), landing just below the top edge.
  document.addEventListener("click", (ev) => {
    const a = ev.target.closest && ev.target.closest('a[href^="#"]');
    if (!a) return;
    const id = a.getAttribute("href").slice(1);
    const target = id ? document.getElementById(id) : document.body;
    if (!target) return;
    ev.preventDefault();
    lenis.scrollTo(target, { offset: 0, duration: 1.6, easing: (t) => 1 - Math.pow(1 - t, 4) });
    history.replaceState(null, "", "#" + id);
  });

  // ---- split headings into masked lines (keeps an aria-label with the full text)
  document.querySelectorAll("[data-reveal='lines']").forEach((el) => {
    const tmp = document.createElement("div");
    tmp.innerHTML = el.innerHTML.replace(/<br\s*\/?>/gi, " ");
    const label = tmp.textContent.replace(/\s+/g, " ").trim();
    const lines = el.innerHTML.split(/<br\s*\/?>/i);
    el.setAttribute("aria-label", label);
    el.innerHTML = lines.map((l, i) => `<span class="line" aria-hidden="true"><span style="--i:${i}">${l.trim()}</span></span>`).join("");
  });

  // ---- one-shot reveals (+ stagger index for children)
  document.querySelectorAll("[data-stagger]").forEach((g) => {
    [...g.children].forEach((c, i) => { c.setAttribute("data-reveal", c.getAttribute("data-reveal") || "up"); c.style.setProperty("--d", `${i * 80}ms`); });
  });
  const io = new IntersectionObserver((entries) => {
    entries.forEach((e) => { if (e.isIntersecting) { e.target.classList.add("is-in"); io.unobserve(e.target); countUp(e.target); } });
  }, { threshold: 0.15, rootMargin: "0px 0px -8% 0px" });
  document.querySelectorAll("[data-reveal]").forEach((el) => io.observe(el));

  function countUp(el) {
    el.querySelectorAll?.("[data-count]").forEach((c) => {
      const to = parseInt(c.dataset.count || c.textContent, 10);
      if (!Number.isFinite(to)) return;
      const t0 = performance.now(), dur = 900;
      (function tick(t) {
        const k = Math.min(1, (t - t0) / dur), v = Math.round(to * (1 - Math.pow(1 - k, 3)));
        c.textContent = String(v).padStart(2, "0");
        if (k < 1) requestAnimationFrame(tick);
      })(t0);
    });
  }

  // ---- scrubbed + velocity effects, all on Lenis' frame
  const heroCopy = document.querySelector("[data-scrub='hero']");
  const scales = [...document.querySelectorAll("[data-scrub='scale']")];
  const tickers = [...document.querySelectorAll("[data-velocity]")];
  const nav = document.getElementById("nav");
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  let lastY = 0;

  lenis.on("scroll", ({ scroll, velocity, direction }) => {
    const vh = innerHeight;
    if (heroCopy) {
      const p = clamp(scroll / vh, 0, 1);
      heroCopy.style.transform = `translate3d(0, ${-p * 120}px, 0)`;
      heroCopy.style.opacity = String(1 - p * 1.2);
    }
    scales.forEach((el) => {
      const r = el.getBoundingClientRect();
      const p = clamp((vh - r.top) / (vh * 0.9), 0, 1);
      el.style.transform = `scale(${0.86 + 0.14 * p})`;
      el.style.borderRadius = `${28 - 16 * p}px`;
    });
    tickers.forEach((el) => {
      el.style.setProperty("--skew", `${clamp(velocity * -0.25, -8, 8)}deg`);
      // marquee speeds up with scroll velocity and reverses when scrolling up
      el.getAnimations().forEach((a) => (a.playbackRate = (direction === -1 ? -1 : 1) * (1 + Math.min(Math.abs(velocity) * 0.35, 6))));
    });
    if (nav) {
      const hide = direction === 1 && scroll > vh * 0.6 && scroll - lastY > 0;
      nav.classList.toggle("nav-hidden", hide);
      lastY = scroll;
    }
  });
})();
