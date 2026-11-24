---
name: lenis-scroll
description: Add lenis.dev-style smooth scrolling and scroll-driven animation to a static web page using Lenis (darkroomengineering/lenis). Use when asked for smooth scroll, scroll animations, parallax, scroll reveals, "make it feel like lenis.dev", or premium/editorial scroll feel on a site in this repo (web/).
---

# Lenis scroll + scroll animations

Lenis replaces native scroll with an interpolated (lerped) scroll position.
Everything that should feel "lenis.dev smooth" must read Lenis' scroll value
on the same animation frame, never raw `scroll` events.

## 1. Install (vendored, no CDN dependency)

```bash
npm i --prefix /var/tmp/lenis lenis@1.3.28
cp /var/tmp/lenis/node_modules/lenis/dist/{lenis.min.js,lenis.css} web/vendor/
sed -i '/sourceMappingURL/d' web/vendor/lenis.min.js   # avoids a 404 for the .map
```

```html
<link rel="stylesheet" href="vendor/lenis.css">
<script src="vendor/lenis.min.js" defer></script>   <!-- exposes window.Lenis -->
<script src="motion.js" defer></script>
```

Pin an exact version. The UMD build is used because the site is
buildless; in a bundler project use `import Lenis from "lenis"` instead.

## 2. Initialise once, one RAF loop

```js
const lenis = new Lenis({ lerp: 0.1, wheelMultiplier: 1, smoothWheel: true, anchors: true });
function raf(t) { lenis.raf(t); requestAnimationFrame(raf); }
requestAnimationFrame(raf);
window.lenis = lenis;           // other scripts (e.g. three.js) can read lenis.scroll
```

- `lerp: 0.1` gives the lenis.dev glide. Lower values are floatier, higher are snappier.
- `anchors: true` makes `<a href="#id">` links glide too.
- **Reduced motion:** if `prefers-reduced-motion: reduce`, skip Lenis entirely and
  show every reveal immediately. Never trap users in motion.
- Scrollable inner panels need `data-lenis-prevent` so they scroll natively.

## 3. Where animation belongs: an audit checklist

Walk the page top to bottom and classify each block:

| Block | Technique | Why |
|---|---|---|
| Hero copy / hero canvas | **Scrubbed** (tied to scroll progress): drift up, fade, rotate | The first scroll should feel like the camera moving, not content jumping |
| Big section headings | **Line-mask reveal**: each line slides up from behind a mask, once | The signature lenis.dev / editorial move |
| Grids of cards / steps | **Staggered fade-up** (60–90 ms between items) | Shows reading order without slowing the reader |
| Media (video, images) | **Scrubbed scale/clip** from ~0.88 to 1 | Gives weight as it enters the viewport |
| Marquees / tickers | **Velocity-linked**: speed and skew follow `lenis.velocity` | Makes the page feel physically connected to the wheel |
| Counters | Count up when visible | Small reward, cheap |
| Nav | Hide on scroll down, show on scroll up (`lenis.direction`) | Gets out of the way of the content |
| Body copy, forms, FAQ answers | **Leave alone** or a subtle fade | Never animate what people are actively reading or typing into |

Rules: reveal once (don't re-hide on scroll up), only animate `transform`
and `opacity` (GPU-cheap, no layout), keep durations to 0.8–1.2 s with an
expo-out ease `cubic-bezier(.16,1,.3,1)`, and everything must look correct
with JS disabled. Content is visible by default and is only hidden once JS
adds `html.motion`.

## 4. Implementation pattern (see web/motion.js for the working version)

- Declarative attributes: `data-reveal="lines|up|fade"`, `data-stagger` on a
  parent, `data-scrub="hero|scale"`, `data-velocity` on tickers, `data-count`.
- One-shot reveals use `IntersectionObserver` (threshold ~0.15, rootMargin
  `0px 0px -10% 0px`) and add `.is-in`; CSS does the transition.
- Scrubbed effects compute progress `p = clamp((vh - rect.top) / (vh + rect.height))`
  inside `lenis.on("scroll", ...)` and write transforms, batched per frame.
- Line splitting: wrap each line of a heading (split on `<br>`, or measure word
  wraps) in `<span class="line"><span>…</span></span>`. Keep the original text in an
  `aria-label` so screen readers aren't fed fragments.

## 5. Verify

- Playwright at 1440px and 390px: zero console errors, no horizontal overflow,
  screenshots mid-scroll showing partially revealed content.
- Emulate `reducedMotion: "reduce"`: all content is visible with no transforms.
- Disable JS: the page is fully readable.
