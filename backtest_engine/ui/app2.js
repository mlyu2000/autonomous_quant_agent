/* Quant Agent Control Plane — control tabs 5-8:
   Generate & Run, Genome, Review Board, Knowledge Base.
   Loaded AFTER app.js; calls into window.UI. */
"use strict";

window.UI_CONTROL = (function () {
  const U = window.UI;
  const api = U.api;

  // ==================================================== 5. Generate & Run
  const COMMANDS = {
    "validate-data": { label: "Validate data", params: [] },
    generate: { label: "Generate specs", params: [
      { key: "count", label: "count", type: "number", def: 8 },
      { key: "use_genome", label: "use genome", type: "checkbox", def: false } ] },
    "generate-kb": { label: "Generate from KB (Neo4j)", params: [
      { key: "count", label: "count", type: "number", def: 8 } ] },
    intake: { label: "Intake knowledge (internet/LLM)", params: [
      { key: "source", label: "source (internet|llm|all)", type: "text", def: "all" },
      { key: "limit_per_feed", label: "items/feed", type: "number", def: 4 },
      { key: "count", label: "llm count", type: "number", def: 3 } ] },
    backtest: { label: "Backtest all specs", params: [
      { key: "spec_dir", label: "spec dir", type: "text", def: "" },
      { key: "out", label: "out dir", type: "text", def: "" } ] },
    "audit-gen": { label: "Audit latest run", params: [
      { key: "generation", label: "label (optional)", type: "text", def: "" } ] },
    evolve: { label: "Evolve genome (EA)", params: [
      { key: "generations", label: "generations", type: "number", def: 1 },
      { key: "pop_size", label: "pop size", type: "number", def: 4 },
      { key: "specs_per_genome", label: "specs/genome", type: "number", def: 2 },
      { key: "seed", label: "seed (optional)", type: "number", def: "" } ] },
  };

  let jobsTableHost = null;
  let runningJobs = false;

  function statusChip(s) {
    const map = { running: "warn", done: "ok", failed: "err", killed: "dim", pending: "info" };
    const c = U.chip(s, map[s] || "dim");
    if (s === "running") c.appendChild(document.createTextNode(" "));
    c.appendChild(U.el("x") || hSpin());
    return c;
  }
  function hSpin() { return U.h("span", { class: "spin" }); }

  function buildJobsTable(jobs) {
    const t = U.h("table", { class: "data" });
    t.appendChild(U.h("thead", null, U.h("tr", null,
      ...["job", "command", "params", "status", "started", "finished", "exit", ""].map((x) => U.h("th", null, x)))));
    const tb = U.h("tbody");
    if (!jobs.length) {
      t.appendChild(U.h("tbody", null, U.h("tr", null,
        U.h("td", { colspan: "8" }, U.h("div", { class: "empty" }, "no jobs yet")))));
      return t;
    }
    for (const j of jobs) {
      const tr = U.h("tr", { class: "row", onclick: (e) => {
        if (e.target.closest("button")) return;
        toggleLog(j.job_id);
      } },
        U.h("td", { class: "mono" }, j.job_id),
        U.h("td", null, j.command),
        U.h("td", { class: "dim" }, U.fmt.trunc(JSON.stringify(j.params), 40)),
        U.h("td", null, (function () {
          const c = U.chip(j.status, { running: "warn", done: "ok", failed: "err", killed: "dim", pending: "info" }[j.status] || "dim");
          if (j.status === "running") c.appendChild(U.h("span", { class: "spin" }));
          return c; })()),
        U.h("td", null, U.fmt.dt(j.started_at)),
        U.h("td", null, U.fmt.dt(j.finished_at)),
        U.h("td", { class: "num" }, j.exit_code === null ? "—" : j.exit_code),
        U.h("td", null, j.status === "running"
          ? U.h("button", { class: "btn danger small", onclick: (ev) => {
              ev.stopPropagation();
              killJob(j.job_id);
            } }, "Kill")
          : U.h("button", { class: "btn secondary small" }, "log")));
      const logRow = U.h("tr", { id: "logrow-" + j.job_id, style: "display:none" });
      const logTd = U.h("td", { colspan: "8" });
      logTd.appendChild(U.h("div", { class: "log", id: "log-" + j.job_id }, "loading…"));
      logRow.appendChild(logTd);
      tb.appendChild(tr);
      tb.appendChild(logRow);
    }
    t.appendChild(tb);
    return t;
  }

  async function toggleLog(jobId) {
    const row = U.el("logrow-" + jobId);
    if (!row) return;
    if (row.style.display !== "none") { row.style.display = "none"; return; }
    row.style.display = "block";
    try {
      const j = await api.get("/api/jobs/" + jobId);
      const box = U.el("log-" + jobId);
      if (box) box.textContent = (j.log || "") + "\n--- stderr ---\n" + (j.stderr || "");
    } catch (e) {
      const box = U.el("log-" + jobId);
      if (box) box.textContent = "failed: " + e.message;
    }
  }

  async function killJob(jobId) {
    if (!confirm("Request kill for job " + jobId + "? (cooperative — takes effect when the command returns)")) return;
    try {
      const r = await api.post("/api/jobs/" + jobId + "/kill");
      U.toast(r.killed ? "kill requested" : r.reason, r.killed ? "ok" : "err");
      refreshJobs();
    } catch (e) { U.toast(e.message, "err"); }
  }

  async function refreshJobs() {
    let jobs;
    try { jobs = await api.get("/api/jobs"); } catch (e) { return; }
    runningJobs = jobs.some((j) => j.status === "running" || j.status === "pending");
    if (jobsTableHost) {
      jobsTableHost.innerHTML = "";
      jobsTableHost.appendChild(buildJobsTable(jobs));
    }
  }

  function startJob(command) {
    const cfg = COMMANDS[command];
    const params = {};
    for (const p of cfg.params) {
      const inp = U.el("jp-" + command + "-" + p.key);
      if (!inp) continue;
      if (p.type === "checkbox") { if (inp.checked) params[p.key] = true; }
      else if (inp.value === "") continue;
      else if (p.type === "number") params[p.key] = Number(inp.value);
      else params[p.key] = inp.value;
    }
    api.post("/api/jobs", { command, params })
      .then((r) => { U.toast("job started: " + r.job_id, "ok"); refreshJobs(); })
      .catch((e) => U.toast(e.message, "err"));
  }

  function buildRunForm() {
    const form = U.h("div", { class: "form" });
    for (const [cmd, cfg] of Object.entries(COMMANDS)) {
      const row = U.h("div", { class: "row" });
      row.appendChild(U.h("label", { style: "min-width:200px" }, cfg.label));
      for (const p of cfg.params) {
        const id = "jp-" + cmd + "-" + p.key;
        if (p.type === "checkbox") {
          row.appendChild(U.h("label", { style: "min-width:0" },
            U.h("input", { type: "checkbox", id, checked: p.def ? "" : null }), " " + p.label));
        } else {
          row.appendChild(U.h("input", {
            type: p.type === "number" ? "number" : "text",
            id, placeholder: p.label, value: p.def || "",
          }));
        }
      }
      row.appendChild(U.h("button", { class: "btn small", onclick: () => startJob(cmd) }, "Start"));
      form.appendChild(row);
    }
    return form;
  }

  async function renderGenerateRun(content) {
    content.innerHTML = "";
    content.appendChild(U.card("Start a pipeline job",
      buildRunForm(),
      U.h("div", { class: "flex mt" },
        U.h("button", { class: "btn secondary small", onclick: refreshJobs }, "Refresh jobs"))));
    jobsTableHost = U.h("div");
    content.appendChild(U.card("Jobs (click a row to expand its log)", jobsTableHost));
    await refreshJobs();
    // live refresh while running
    U.onJobsTick(refreshJobs);
  }
  U.tabs.jobs = {
    title: "Generate & Run",
    render: renderGenerateRun,
  };

  // ==================================================== 6. Genome
  const GENES = [
    ["lookback_min", "lookback min"], ["lookback_max", "lookback max"],
    ["tp_min", "tp min"], ["tp_max", "tp max"],
    ["sl_min", "sl min"], ["sl_max", "sl max"],
    ["tp_delta", "tp delta"], ["sl_delta", "sl delta"],
    ["bbands_dev_min", "bbands dev min"], ["bbands_dev_max", "bbands dev max"],
    ["mutation_rate", "mutation rate"],
  ];

  async function renderGenome(content) {
    content.innerHTML = "";
    let ev;
    try { ev = await api.get("/api/evolution"); }
    catch (e) { content.appendChild(U.h("div", { class: "empty" }, e.message)); return; }
    const best = ev.best_genome || {};
    const bf = ev.best_fitness || {};
    const baf = ev.baseline_fitness_at_eval || {};

    content.appendChild(U.h("div", { class: "grid mb" },
      U.kpi("Best score", U.fmt.num(bf.score), "ok"),
      U.kpi("Baseline score", U.fmt.num(baf.score), "warn"),
      U.kpi("Improved", ev.improved_over_baseline ? "yes" : "no", ev.improved_over_baseline ? "ok" : "err"),
      U.kpi("Auto-applied", ev.auto_applied ? "yes" : "no", ev.auto_applied ? "ok" : "dim")));

    const form = U.h("div", { class: "form" });
    for (const [key, label] of GENES) {
      form.appendChild(U.h("div", { class: "row" },
        U.h("label", null, label),
        U.h("input", { type: "number", step: "any", id: "g-" + key,
          value: best[key] === undefined ? "" : best[key] })));
    }
    form.appendChild(U.h("div", { class: "row" },
      U.h("label", null, "seed (optional)"),
      U.h("input", { type: "number", id: "g-seed", value: best.seed === undefined ? "" : best.seed })));
    form.appendChild(U.h("div", { class: "row" },
      U.h("label", null, "reason"),
      U.h("input", { type: "text", id: "g-reason", placeholder: "why this edit?" })));
    const verdict = U.h("div", { id: "genome-verdict", class: "mt" });
    const applyBtn = U.h("button", { class: "btn", onclick: applyGenome }, "Apply genome edit");
    form.appendChild(U.h("div", { class: "row" }, applyBtn));
    content.appendChild(U.card("Edit the builder genome (governance-gated)",
      form, verdict,
      U.h("div", { class: "dim small mt" },
        "LOW-risk (all genes within declared bounds) → auto-applied to the evolution state. " +
        "HIGH-risk (out of bounds / unknown field) → NOT applied; stored in the rejected-proposal store for the 5-generation re-review cycle.")));

    async function applyGenome() {
      applyBtn.disabled = true;
      verdict.innerHTML = "";
      try {
        const genome = {};
        for (const [key] of GENES) {
          const v = U.el("g-" + key).value;
          if (v !== "") genome[key] = Number(v);
        }
        const seed = U.el("g-seed").value;
        if (seed !== "") genome.seed = Number(seed);
        genome.lookback_deltas = [1, 2, 5];
        const r = await api.post("/api/controls/genome",
          { genome, reason: U.el("g-reason").value || "UI genome edit" });
        if (r.risk === "LOW" && r.applied) {
          verdict.appendChild(U.h("div", { class: "chip ok", style: "font-size:14px;padding:8px 14px" },
            "LOW risk — APPLIED to evolution state (genome " + r.genome_id + ")"));
          U.toast("genome applied (LOW risk)", "ok");
        } else {
          verdict.appendChild(U.h("div", { class: "chip warn", style: "font-size:14px;padding:8px 14px" },
            "HIGH risk — NOT applied. " + r.reason + "  (stored as " + (r.stored_id || "?") +
            ", next review gen " + (r.next_review_gen ?? "?") + ")"));
          U.toast("HIGH-risk edit stored, not applied", "err");
        }
      } catch (e) {
        verdict.appendChild(U.h("div", { class: "chip err" }, e.message));
        U.toast(e.message, "err");
      } finally {
        applyBtn.disabled = false;
      }
    }
  }
  U.tabs.genome = { title: "Genome", render: renderGenome };

  // ==================================================== 7. Review Board
  async function renderReview(content) {
    content.innerHTML = "";
    let gov;
    try { gov = await api.get("/api/governance"); }
    catch (e) { content.appendChild(U.h("div", { class: "empty" }, e.message)); return; }

    content.appendChild(U.card("Protected surfaces (read-only — the UI can never modify these)",
      U.h("div", { class: "flex" },
        (gov.protected_paths || []).map((p) => U.h("span", { class: "code" }, p)))));

    const due = new Set((gov.due_for_review || []).map((e) => e.id));
    content.appendChild(U.card(
      "Rejected HIGH-risk proposals (store: results/rejected_proposals.jsonl)",
      buildRejectedTable(gov.rejected || [], due, inspectProposal)));

    const board = U.h("div", { id: "review-board" });
    content.appendChild(U.card("Disposition (5-generation re-review cycle)", board));
    board.appendChild(U.h("div", { class: "dim small" },
      "Select a proposal above to approve (accept manually, out of cycle) or hold (keep cycling)."));

    function inspectProposal(e) {
      board.innerHTML = "";
      board.appendChild(U.h("div", { class: "code" }, JSON.stringify(e.detail || e, null, 2).slice(0, 4000)));
      const flex = U.h("div", { class: "flex mt" });
      flex.appendChild(U.h("button", { class: "btn", onclick: () => disposition([e.id], "approve") }, "Approve (accept manually)"));
      flex.appendChild(U.h("button", { class: "btn secondary", onclick: () => disposition([e.id], "hold") }, "Hold (keep cycling)"));
      flex.appendChild(U.h("button", { class: "btn danger", onclick: () => disposition([e.id], "reject") }, "Reject (keep cycling)"));
      board.appendChild(flex);
    }

    async function disposition(ids, disp) {
      if (!confirm(disp + " " + ids.length + " proposal(s)?")) return;
      try {
        const r = await api.post("/api/governance/review", { entries: ids, disposition: disp });
        U.toast(r.note + " (" + r.reviewed + " reviewed)", "ok");
        renderReview(content);
      } catch (e2) { U.toast(e2.message, "err"); }
    }
  }
  U.tabs.review = { title: "Review Board", render: renderReview };

  // ==================================================== 8. Knowledge Base
  async function renderKB(content) {
    content.innerHTML = "";
    const [kb, lake, manifest, intake] = await Promise.all([
      api.get("/api/kb"), api.get("/api/data-lake"), api.get("/api/manifest"),
      api.get("/api/intake"),
    ]);
    const stats = kb.last_run;
    content.appendChild(U.card("Knowledge base (Neo4j) — last generate-kb run",
      stats
        ? U.h("div", { class: "grid" },
            U.kpi("Connected", stats.connected ? "yes" : "no", stats.connected ? "ok" : "err"),
            U.kpi("Pulled", stats.pulled, "info"),
            U.kpi("Mapped", stats.mapped, "ok"),
            U.kpi("Skipped (unmappable)", stats.skipped_unmappable, "warn"),
            U.kpi("Rejected (validation)", stats.rejected_validation, stats.rejected_validation ? "err" : "dim"),
            U.kpi("Deduped", stats.deduped))
        : U.h("div", { class: "empty" }, "no generate-kb run yet (use Generate & Run)")));

    // Continuous knowledge intake (internet RSS + LLM model) -> Neo4j
    content.appendChild(U.card("Continuous knowledge intake (internet + LLM)",
      U.h("div", null,
        U.h("div", { class: "grid" },
          U.kpi("Last run", intake.last_run ? intake.last_run.replace("T", " ").replace("+00:00", "Z") : "never", "info"),
          U.kpi("Strategies ingested", intake.ingested_count, "ok"),
          U.kpi("From internet", intake.totals?.internet ?? 0, "dim"),
          U.kpi("From LLM model", intake.totals?.llm ?? 0, "dim")),
        U.h("p", { class: "dim" },
          "Pulls new trading knowledge from RSS feeds + the LLM every cycle, extracts it via the " +
          "6-layer schema, embeds (768d), and stores it in the Neo4j graph. Trigger a single cycle " +
          "from the Generate & Run tab (command: Intake knowledge). Non-strategic content is skipped " +
          "(fail-closed, never faked)."),
        (intake.recent_cycles && intake.recent_cycles.length)
          ? U.h("table", { class: "data" },
              U.h("thead", null, U.h("tr", null,
                ...["time", "source", "strategies", "skipped", "elapsed"].map((x) => U.h("th", null, x)))),
              U.h("tbody", null, intake.recent_cycles.map((c) => U.h("tr", null,
                U.h("td", null, (c.at || "").replace("T", " ").replace("+00:00", "")),
                U.h("td", null, U.chip(c.source, c.source === "llm" ? "warn" : "info")),
                U.h("td", null, String((c.stats?.ingested || []).length)),
                U.h("td", null, String(c.stats?.skipped_nonstrat ?? "—")),
                U.h("td", null, String(c.stats?.elapsed_s ?? "—") + "s")))))
          : U.h("div", { class: "empty" }, "no intake cycles yet (run 'Intake knowledge' in Generate & Run)")))
    );

    content.appendChild(U.card("Data lake (frozen XAUUSD parquet)",
      U.h("table", { class: "data" },
        U.h("thead", null, U.h("tr", null,
          ...["timeframe", "rows", "start", "end", "size MB", "path"].map((x) => U.h("th", null, x)))),
        U.h("tbody", null, lake.map((d) => U.h("tr", null,
          U.h("td", null, U.chip(d.timeframe, "info")),
          U.h("td", { class: "num" }, d.rows),
          U.h("td", null, U.fmt.trunc(d.start, 16) || "—"),
          U.h("td", null, U.fmt.trunc(d.end, 16) || "—"),
          U.h("td", { class: "num" }, d.size_mb),
          U.h("td", { class: "dim mono" }, U.fmt.trunc(d.path, 60))))))));

    const broker = manifest.broker || {};
    const comm = manifest.commission || {};
    const manifestCard = U.card("Data contract (frozen manifest)",
      U.kv([
        ["instrument", manifest.instrument],
        ["timeframes", (manifest.default_timeframes || []).join(", ")],
        ["initial cash", broker.initial_cash],
        ["leverage", broker.leverage],
        ["contract size", broker.contract_size],
        ["lot step", broker.lot_step],
        ["min / max lot", broker.min_lot + " / " + broker.max_lot],
        ["commission mode", comm.mode],
        ["commission value", comm.value + " " + (comm.currency || "")],
      ]));
    const validateBtn = U.h("button", { class: "btn secondary", onclick: () => {
      api.post("/api/jobs", { command: "validate-data", params: {} })
        .then((r) => { U.toast("validate-data job started: " + r.job_id, "ok"); })
        .catch((e) => U.toast(e.message, "err"));
    } }, "Re-run validate-data");
    content.appendChild(U.h("div", { class: "flex" }, manifestCard,
      U.h("div", { class: "card", style: "align-self:flex-start" },
        U.h("div", { class: "card-title" }, "Integrity"), validateBtn,
        U.h("div", { class: "dim small mt" },
          "validate-data checks every timeframe: present, no NaNs, no duplicate " +
          "timestamps, sorted, positive prices, minimum bar counts."))));
  }
  U.tabs.kb = { title: "Knowledge Base", render: renderKB };

  // ---------------------------------------------------------------- init
  function buildRejectedTable(entries, due, onClick) {
    const t = U.h("table", { class: "data" });
    t.appendChild(U.h("thead", null, U.h("tr", null,
      ...["id", "gen", "kind", "reason", "status", "next review", "cadence"].map((x) => U.h("th", null, x)))));
    const tb = U.h("tbody");
    if (!entries.length) {
      tb.appendChild(U.h("tr", null, U.h("td", { colspan: "7" },
        U.h("div", { class: "empty" }, "no rejected proposals yet"))));
    }
    for (const e of entries) {
      tb.appendChild(U.h("tr", { class: "row", onclick: () => onClick(e) },
        U.h("td", { class: "mono" }, e.id),
        U.h("td", { class: "num" }, e.generation),
        U.h("td", null, U.chip(e.kind, "info")),
        U.h("td", { class: "dim" }, U.fmt.trunc(e.reason_rejected || e.description, 60)),
        U.h("td", null, U.chip(e.status, e.status === "pending_review" ? "warn" : "ok")),
        U.h("td", { class: "num" }, e.next_review_gen),
        U.h("td", null, due.has(e.id) ? U.chip("due", "warn") : U.chip("later", "dim"))));
    }
    t.appendChild(tb);
    return t;
  }

  function init() {
    // tabs are registered above (U.tabs.jobs/genome/review/kb)
    U.renderTabButtons();
  }

  return { init };
})();
