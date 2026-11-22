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

  const money = (c) => "$" + (c / 100).toLocaleString("en-US", { minimumFractionDigits: 2 });
  function describe(e) {
    const d = e.data || {};
    switch (e.kind) {
      case "mandate.signed": return ["Mandate signed", `Up to ${money(d.scope.max_amount_per_txn)} per purchase, ${money(d.scope.max_total_amount)} total · <code>${d.signature}</code>`, "ok"];
      case "acp.session": return ["ACP session open", `Merchant key <code>${d.merchant_key}</code>`];
      case "tool_call": return ["→ " + d.name, d.input && Object.keys(d.input).length ? `<code>${JSON.stringify(d.input)}</code>` : "checking limits"];
      case "tool_result": {
        const o = d.output || {};
        if (o.type === "offers") return ["Signed offers", o.offers.map((x) => `${x.name} · ${money(x.price)} ${x.signature_valid ? "✓ signature" : "✗"}`).join("<br>"), "ok"];
        if (o.type === "clarification") return ["Merchant asks", o.question];
        if (o.order) return ["Order confirmed", `${o.order.id} · ${money(o.order.amount)}`, "ok"];
        if (o.scope) return ["Mandate read", `${money(o.remaining)} remaining`];
        return ["Result", d.is_error ? "error" : "ok", d.is_error ? "warn" : ""];
      }
      case "agent_message": return ["Agent", d.text];
      case "payment_intent.created": { const p = d.payment_intent; return ["Payment intent", `${p.id}<br>risk: ${p.risk.decision} (${p.risk.score})`]; }
      case "payment_intent.succeeded": { const p = d.payment_intent; return ["Charged via " + p.provider, `${money(p.amount_captured)} → ${p.merchant_id}`, "ok"]; }
      case "acp.order.confirmed": return ["Merchant verified payment", d.order.id, "ok"];
      case "ledger": return ["Ledger posted", d.entries.map((x) => `${x.account} ${x.debit ? "Dr " + money(x.debit) : "Cr " + money(x.credit)}`).join("<br>") + `<br>trial balance: ${JSON.stringify(d.trial_balance)}`, "ok"];
      case "receipt": return ["Signed receipt", `<code>${d.signature}</code>`, "ok"];
      default: return [e.kind, ""];
    }
  }
  const actorOf = (e) => (e.kind === "acp.order.confirmed" ? "merchant" : e.kind === "tool_result" && e.data.name === "ask_merchant" ? "merchant" : e.actor);

  let trace = null, timers = [];
  function play() {
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
      }, 500 + i * 900));
    });
  }
  fetch("trace.json").then((r) => r.json()).then((t) => {
    trace = t;
    document.getElementById("goal").textContent = t.goal;
    const io = new IntersectionObserver(([en]) => { if (en.isIntersecting) { play(); io.disconnect(); } }, { threshold: 0.3 });
    io.observe(document.getElementById("demo"));
    document.getElementById("play").addEventListener("click", play);
  }).catch(() => { document.getElementById("demo-note").textContent = "Trace unavailable"; });
})();
