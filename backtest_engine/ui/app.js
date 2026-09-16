/* Quant Agent Control Plane — core: window.UI (api, DOM, tabs, charts)
   + tabs 1-4 (Runs, Audit, Evolution, Strategies). Vanilla ES2020. */
"use strict";

window.UI = (function () {
  // ---------------------------------------------------------------- api
  async function request(method, path, body) {
    const opts = { method, headers: { "Content-Type": "application/json" } };
    if (body !== undefined) opts.body = JSON.stringify(body);
    let res;
    try {
      res = await fetch(path, opts);
    } catch (e) {
      throw new Error("API unreachable: " + e.message);
    }
    let data = null;
    try { data = await res.json(); } catch (e) { /* non-JSON */ }
    if (!res.ok) {
      const msg = (data && (data.error || data.detail)) ||
        (res.status + " " + res.statusText);
      throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    }
    return data;
  }
  const api = {
    get: (p) => request("GET", p),
    post: (p, b) => request("POST", p, b || {}),
    patch: (p, b) => request("PATCH", p, b || {}),
  };

  // ---------------------------------------------------------------- dom
  function el(id) { return document.getElementById(id); }

  function h(tag, attrs, ...children) {
    const node = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (v === null || v === undefined) continue;
        if (k === "class") node.className = v;
        else if (k === "html") node.innerHTML = v;
        else if (k.startsWith("on") && typeof v === "function")
          node.addEventListener(k.slice(2), v);
        else node.setAttribute(k, v);
      }
    }
    const flat = [];
    for (const c of children) {
      if (c === null || c === undefined) continue;
      if (Array.isArray(c)) flat.push(...c);
      else flat.push(c);
    }
    for (const c of flat) {
      if (c === null || c === undefined) continue;
      node.appendChild(typeof c === "string" || typeof c === "number" || typeof c === "boolean"
        ? document.createTextNode(String(c)) : c);
    }
    return node;
  }

  function chip(text, cls) { return h("span", { class: "chip " + (cls || "dim") }, text); }

  function toast(msg, kind) {
    const t = h("div", { class: "toast " + (kind || "") }, msg);
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 4000);
  }

  // ---------------------------------------------------------------- charts
  function chart(idOrNode, option) {
    if (!window.echarts) {
      return null;
    }
    const node = (typeof idOrNode === "string") ? el(idOrNode) : idOrNode;
    if (!node) return null;
    let inst = echarts.getInstanceByDom(node);
    if (!inst) inst = echarts.init(node);
    inst.setOption(option, true);
    return inst;
  }

  // ---------------------------------------------------------------- fmt
  const fmt = {
    num: (x, d = 2) => (x === null || x === undefined || Number.isNaN(x))
      ? "—" : Number(x).toFixed(d),
    pct: (x, d = 2) => (x === null || x === undefined || Number.isNaN(x))
      ? "—" : Number(x).toFixed(d) + "%",
    dt: (s) => {
      if (!s) return "—";
      const d = new Date(s);
      return isNaN(d.getTime()) ? s : d.toISOString().replace("T", " ").slice(0, 16) + "Z";
    },
    trunc: (s, n = 60) => {
      s = s === null || s === undefined ? "—" : String(s);
      return s.length > n ? s.slice(0, n) + "…" : s;
    },
  };

  // ---------------------------------------------------------------- tabs
  const tabs = {};           // name -> {title, render, onShow}
  let activeTab = "runs";
  let busy = false;

  function renderTabButtons() {
    const nav = el("tabs");
    nav.innerHTML = "";
    for (const [name, t] of Object.entries(tabs)) {
      nav.appendChild(h("button", {
        class: name === activeTab ? "active" : "",
        onclick: () => showTab(name),
      }, t.title));
    }
  }

  function showTab(name) {
    if (!tabs[name]) return;
    activeTab = name;
    renderTabButtons();
    renderActive();
    const t = tabs[name];
    if (t.onShow) t.onShow();
  }

  function renderActive() {
    if (busy) return;
    busy = true;
    el("content").innerHTML = '<div class="empty"><span class="spin"></span> loading…</div>';
    Promise.resolve(tabs[activeTab].render(el("content")))
      .catch((e) => showError(e))
      .finally(() => { busy = false; });
  }

  function showError(e) {
    const banner = el("api-banner");
    banner.textContent = String(e && e.message || e);
    banner.className = "visible banner-err";
    el("content").innerHTML = "";
    el("content").appendChild(h("div", { class: "empty" }, "render failed — see banner"));
  }

  function hideError() {
    el("api-banner").className = "";
  }

  // wrap api calls: show banner on failure
  async function apiCall(fn) {
    try {
      hideError();
      return await fn();
    } catch (e) {
      const banner = el("api-banner");
      banner.textContent = String(e && e.message || e);
      banner.className = "visible banner-err";
      throw e;
    }
  }

  // ---------------------------------------------------------------- helpers
  function kpi(label, value, cls) {
    return h("div", { class: "kpi" },
      h("div", { class: "kpi-label" }, label),
      h("div", { class: "kpi-value" + (cls ? " " + cls : "") }, value));
  }

  function card(title, ...children) {
    const c = h("div", { class: "card" });
    if (title) c.appendChild(h("div", { class: "card-title" }, title));
    for (const x of children) c.appendChild(x);
    return c;
  }

  function table(headers, rows, numCols) {
    numCols = numCols || {};
    const t = h("table", { class: "data" });
    const thead = h("thead");
    const hr = h("tr");
    for (const hd of headers) hr.appendChild(h("th", null, hd));
    thead.appendChild(hr);
    t.appendChild(thead);
    const tb = h("tbody");
    for (const row of rows) {
      const tr = h("tr");
      row.forEach((cell, i) => {
        tr.appendChild(h("td", numCols[i] ? { class: "num" } : null,
          cell === null || cell === undefined ? "—" : String(cell)));
      });
      tb.appendChild(tr);
    }
    t.appendChild(tb);
    return t;
  }

  function kv(rows) {
    const c = h("div", { class: "kv" });
    for (const [k, v] of rows) {
      c.appendChild(h("div", { class: "row" },
        h("span", { class: "k" }, k),
        h("span", { class: "v" }, v === null || v === undefined ? "—" : String(v))));
    }
    return c;
  }

  // ---------------------------------------------------------------- jobs tick (for tab 5)
  let jobsTick = null;
  function onJobsTick(fn) { jobsTick = fn; }
  let jobsPoller = null;
  async function startJobsPolling() {
    if (jobsPoller) return;
    jobsPoller = setInterval(async () => {
      if (!jobsTick) return;
      try {
        const jobs = await api.get("/api/jobs");
        if (jobs.some((j) => j.status === "running" || j.status === "pending")) {
          jobsTick(jobs);
        }
      } catch (e) { /* banner handled by next successful render */ }
    }, 3000);
  }

  // ================================================================ TABS
  // ------------------------------------------------------------- 1. Runs
  let selectedRun = null;

  async function renderRuns(content) {
    const runs = await apiCall(() => api.get("/api/runs"));
    if (!runs.length) {
      content.innerHTML = "";
      content.appendChild(h("div", { class: "empty" }, "no runs yet"));
      return;
    }
    table_wrap(runs, content);
  }

  // (rebuild table with clickable rows — table() above returns plain table)
  function table_wrap(runs, content) {
    content.innerHTML = "";
    const t = h("table", { class: "data" });
    const head = h("tr", null,
      ...["run_id", "git", "time", "specs", "PASS", "FAIL", "INC", "errors", "period"].map((x) => h("th", null, x)));
    t.appendChild(h("thead", null, head));
    const tb = h("tbody");
    for (const r of runs) {
      const tr = h("tr", { class: "row", onclick: () => { selectedRun = r.run_id; renderRunsDetail(r.run_id); } },
        h("td", { class: "mono" }, r.run_id),
        h("td", { class: "dim mono" }, r.git_sha),
        h("td", null, fmt.dt(r.generated_at)),
        h("td", { class: "num" }, r.spec_count),
        h("td", null, chip(String(r.pass), "ok")),
        h("td", null, chip(String(r.fail), r.fail ? "err" : "dim")),
        h("td", null, chip(String(r.inconclusive), r.inconclusive ? "warn" : "dim")),
        h("td", { class: "num" }, r.error_flags || 0),
        h("td", null, (r.period || []).join(" → ") || "—"),
      );
      tb.appendChild(tr);
    }
    t.appendChild(tb);
    content.appendChild(t);
    if (selectedRun) renderRunsDetail(selectedRun);
  }

  async function renderRunsDetail(runId) {
    const host = h("div", { id: "run-detail" });
    const prev = el("run-detail");
    if (prev) prev.remove();
    const content = el("content");
    content.appendChild(host);
    host.appendChild(h("div", { class: "empty" }, "<span class='spin'></span> loading run…"));
    let run;
    try { run = await api.get("/api/runs/" + runId); }
    catch (e) { host.innerHTML = ""; host.appendChild(h("div", { class: "empty" }, "failed: " + e.message)); return; }

    host.innerHTML = "";
    host.appendChild(h("h3", { class: "mt24 mb" }, "Run " + runId +
      (run.manifest && run.manifest.period ? "  (" + run.manifest.period.join(" → ") + ")" : "")));

    // per-spec KPI cards
    const grid = h("div", { class: "grid" });
    for (const s of run.specs) {
      const m = s.metrics || {};
      const cls = (v) => v > 0 ? "ok" : v < 0 ? "err" : "";
      const card = h("div", { class: "card" },
        h("div", { class: "card-title flex" },
          h("span", { class: "mono", style: "flex:1" }, s.spec_id),
          s.error_flag ? chip("error", "err") : chip("ok", "ok")),
        h("div", { class: "grid" },
          kpi("Return", fmt.pct(m.total_return_pct), cls(m.total_return_pct)),
          kpi("Max DD", fmt.pct(m.max_drawdown_pct), m.max_drawdown_pct > 15 ? "err" : "warn"),
          kpi("Sharpe", fmt.num(m.sharpe_ratio)),
          kpi("Sortino", fmt.num(m.sortino_ratio)),
          kpi("Calmar", fmt.num(m.calmar_ratio)),
          kpi("SQN", fmt.num(m.sqn)),
          kpi("Win rate", fmt.pct(m.win_rate)),
          kpi("PF", fmt.num(m.profit_factor)),
          kpi("Trades", m.total_trades ?? "—"),
          kpi("Commissions $", fmt.num(m.total_commissions)),
          kpi("Buy&Hold", fmt.pct(m.buy_and_hold_return_pct), cls(m.buy_and_hold_return_pct)),
        ),
        h("div", { class: "code mt" },
          (s.condition || "—") + "\n" +
          [s.timeframe, s.mechanism_class, s.thesis_id].filter(Boolean).join("  ·  ")),
      );
      grid.appendChild(card);
    }
    host.appendChild(grid);

    // return vs buy&hold chart
    host.appendChild(h("div", { class: "card mt24" },
      h("div", { class: "card-title" }, "Strategy return vs buy-and-hold (%)"),
      h("div", { id: "run-chart", class: "chart" })));
    const names = run.specs.map((s) => s.spec_id.slice(0, 8));
    chart("run-chart", {
      backgroundColor: "transparent",
      tooltip: { trigger: "axis" },
      legend: { data: ["strategy", "buy&hold"], textStyle: { color: "#8b949e" } },
      grid: { left: 40, right: 20, top: 40, bottom: 60 },
      xAxis: { type: "category", data: names, axisLabel: { color: "#8b949e", rotate: 45 } },
      yAxis: { type: "value", name: "%", axisLabel: { color: "#8b949e" }, splitLine: { lineStyle: { color: "#2d3748" } } },
      series: [
        { name: "strategy", type: "bar", data: run.specs.map((s) => (s.metrics || {}).total_return_pct), itemStyle: { color: "#4f8cff" } },
        { name: "buy&hold", type: "bar", data: run.specs.map((s) => (s.metrics || {}).buy_and_hold_return_pct), itemStyle: { color: "#8b949e" } },
      ],
    });
  }

  tabs.runs = {
    title: "Runs",
    render: (content) => renderRuns(content),
  };

  // ------------------------------------------------------------- 2. Audit
  async function renderAudit(content) {
    content.innerHTML = "";
    const gens = await apiCall(() => api.get("/api/audit"));
    if (!gens.length) { content.appendChild(h("div", { class: "empty" }, "no audit generations yet")); return; }
    content.appendChild(h("div", { class: "card mb" },
      h("div", { class: "card-title" }, "PASS / FAIL / INCONCLUSIVE per generation"),
      h("div", { id: "audit-chart", class: "chart" })));
    const labels = gens.map((g) => g.generation);
    const sum = gens.map((g) => (g.summary || {}));
    chart("audit-chart", {
      backgroundColor: "transparent",
      tooltip: { trigger: "axis" },
      legend: { data: ["PASS", "FAIL", "INCONCLUSIVE"], textStyle: { color: "#8b949e" } },
      grid: { left: 40, right: 20, top: 40, bottom: 60 },
      xAxis: { type: "category", data: labels, axisLabel: { color: "#8b949e", rotate: 30 } },
      yAxis: { type: "value", axisLabel: { color: "#8b949e" }, splitLine: { lineStyle: { color: "#2d3748" } } },
      series: [
        { name: "PASS", type: "bar", stack: "s", data: sum.map((s) => s.PASS || 0), itemStyle: { color: "#3fb950" } },
        { name: "FAIL", type: "bar", stack: "s", data: sum.map((s) => s.FAIL || 0), itemStyle: { color: "#f85149" } },
        { name: "INCONCLUSIVE", type: "bar", stack: "s", data: sum.map((s) => s.INCONCLUSIVE || 0), itemStyle: { color: "#d29922" } },
      ],
    });

    // failed items with drill-down (lazy: full metrics fetched on click)
    content.appendChild(h("div", { class: "card" },
      h("div", { class: "card-title" }, "Failed items (click to inspect)"),
      buildFailTable(gens)));
  }

  function buildFailTable(gens) {
    const t = h("table", { class: "data" });
    t.appendChild(h("thead", null, h("tr", null,
      ...["spec_id", "generation", "result", "return", "maxDD", "trades", "PF", "win%"].map((x) => h("th", null, x)))));
    const tb = h("tbody");
    const rows = [];
    for (const g of gens) {
      for (const it of (g.items || [])) {
        const status = (it.result || {}).status || "?";
        if (status === "PASS") continue;
        rows.push({ g, it, status });
      }
    }
    if (!rows.length) {
      tb.appendChild(h("tr", null, h("td", { colspan: "8" },
        h("div", { class: "empty" }, "no failed items"))));
    }
    for (const { g, it, status } of rows) {
      const m = it.metrics || {};
      const tr = h("tr", { class: "row" },
        h("td", { class: "mono" }, it.spec_id),
        h("td", null, g.generation),
        h("td", null, chip(status, status === "FAIL" ? "err" : "warn")),
        h("td", { class: "num" }, fmt.pct(m.total_return_pct)),
        h("td", { class: "num" }, fmt.pct(m.max_drawdown_pct)),
        h("td", { class: "num" }, m.total_trades === undefined ? "—" : m.total_trades),
        h("td", { class: "num" }, fmt.num(m.profit_factor)),
        h("td", { class: "num" }, fmt.pct(m.win_rate)));
      const detail = h("div", { class: "code mt" }, "click to load full metrics…");
      detail.style.display = "none";
      let loaded = false;
      tr.addEventListener("click", () => {
        detail.style.display = detail.style.display === "none" ? "block" : "none";
        if (detail.style.display === "none") return;
        if (loaded) return;
        detail.textContent = "loading…";
        api.get(`/api/audit/${encodeURIComponent(g.generation)}/${encodeURIComponent(it.spec_id)}`)
          .then((full) => {
            loaded = true;
            detail.textContent = JSON.stringify(full.metrics, null, 1).slice(0, 4000);
          })
          .catch((e) => { loaded = true; detail.textContent = "failed: " + e.message; });
      });
      tb.appendChild(tr);
      const trd = h("tr");
      const td = h("td", { colspan: "8" });
      td.appendChild(detail);
      trd.appendChild(td);
      tb.appendChild(trd);
    }
    t.appendChild(tb);
    return t;
  }
  tabs.audit = { title: "Audit", render: renderAudit };

  // ---------------------------------------------------------- 3. Evolution
  async function renderEvolution(content) {
    content.innerHTML = "";
    const ev = await apiCall(() => api.get("/api/evolution"));
    if (!ev || !ev.baseline_genome) {
      content.appendChild(h("div", { class: "empty" }, "no evolution state yet (run `evolve` first)"));
      return;
    }
    const bf = ev.best_fitness || {};
    const baf = ev.baseline_fitness_at_eval || {};
    content.appendChild(h("div", { class: "grid mb" },
      kpi("Best score", fmt.num(bf.score), "ok"),
      kpi("Baseline score (at eval)", fmt.num(baf.score), "warn"),
      kpi("Pass rate (best)", fmt.pct((bf.pass_rate || 0) * 100)),
      ev.improved_over_baseline ? h("div", { class: "kpi" },
        h("div", { class: "kpi-label" }, "Improved over baseline"),
        h("div", { class: "kpi-value ok" }, "yes")) :
        h("div", { class: "kpi" },
          h("div", { class: "kpi-label" }, "Improved over baseline"),
          h("div", { class: "kpi-value err" }, "no")),
      ev.auto_applied ? h("div", { class: "kpi" },
        h("div", { class: "kpi-label" }, "Auto-applied"),
        h("div", { class: "kpi-value ok" }, "yes")) :
        h("div", { class: "kpi" },
          h("div", { class: "kpi-label" }, "Auto-applied"),
          h("div", { class: "kpi-value dim" }, "no")),
    ));

    // history line chart
    const hist = ev.history || [];
    content.appendChild(h("div", { class: "card mb" },
      h("div", { class: "card-title" }, "Genome fitness over generations (best per gen)"),
      h("div", { id: "evo-chart", class: "chart" })));
    const genLabels = hist.map((g) => "gen " + (g.generation + 1));
    const bestPerGen = hist.map((g) => {
      const gs = (g.genomes || []).map((x) => ({ id: x.genome_id, score: (x.fitness || {}).score }));
      return gs.length ? Math.max(...gs.map((x) => x.score)) : null;
    });
    const allSeries = hist.flatMap((g, gi) =>
      (g.genomes || []).map((x) => [genLabels[gi], (x.fitness || {}).score]));
    chart("evo-chart", {
      backgroundColor: "transparent",
      tooltip: { trigger: "axis" },
      grid: { left: 40, right: 20, top: 30, bottom: 40 },
      xAxis: { type: "category", data: genLabels, axisLabel: { color: "#8b949e" } },
      yAxis: { type: "value", name: "score", axisLabel: { color: "#8b949e" }, splitLine: { lineStyle: { color: "#2d3748" } } },
      series: [{
        name: "best per gen", type: "line", data: bestPerGen,
        itemStyle: { color: "#3fb950" }, lineStyle: { color: "#3fb950" },
        areaStyle: { color: "rgba(63,185,80,.12)" },
        markPoint: { data: allSeries.length ? [] : [] },
      }],
    });

    // best genome params
    const bg = ev.best_genome || {};
    const rows = Object.entries(bg).filter(([k]) => k !== "genome_id").map(([k, v]) => [k, typeof v === "object" ? JSON.stringify(v) : v]);
    content.appendChild(h("div", { class: "card" },
      h("div", { class: "card-title" }, "Best genome — " + (bg.genome_id || "")),
      kv(rows)));
  }
  tabs.evolution = { title: "Evolution", render: renderEvolution };

  // ---------------------------------------------------------- 4. Strategies
  let editingSpec = null;

  async function renderStrategies(content) {
    content.innerHTML = "";
    const specs = await apiCall(() => api.get("/api/specs"));
    if (!specs.length) { content.appendChild(h("div", { class: "empty" }, "no strategies yet")); return; }

    const t = h("table", { class: "data" });
    t.appendChild(h("thead", null, h("tr", null,
      ...["spec_id", "source", "tf", "mechanism", "condition"].map((x) => h("th", null, x)))));
    const tb = h("tbody");
    for (const s of specs) {
      const tr = h("tr", { class: "row", onclick: () => { editingSpec = s.spec_id; renderStrategyEditor(s.spec_id); } },
        h("td", { class: "mono" }, s.spec_id),
        h("td", { class: "dim" }, s.source || "template"),
        h("td", null, chip(s.timeframe, "info")),
        h("td", null, s.mechanism_class || "—"),
        h("td", { class: "dim" }, fmt.trunc(s.condition, 70)),
      );
      tb.appendChild(tr);
    }
    t.appendChild(tb);
    content.appendChild(t);
    if (editingSpec) renderStrategyEditor(editingSpec);
  }

  async function renderStrategyEditor(specId) {
    const host = h("div", { id: "spec-editor" });
    const prev = el("spec-editor");
    if (prev) prev.remove();
    el("content").appendChild(host);
    host.appendChild(h("div", { class: "empty" }, "<span class='spin'></span> loading spec…"));
    let spec;
    try { spec = await api.get("/api/specs/" + specId); }
    catch (e) { host.innerHTML = ""; host.appendChild(h("div", { class: "empty" }, "failed: " + e.message)); return; }

    host.innerHTML = "";
    host.appendChild(h("h3", { class: "mt24 mb" }, "Spec " + specId));
    const detail = h("div", { class: "detail" });

    // left: form
    const cond = (spec.entry_rules || []).map((r) => r.condition).join("\n");
    const form = h("div", { class: "form" });
    const tfSel = h("select", null,
      ...["M1", "M15", "H1", "H4", "D1"].map((tf) =>
        h("option", { value: tf, selected: spec.timeframe === tf ? "" : null }, tf)));
    const mechInp = h("input", { value: spec.mechanism_class || "" });
    const lotsInp = h("input", { type: "number", step: "0.01", min: "0.01",
      value: (spec.sizing && spec.sizing.lots) || 0.01 });
    const condTa = h("textarea", { rows: "4" }, cond);
    form.appendChild(h("div", { class: "row" }, h("label", null, "timeframe"), tfSel));
    form.appendChild(h("div", { class: "row" }, h("label", null, "mechanism_class"), mechInp));
    form.appendChild(h("div", { class: "row" }, h("label", null, "lots (fixed)"), lotsInp));
    form.appendChild(h("div", { class: "row" }, h("label", { style: "align-self:start" }, "entry condition(s)"), condTa));
    const saveBtn = h("button", { class: "btn", onclick: doSave }, "Save (re-validated, fail-closed)");
    const archBtn = h("button", { class: "btn danger", onclick: doArchive }, "Archive");
    form.appendChild(h("div", { class: "row" }, saveBtn, archBtn));

    // right: full json
    const jsonBox = h("pre", { class: "code" }, JSON.stringify(spec, null, 2));
    const newBtn = h("button", { class: "btn secondary mt", onclick: doNew }, "+ New spec (from this form)");

    detail.appendChild(card("Edit", form, newBtn));
    detail.appendChild(card("Full spec (current)", jsonBox));
    host.appendChild(detail);

    async function doSave() {
      saveBtn.disabled = true;
      try {
        const updated = await api.patch("/api/specs/" + specId, {
          timeframe: tfSel.value,
          mechanism_class: mechInp.value || undefined,
          sizing: { mode: "fixed_lot", lots: parseFloat(lotsInp.value) },
          entry_rules: [{ direction: (spec.entry_rules[0] || {}).direction || "long", condition: condTa.value.trim() }],
        });
        jsonBox.textContent = JSON.stringify(updated, null, 2);
        toast("spec " + specId + " saved (passed validation)", "ok");
      } catch (e) {
        toast("rejected: " + e.message, "err");
      } finally {
        saveBtn.disabled = false;
      }
    }
    async function doArchive() {
      if (!confirm("Archive spec " + specId + "? (moved to archive/, never deleted)")) return;
      try {
        const r = await api.post("/api/specs/" + specId + "/archive");
        toast("archived to " + r.archived_to, "ok");
        editingSpec = null;
        renderStrategies(el("content"));
      } catch (e) { toast(e.message, "err"); }
    }
    async function doNew() {
      const id = prompt("New spec_id (4-24 lowercase alphanumerics):", "ui" + Date.now().toString(36).slice(-6));
      if (!id) return;
      try {
        const r = await api.post("/api/specs", { spec: {
          spec_id: id,
          thesis_id: spec.thesis_id ? spec.thesis_id + "_copy" : "ui_spec",
          mechanism_class: mechInp.value || spec.mechanism_class,
          timeframe: tfSel.value,
          indicators: spec.indicators,
          entry_rules: [{ direction: "long", condition: condTa.value.trim() }],
          exit_rules: spec.exit_rules,
          risk: spec.risk,
          sizing: { mode: "fixed_lot", lots: parseFloat(lotsInp.value) },
        } });
        toast("created " + r.spec_id, "ok");
        editingSpec = r.spec_id;
        renderStrategies(el("content"));
      } catch (e) { toast("rejected: " + e.message, "err"); }
    }
  }
  tabs.strategies = { title: "Strategies", render: renderStrategies };

  // ---------------------------------------------------------------- init
  async function init() {
    // header git sha
    try {
      const h2 = await api.get("/api/health");
      el("hdr-git").textContent = "git " + h2.git_sha +
        "  ·  " + h2.counts.runs + " runs · " + h2.counts.specs + " specs · " +
        h2.counts.audit_gens + " audit gens";
    } catch (e) { /* banner shown on tab render */ }

    renderTabButtons();
    showTab("runs");

    // 10s auto-refresh for live tabs
    setInterval(() => {
      if (["runs", "audit", "evolution"].includes(activeTab) && !busy) {
        const wasSelectedRun = selectedRun;
        renderActive();
        // re-apply run detail selection after re-render (runs tab re-renders it)
        if (activeTab === "runs" && wasSelectedRun) {
          // renderRuns already re-renders detail when selectedRun is set
        }
      }
    }, 10000);

    // control-plane tabs (5-8) registered by app2.js after us
    if (window.UI_CONTROL && window.UI_CONTROL.init) {
      window.UI_CONTROL.init();
      renderTabButtons();
    }
    startJobsPolling();
  }

  document.addEventListener("DOMContentLoaded", init);

  // ---------------------------------------------------------------- public
  return {
    api, el, h, chip, toast, chart, fmt,
    tabs, showTab, renderTabButtons,
    kpi, card, table, kv, apiCall,
    onJobsTick, startJobsPolling,
    get activeTab() { return activeTab; },
    set selectedRun(v) { selectedRun = v; },
    get selectedRun() { return selectedRun; },
  };
})();
