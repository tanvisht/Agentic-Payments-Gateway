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

  let trace = null, timers = [];
  function play(t) {
    if (t) trace = t;
    document.getElementById("goal").textContent = trace.goal;
    timers.forEach(clearTimeout); timers = [];
    document.querySelectorAll(".ev").forEach((n) => n.remove());
    document.getElementById("result").textContent = "";
    let n = 0;
    trace.events.forEach((e, i) => {
      timers.push(setTimeout(() => {
        const [title, body, cls] = describe(e);
        const el = document.createElement("div");
        el.className = "ev " + (cls || "");
        el.innerHTML = `<b>${String(++n).padStart(2, "0")} · ${title}</b>${body}`;
        document.querySelector(`.lane[data-actor="${actorOf(e)}"]`).appendChild(el);
        requestAnimationFrame(() => el.classList.add("show"));
        document.getElementById("demo-count").textContent = String(n).padStart(2, "0");
        if (i === trace.events.length - 1) document.getElementById("result").textContent = trace.result;
      }, 400 + i * 650));
    });
  }
  const note = document.getElementById("demo-note");
  let recorded = null;
  fetch("trace.json").then((r) => r.json()).then((t) => {
    recorded = t; trace = t;
    document.getElementById("goal").textContent = t.goal;
    const io = new IntersectionObserver(([en]) => { if (en.isIntersecting) { play(); io.disconnect(); } }, { threshold: 0.3 });
    io.observe(document.getElementById("demo"));
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
