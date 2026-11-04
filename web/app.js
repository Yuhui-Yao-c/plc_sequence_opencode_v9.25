"use strict";

let currentRun = null, currentJob = null, calibMode = false, busy = false;
let pollTimer = null, pollGeneration = 0, logOffset = 0, tableGeneration = 0;
const resultPage = { offset: 0, limit: 50 };
const ACTIVE = new Set(["queued", "predicting", "analyzing", "finishing", "cancelling"]);
const LABELS = { queued: "等待启动", predicting: "解析源码", analyzing: "分析工艺与动作描述", finishing: "校验并生成交付", cancelling: "正在停止", succeeded: "已完成", success: "已完成", partial: "部分完成", failed: "失败", incomplete: "批次未完成", interrupted: "任务已中断", cancelled: "已停止", pending: "待处理", running: "运行中" };
function el(id) { return document.getElementById(id); }
function esc(value) { return String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function show(id, visible = true) { el(id).hidden = !visible; el(id).style.display = visible ? "" : "none"; }
function message(text, error = true) {
  const box = el("notice"); box.textContent = text; box.className = `notice ${error ? "notice-error" : "notice-info"}`; box.hidden = !text;
}
async function api(url, options = {}, timeout = 20000) {
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(url, { ...options, signal: controller.signal, cache: "no-store" });
    const data = await response.json().catch(() => { throw new Error("服务返回格式不兼容，请使用当前版本启动入口并刷新页面"); });
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  } catch (error) { if (error.name === "AbortError") throw new Error("连接超时，请检查工作台服务窗口"); throw error; }
  finally { clearTimeout(timer); }
}
function post(url, value = {}, timeout) { return api(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) }, timeout); }
async function checkHealth() {
  el("btnHealth").disabled = true;
  try {
    const h = await api("/api/health?refresh=1", {}, 45000);
    if (h.protocol !== 2) throw new Error("启动了旧版本后端，请关闭旧窗口并运行 run_web.bat");
    el("healthDetails").textContent = h.checks.map(c => `${c.ok ? "✓" : "✗"} ${c.name}：${c.detail}`).join("\n");
    el("healthState").textContent = h.ready ? "环境检查通过" : "环境未就绪，请处理下列项目";
    el("healthState").className = h.ready ? "health-ok" : "health-error";
    el("btnPredict").disabled = !h.ready || busy;
    if (!h.ready) el("healthPanel").open = true;
    return h;
  } catch (error) {
    el("healthState").textContent = "无法连接当前工作台";
    el("healthDetails").textContent = error.message; el("healthPanel").open = true;
    el("btnPredict").disabled = true; message(error.message); return null;
  } finally { el("btnHealth").disabled = false; }
}
function setCalibMode(on) {
  calibMode = on; el("calibCheck").checked = on;
  el("calibState").textContent = on ? "开启" : "关闭";
  show("rowManual", on);
  if (!on) show("sectionCoverage", false);
}
async function loadRuns(resume = false) {
  try {
    const data = await api("/api/runs");
    busy = data.runs.some(r => r.job && ACTIVE.has(r.job.status));
    el("runList").innerHTML = data.runs.length ? data.runs.map(r => {
      const cls = r.finish_status === "success" ? "success" : ["failed", "cancelled", "interrupted", "incomplete"].includes(r.finish_status) ? "failed" : r.finish_status === "running" ? "pending" : "prepared";
      return `<li class="${r.id === currentRun ? "active" : ""}"><button type="button" data-run="${esc(r.id)}" title="${esc(r.path)}"><span class="run-dot ${cls}"></span><span class="run-name">${esc(r.proc_name)}</span><span class="run-state">${esc(LABELS[r.finish_status] || r.finish_status)}</span></button></li>`;
    }).join("") : '<li class="run-empty">暂无任务</li>';
    if (resume) {
      const running = data.runs.find(r => r.job && ACTIVE.has(r.job.status));
      const remembered = localStorage.getItem("plc-current-run");
      const selected = running || data.runs.find(r => r.id === remembered);
      if (selected) await selectRun(selected.id);
    }
    return data.runs;
  } catch (error) { el("runList").innerHTML = '<li class="run-empty">历史列表加载失败</li>'; message(error.message); return []; }
}
function stopPolling() { pollGeneration++; clearTimeout(pollTimer); pollTimer = null; }
async function selectRun(id) {
  stopPolling(); currentRun = id; localStorage.setItem("plc-current-run", id);
  show("emptyState", false); show("sectionNew", false); message("");
  const selection = currentRun;
  try {
    const info = await api(`/api/run/${encodeURIComponent(id)}/status`);
    if (currentRun !== selection) return;
    renderRun(info); await loadRuns();
    if (info.job) startPolling(info.job.id);
    else { currentJob = null; show("sectionProgress", false); }
  } catch (error) { message(`加载失败：${error.message}`); show("sectionProgress", false); }
}
function card(label, value) { return `<div class="card"><div class="card-label">${esc(label)}</div><div class="card-value">${esc(value)}</div></div>`; }
function renderRun(info) {
  el("currentRunName").textContent = info.proc_name;
  el("currentRunStatus").textContent = LABELS[info.finish_status] || info.finish_status;
  el("currentRunStatus").className = `badge ${info.finish_status === "success" ? "badge-success" : "badge-warn"}`;
  show("sectionResultSummary");
  el("cardsContainer").innerHTML = card("候选总数", info.candidate_count) + card("工艺批次", `${info.batches_result}/${info.batches_input}`) + card("描述批次", `${info.bdesc_result}/${info.bdesc_input}`) + (info.final_rows == null ? "" : card("工艺草表行数", info.final_rows));
  const warnings = info.warnings || [];
  const canContinue = info.manifest_ready && !["success", "running"].includes(info.finish_status);
  el("resultSummary").innerHTML = (warnings.length ? `<p class="warning-text">${warnings.map(esc).join("；")}</p>` : "") + (canContinue ? `<div class="resume-box"><div><strong>${esc(LABELS[info.finish_status] || "任务未完成")}</strong><p>工艺剩余 ${Math.max(0, info.batches_input - info.batches_result)} 批；描述剩余 ${Math.max(0, info.bdesc_input - info.bdesc_result)} 批。已有分析结果保留。</p></div><button type="button" class="btn btn-primary" data-continue="${esc(info.id)}">继续处理</button></div>` : "");
  show("sectionCoverage", Boolean(info.coverage));
  if (info.coverage) el("coverageBox").textContent = info.coverage.error ? `覆盖评价失败：${info.coverage.error}` : `覆盖评价已生成${info.coverage.proc_name ? `：${info.coverage.proc_name}` : ""}；下载见交付文件。`;
  show("sectionResult", info.preview_ready);
  if (info.preview_ready) loadPrediction(resultPage.offset);
  show("sectionDownload", (info.files || []).length > 0);
  el("downloadList").innerHTML = (info.files || []).map(f => `<a class="download-item" href="/api/run/${encodeURIComponent(info.id)}/file/${encodeURIComponent(f.path)}" download><span class="download-icon">↓</span><div><div class="download-name">${esc(f.name)}</div><div class="download-desc">${esc(f.label)}</div></div></a>`).join("");
}
async function loadPrediction(offset = 0) {
  if (!currentRun) return;
  const id = currentRun, generation = ++tableGeneration;
  resultPage.offset = offset;
  const params = new URLSearchParams({ offset, limit: resultPage.limit, q: el("searchBox").value.trim() });
  try {
    const data = await api(`/api/run/${encodeURIComponent(id)}/prediction?${params}`);
    if (currentRun !== id || generation !== tableGeneration) return;
    el("resultCount").textContent = `共 ${data.total} 行${data.provisional ? " · 解析预览，尚未交付" : ""}`;
    el("resultBody").innerHTML = data.rows.length ? data.rows.map(r => `<tr>${["构成要素", "动作描述", "动作变量", "开始标签", "结束标签", "备注"].map(key => `<td>${esc(r[key])}</td>`).join("")}</tr>`).join("") : '<tr><td colspan="6" class="empty-cell">无匹配结果</td></tr>';
    const page = Math.floor(data.offset / data.limit), pages = Math.ceil(data.total / data.limit);
    let controls = "";
    if (pages > 1) {
      controls += `<button class="page-btn" data-offset="${Math.max(0, page - 1) * data.limit}" ${page === 0 ? "disabled" : ""}>上一页</button>`;
      controls += `<span>${page + 1} / ${pages}</span>`;
      controls += `<button class="page-btn" data-offset="${(page + 1) * data.limit}" ${page + 1 >= pages ? "disabled" : ""}>下一页</button>`;
    }
    el("pagination").innerHTML = controls;
  } catch (error) { if (currentRun === id) el("resultBody").innerHTML = `<tr><td colspan="6" class="empty-cell">${esc(error.message)}</td></tr>`; }
}
async function browseFile(inputID, mode) {
  try {
    const data = await post("/api/browse", { mode }, 310000);
    if (data.path) el(inputID).value = data.path;
  } catch (error) { message(error.message); el(inputID).focus(); }
}
function showNewTask() {
  stopPolling(); currentRun = null; currentJob = null; message("");
  resultPage.offset = 0; el("searchBox").value = ""; tableGeneration++;
  for (const id of ["emptyState", "sectionResultSummary", "sectionProgress", "sectionResult", "sectionDownload", "sectionCoverage"]) show(id, false);
  show("sectionNew"); el("currentRunName").textContent = "新任务";
  el("currentRunStatus").textContent = busy ? "另有任务运行中" : "新建";
  loadRuns(); checkHealth();
}
async function runPredict() {
  const source = el("inpSource").value.trim(), pattern = el("inpPattern").value.trim();
  if (!source || !pattern) { message("请填写源码路径与匹配格式"); return; }
  if (calibMode && !el("inpManual").value.trim()) { message("校准模式需要人工表路径"); return; }
  el("btnPredict").disabled = true; message("");
  try {
    const data = await post("/api/predict", { source, pattern, output_root: el("inpOutputRoot").value.trim(), trace: el("inpTrace").value.trim(), calibration: calibMode, ...(calibMode ? { manual: el("inpManual").value.trim() } : {}) }, 45000);
    currentRun = data.run_id; localStorage.setItem("plc-current-run", currentRun);
    show("sectionNew", false); show("emptyState", false); show("sectionProgress");
    busy = true; startPolling(data.job_id); loadRuns();
  } catch (error) { message(`启动失败：${error.message}`); el("btnPredict").disabled = false; }
}
async function continueRun(id) {
  message("");
  try { const data = await post(`/api/run/${encodeURIComponent(id)}/continue`, {}, 45000); currentRun = data.run_id; busy = true; startPolling(data.job_id); }
  catch (error) { message(`继续处理失败：${error.message}`); }
}
function startPolling(jobID) {
  stopPolling(); const generation = pollGeneration;
  if (currentJob !== jobID) { logOffset = 0; el("jobLog").textContent = ""; }
  currentJob = jobID; show("sectionProgress");
  const poll = async () => {
    try {
      const job = await api(`/api/job/${encodeURIComponent(jobID)}?offset=${logOffset}`);
      if (generation !== pollGeneration) return;
      logOffset = job.next_offset;
      if (job.output) {
        el("jobLog").textContent = (el("jobLog").textContent + job.output).slice(-60000);
        el("jobLog").scrollTop = el("jobLog").scrollHeight;
      }
      const active = ACTIVE.has(job.status);
      el("phaseHeroName").textContent = LABELS[job.status] || job.status;
      el("phaseHeroDesc").textContent = job.error || (active ? "任务正在执行，日志与两类批次进度会自动更新。" : job.status === "succeeded" ? "交付文件已生成，可在下方下载。" : "请查看日志；已完成的批次会保留。");
      el("phaseHeroIcon").textContent = active ? "⏳" : job.status === "succeeded" ? "✓" : "!";
      el("jobStatusBadge").textContent = LABELS[job.status] || job.status;
      el("btnStop").disabled = !(active || job.orphaned) || job.status === "cancelling";
      el("progressStats").textContent = `工艺 ${job.run.batches_result}/${job.run.batches_input} · 描述 ${job.run.bdesc_result}/${job.run.bdesc_input}`;
      renderRun(job.run);
      if (active) pollTimer = setTimeout(poll, 1500);
      else {
        busy = false; el("btnPredict").disabled = false; await loadRuns();
        // Drain logs if a large final chunk remains before stopping polling.
        if (job.has_more) pollTimer = setTimeout(poll, 100);
      }
    } catch (error) {
      if (generation !== pollGeneration) return;
      el("phaseHeroDesc").textContent = `连接中断：${error.message}。服务恢复后会自动重连。`;
      pollTimer = setTimeout(poll, 3000);
    }
  };
  poll();
}
window.addEventListener("DOMContentLoaded", async () => {
  el("btnNewTask").addEventListener("click", showNewTask);
  el("btnCloseNew").addEventListener("click", () => show("sectionNew", false));
  el("btnPredict").addEventListener("click", runPredict);
  el("btnHealth").addEventListener("click", checkHealth);
  el("calibCheck").addEventListener("change", event => setCalibMode(event.target.checked));
  el("btnStop").addEventListener("click", async () => {
    if (!currentJob) return;
    try { await post(`/api/job/${encodeURIComponent(currentJob)}/stop`); } catch (error) { message(error.message); }
  });
  el("btnCollapseTable").addEventListener("click", () => {
    const open = el("tableArea").style.display === "none";
    show("tableArea", open); el("btnCollapseTable").textContent = open ? "收起" : "展开";
  });
  document.addEventListener("click", event => {
    const button = event.target.closest("[data-run],[data-continue],[data-offset],[data-browse]");
    if (!button || button.disabled) return;
    if (button.dataset.run) selectRun(button.dataset.run);
    else if (button.dataset.continue) continueRun(button.dataset.continue);
    else if (button.dataset.offset != null) loadPrediction(Number(button.dataset.offset));
    else browseFile(button.dataset.browse, button.dataset.mode || "file");
  });
  let searchTimer;
  el("searchBox").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => loadPrediction(0), 300); });
  await checkHealth(); await loadRuns(true);
});
