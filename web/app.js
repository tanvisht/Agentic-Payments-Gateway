// Nav theme, counters, and the transaction replay (data: trace.json, recorded from a real gateway run).
(function () {
  const nav = document.getElementById("nav");
  const lights = document.querySelectorAll(".light");
  function navTheme() {
    const y = 40;
    let onLight = false;
    lights.forEach((s) => { const r = s.getBoundingClientRect(); if (r.top <= y && r.bottom > y) onLight = true; });
    nav.classList.toggle("on-light", onLight);
  }
  addEventListener("scroll", navTheme, { passive: true }); navTheme();
  document.getElementById("faq-count").textContent = String(document.querySelectorAll(".acc details").length).padStart(2, "0");

  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const code = (v) => `<code>${esc(v)}</code>`;
  const money = (c) => "$" + (c / 100).toLocaleString("en-US", { minimumFractionDigits: 2 });
  function describe(e) {
    const d = e.data || {};
    switch (e.kind) {
      case "mandate.signed": return ["Mandate signed", `Up to ${money(d.scope.max_amount_per_txn)} per purchase · ${code(d.signature)}`, "ok"];
      case "acp.session": return ["ACP session open", `Merchant key ${code(d.merchant_key)}`];
      case "tool_call": return ["→ " + esc(d.name), d.input && Object.keys(d.input).length ? code(JSON.stringify(d.input)) : "checking limits"];
      case "tool_result": {
        const o = d.output || {};
        if (o.type === "offers") return ["Signed offers", o.offers.length ? o.offers.map((x) => `${esc(x.name)} · ${money(x.price)} ${x.signature_valid ? "✓ signature" : "✗"}`).join("<br>") : esc(o.message || "No matching offers") + (o.alternatives && o.alternatives.length ? "<br>Alternatives (not offered): " + o.alternatives.map((a) => esc(a.name)).join(", ") : ""), "ok"];
        if (o.type === "clarification") return ["Merchant asks", esc(o.question)];
        if (o.order) return ["Order confirmed", `${esc(o.order.id)} · ${money(o.order.amount)}`, "ok"];
        if (o.scope) return ["Mandate read", `${money(o.remaining)} remaining`];
        if (d.is_error) return ["Refused", esc((o.error && (o.error.message || o.error.code)) || "error"), "warn"];
        return ["Result", "ok"];
      }
      case "agent_message": return ["Agent", esc(d.text)];
      case "payment_intent.created": { const p = d.payment_intent; return ["Payment intent", `${esc(p.id)}<br>risk: ${esc(p.risk.decision)} (${esc(p.risk.score)})`]; }
      case "payment_intent.succeeded": { const p = d.payment_intent; return ["Charged via " + esc(p.provider), `${money(p.amount_captured)} → ${esc(p.merchant_id)}`, "ok"]; }
      case "acp.order.confirmed": return ["Merchant verified payment", esc(d.order.id), "ok"];
      case "ledger": return ["Ledger posted", d.entries.map((x) => `${esc(x.account)} ${x.debit ? "Dr " + money(x.debit) : "Cr " + money(x.credit)}`).join("<br>") + `<br>trial balance: ${esc(JSON.stringify(d.trial_balance))}`, "ok"];
      case "receipt": return ["Signed receipt", code(d.signature), "ok"];
      default: return [esc(e.kind), ""];
    }
  }
  const actorOf = (e) => (e.kind === "acp.order.confirmed" ? "merchant" : e.kind === "tool_result" && e.data.name === "ask_merchant" ? "merchant" : e.actor);

  // Plain-language narration for the big caption, one line per event.
  function story(e) {
    const d = e.data || {}, o = (d.output || {});
    switch (e.kind) {
      case "mandate.signed": return ["You", "You sign a spending mandate", `Your own key signs the limits: up to ${money(d.scope.max_amount_per_txn)} per purchase, only at approved merchants. The gateway never holds that key.`];
      case "acp.session": return ["Merchant", "The merchant opens a session", "Agent and merchant speak the Agentic Commerce Protocol. The merchant publishes the key it signs offers with."];
      case "tool_call": return ["Agent", d.name === "check_mandate" ? "The agent checks its limits" : d.name === "ask_merchant" ? "The agent asks the merchant" : d.name === "buy_offer" ? "The agent decides to buy" : "The agent calls " + d.name,
        d.input && d.input.message ? `“${d.input.message}”` : d.input && d.input.offer_id ? "Only a merchant-signed offer can be bought, at exactly the signed price." : "Before spending anything, it reads what it is allowed to spend."];
      case "tool_result":
        if (o.type === "offers") return ["Merchant", o.offers.length ? "Signed, price-locked offers arrive" : "Nothing fits, so nothing is offered", o.offers.length ? o.offers.map((x) => `${x.name}: ${money(x.price)}${x.signature_valid ? ", signature verified" : ", signature invalid"}`).join(" · ") : "Close matches come back as unsigned alternatives the agent cannot buy."];
        if (o.type === "clarification") return ["Merchant", "The merchant asks a question", o.question];
        if (o.order) return ["Agent", "The order is confirmed", `${o.order.id} for ${money(o.order.amount)}.`];
        if (o.scope) return ["Agent", "Limits confirmed", `${money(o.remaining)} left to spend under this mandate.`];
        if (d.is_error) return ["Gateway", "Blocked", (o.error && (o.error.message || o.error.code)) || "The request was refused."];
        return ["Agent", "Result received", ""];
      case "agent_message": return ["Agent", "The agent reasons", d.text];
      case "payment_intent.created": return ["Gateway", "Signed request checked, budget reserved", `Signature, nonce, mandate and risk all pass (risk: ${d.payment_intent.risk.decision}). The amount is reserved atomically before any money moves.`];
      case "payment_intent.succeeded": return ["Gateway", "The card is charged", `${money(d.payment_intent.amount_captured)} to ${d.payment_intent.merchant_id} via ${d.payment_intent.provider}.`];
      case "acp.order.confirmed": return ["Merchant", "The merchant verifies the payment", "It checks status, amount, currency and merchant with the gateway, and binds this payment to exactly one order."];
      case "ledger": return ["Gateway", "Double-entry ledger posted", `Debits equal credits. Trial balance: ${JSON.stringify(d.trial_balance)}.`];
      case "receipt": return ["Gateway", "A signed receipt is issued", "It binds your mandate, the payment and the ledger, and anyone can verify it offline."];
      default: return [e.actor, e.kind, ""];
    }
  }

  // ---- scroll-driven stage: each scroll step reveals the next event
  const stage = document.getElementById("tx-stage");
  const $ = (id) => document.getElementById(id);
  let trace = null, cards = [], shown = -1;
  const STEP_VH = 0.42; // scroll distance per event, as a fraction of the viewport

  function build(t) {
    trace = t;
    $("goal").textContent = t.goal;
    document.querySelectorAll(".ev").forEach((n) => n.remove());
    $("result").textContent = "";
    const rail = $("tx-rail"); rail.innerHTML = "";
    cards = t.events.map((e, i) => {
      const [title, body, cls] = describe(e);
      const el = document.createElement("div");
      el.className = "ev " + (cls || "");
      el.innerHTML = `<b>${String(i + 1).padStart(2, "0")} · ${title}</b>${body}`;
      document.querySelector(`.lane[data-actor="${actorOf(e)}"]`).appendChild(el);
      const dot = document.createElement("i"); rail.appendChild(dot);
      return el;
    });
    $("tx-total").textContent = String(cards.length).padStart(2, "0");
    stage.style.setProperty("--steps", cards.length);
    shown = -1; setStep(-1);
  }

  function setStep(k) {
    if (!trace) return;
    k = Math.max(-1, Math.min(cards.length - 1, k));
    if (k === shown) return;
    shown = k;
    cards.forEach((c, i) => { c.classList.toggle("show", i <= k); c.classList.toggle("active", i === k); });
    [...$("tx-rail").children].forEach((d, i) => d.classList.toggle("on", i <= k));
    const n = k + 1;
    $("tx-n").textContent = String(n).padStart(2, "0");
    $("demo-count").textContent = String(n).padStart(2, "0");
    const cap = document.querySelector(".tx-caption");
    if (k < 0) {
      $("tx-actor").textContent = "Ready"; $("tx-title").textContent = "Scroll to run the purchase";
      $("tx-detail").textContent = "Each step is real output from the gateway: signatures, offers, risk, the charge, the ledger and the receipt.";
    } else {
      const [actor, title, detail] = story(trace.events[k]);
      $("tx-actor").textContent = actor; $("tx-title").textContent = title; $("tx-detail").textContent = detail;
      cap.dataset.actor = (actor || "").toLowerCase();
    }
    cap.classList.remove("swap"); void cap.offsetWidth; cap.classList.add("swap");
    $("result").textContent = k === cards.length - 1 ? trace.result : "";
  }

  function progressFromScroll() {
    if (!trace) return;
    const r = stage.getBoundingClientRect(), vh = innerHeight;
    const travel = Math.max(1, r.height - vh);
    const p = Math.min(1, Math.max(0, -r.top / travel));
    setStep(r.top > vh * 0.2 ? -1 : Math.round(p * cards.length) - 1 + (p > 0 ? 1 : 0));
  }
  const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (window.lenis) window.lenis.on("scroll", progressFromScroll); else addEventListener("scroll", progressFromScroll, { passive: true });

  // Replay / live run: glide through the stage so scroll and story stay in sync.
  function play(t) {
    if (t) build(t);
    if (reduceMotion || !window.lenis) { setStep(cards.length - 1); return; }
    const top = stage.getBoundingClientRect().top + scrollY;
    const travel = stage.offsetHeight - innerHeight;
    window.lenis.scrollTo(top, { immediate: false, duration: 0.9, onComplete: () => {
      window.lenis.scrollTo(top + travel, { duration: Math.max(4, cards.length * 0.55), easing: (x) => x, lock: false });
    } });
  }

  const note = document.getElementById("demo-note");
  let recorded = null;
  fetch("trace.json").then((r) => r.json()).then((t) => {
    recorded = t; build(t);
    if (reduceMotion || !window.lenis) setStep(cards.length - 1); else progressFromScroll();
  }).catch(() => { note.textContent = "Trace unavailable"; });
  document.getElementById("play").addEventListener("click", () => trace && play());

  const form = document.getElementById("run-form"), btn = document.getElementById("run-btn");
  const goalIn = document.getElementById("goal-in"), budgetIn = document.getElementById("budget-in");
  document.querySelectorAll(".chip").forEach((c) => c.addEventListener("click", () => {
    goalIn.value = c.dataset.goal; budgetIn.value = c.dataset.budget;
    document.querySelectorAll(".chip").forEach((x) => x.classList.toggle("on", x === c));
  }));
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    btn.disabled = true; note.textContent = "Running a real purchase through the gateway…";
    const ctrl = new AbortController(); const to = setTimeout(() => ctrl.abort(), 20000);
    try {
      const r = await fetch("/api/run", { method: "POST", headers: { "Content-Type": "application/json" }, signal: ctrl.signal,
        body: JSON.stringify({ goal: goalIn.value.slice(0, 160), budget: Math.max(1, Math.min(1500, Number(budgetIn.value) || 400)) }) });
      if (!r.ok) throw new Error("HTTP " + r.status);
      const t = await r.json();
      if (!t.events) throw new Error("bad trace");
      note.textContent = "Live run · executed just now on the gateway";
      play(t);
    } catch (e) {
      note.textContent = "Live API unreachable, so showing the recorded run instead";
      if (recorded) play(recorded);
    } finally { clearTimeout(to); btn.disabled = false; }
  });
})();
