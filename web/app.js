/* PLC Sequence Agent v9.25 — local workflow console */
const API = "";
const state = {
  currentRun: null,
  currentJobId: null,
  pollTimer: null,
  searchTimer: null,
  pairs: { rows: [], total: 0, page: 1, total_pages: 0 },
  systemReady: false,
  busy: false
};

const $ = (id) => document.getElementById(id);

async function fetchJSON(url, options = {}) {
  const response = await fetch(`${API}${url}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, ch => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[ch]));
}
function basename(value) { return String(value || "").replace(/\\/g, "/").split("/").pop(); }
function stripExt(value) { return String(value || "").replace(/\.[^.]+$/, ""); }
function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
function formatDate(timestamp) {
  if (!timestamp) return "—";
  return new Date(timestamp * 1000).toLocaleString("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"
  });
}
function showToast(message, kind = "info") {
  const el = $("toast");
  el.textContent = message;
  el.dataset.kind = kind;
  el.classList.add("show");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => el.classList.remove("show"), 3000);
}
function show(id) { $(id)?.classList.remove("hidden"); }
function hide(id) { $(id)?.classList.add("hidden"); }

function defaultOutputPath(source) {
  const raw = stripExt(basename(source)).replace(/^\d+[._-]?/, "");
  const clean = raw.replace(/[^\w\u4e00-\u9fff .()-]+/g, "_").trim() || "PLC_Run";
  const d = new Date();
  const stamp = [
    d.getFullYear(),
    String(d.getMonth() + 1).padStart(2, "0"),
    String(d.getDate()).padStart(2, "0"),
    "_",
    String(d.getHours()).padStart(2, "0"),
    String(d.getMinutes()).padStart(2, "0"),
    String(d.getSeconds()).padStart(2, "0")
  ].join("");
  return `outputs/${clean}_${stamp}`;
}

// ---------------------------------------------------------------------------
// System diagnostics
// ---------------------------------------------------------------------------
async function loadSystemStatus() {
  try {
    const data = await fetchJSON("/api/system/status");
    const op = data.opencode || {};
    const ready = Boolean(op.available && op.agents_ready);
    state.systemReady = ready;

    $("runtime-python").textContent = data.python_version || "—";
    $("runtime-flask").textContent = data.flask_version || "—";
    $("runtime-opencode").textContent = op.available ? (op.version || "已安装") : "未就绪";
    $("runtime-agents").textContent = op.agents_ready ? "2 / 2 批次 Agent" : `${(op.agents || []).length} / 2`;
    $("runtime-root").textContent = data.project_root || "—";
    $("runtime-root").title = data.project_root || "";

    const message = op.message || (ready ? "运行环境正常" : "OpenCode 未就绪");
    $("runtime-message").textContent = message;
    $("runtime-message").title = op.binary || "";
    $("system-badge").textContent = ready ? "环境就绪" : "需要配置";
    $("system-badge").className = `status-pill ${ready ? "status-success" : "status-warning"}`;

    $("sidebar-status-dot").className = `status-dot ${ready ? "ok" : "bad"}`;
    $("sidebar-status-text").textContent = ready ? "本地 Agent 已就绪" : "OpenCode 未就绪";
    $("header-system-status").innerHTML = `<span class="status-dot ${ready ? "ok" : "bad"}"></span><span>${escapeHtml(ready ? "本地执行环境已就绪" : "OpenCode 配置待检查")}</span>`;
    $("btn-start").disabled = !ready || state.busy;
    return ready;
  } catch (error) {
    state.systemReady = false;
    $("runtime-message").textContent = error.message;
    $("system-badge").textContent = "服务异常";
    $("system-badge").className = "status-pill status-error";
    $("sidebar-status-dot").className = "status-dot bad";
    $("sidebar-status-text").textContent = "本地服务异常";
    $("header-system-status").innerHTML = '<span class="status-dot bad"></span><span>无法连接本地后端</span>';
    $("btn-start").disabled = true;
    return false;
  }
}

// ---------------------------------------------------------------------------
// File selection
// ---------------------------------------------------------------------------
async function browseFile(target, filter, title) {
  try {
    const data = await fetchJSON("/api/browse", {
      method: "POST",
      body: JSON.stringify({ mode: "file", filter, title })
    });
    if (!data.path) return;
    $(target).value = data.path;
    if (target === "source" && !$("out").value.trim()) {
      $("out").value = defaultOutputPath(data.path);
    }
  } catch (error) {
    showToast(`文件选择失败：${error.message}`, "error");
  }
}

// ---------------------------------------------------------------------------
// Job lifecycle
// ---------------------------------------------------------------------------
function setBusy(value) {
  state.busy = value;
  $("btn-start").disabled = value || !state.systemReady;
  $("btn-cancel").disabled = !value;
  $("btn-cancel").classList.toggle("hidden", !value);
  $("btn-resume").disabled = value;
}
function appendLog(line) {
  const terminal = $("log-output");
  const placeholder = terminal.querySelector(".log-placeholder");
  if (placeholder) placeholder.remove();
  const row = document.createElement("div");
  row.className = "log-line";
  if (/error|错误|失败|traceback|exception|✗/i.test(line)) row.classList.add("log-error");
  else if (/warn|警告|待核验/i.test(line)) row.classList.add("log-warn");
  else if (/✓|完成|成功|已就绪/i.test(line)) row.classList.add("log-success");
  else row.classList.add("log-info");
  row.textContent = line;
  terminal.appendChild(row);
  terminal.scrollTop = terminal.scrollHeight;
}
function setRunStatus(kind, label) {
  $("run-status").textContent = label;
  $("run-status").className = `status-pill status-${kind}`;
}
function updatePipeline(stage, status) {
  const ids = ["step-predict", "step-agent", "step-finish"];
  ids.forEach(id => $(id).classList.remove("active", "done"));
  if (status === "completed") {
    ids.forEach(id => $(id).classList.add("done"));
    return;
  }
  if (status === "failed" || status === "cancelled") return;
  const text = String(stage || "");
  let index = 0;
  if (/Agent 批次|工艺分析|动作描述/i.test(text)) index = 1;
  if (/Finish|交付|校验/i.test(text)) index = 2;
  if (/源码解析|predict/i.test(text)) index = 0;
  ids.forEach((id, i) => {
    if (i < index) $(id).classList.add("done");
    if (i === index) $(id).classList.add("active");
  });
}
function updateJobView(data) {
  $("current-stage").textContent = data.stage || "执行中";
  $("current-item").textContent = data.current_item || "后台正在处理";
  $("log-time").textContent = `${Number(data.elapsed || 0).toFixed(1)}s`;
  const progress = data.progress || {};
  const percent = Math.max(0, Math.min(100, Number(progress.percent || 0)));
  $("progress-percent").textContent = `${Math.round(percent)}%`;
  $("progress-count").textContent = `${progress.done || 0} / ${progress.total || 0}`;
  $("progress-bar").style.width = `${percent}%`;
  updatePipeline(data.stage, data.status);

  if (data.status === "running") {
    setRunStatus("running", "执行中");
  } else if (data.status === "completed") {
    setRunStatus("success", "全部完成");
  } else if (data.status === "cancelled") {
    setRunStatus("warning", "已取消");
  } else if (data.status === "failed") {
    setRunStatus("error", "执行失败");
  }
}
async function startWorkflow(event) {
  event?.preventDefault();
  if (state.busy) return;
  const payload = {
    source: $("source").value.trim(),
    pattern: $("pattern").value.trim() || "*",
    out: $("out").value.trim(),
    trace: $("trace").value.trim(),
    manual: $("manual").value.trim()
  };
  if (!payload.source || !payload.out) {
    showToast("请先填写源码文件和输出目录。", "warning");
    return;
  }
  if (!state.systemReady) {
    showToast("OpenCode Agent 尚未就绪，请先检查运行环境。", "error");
    return;
  }

  $("log-output").innerHTML = "";
  state.currentJobId = null;
  $("run-subtitle").textContent = `运行目录：${payload.out}`;
  $("current-stage").textContent = "正在创建任务";
  $("current-item").textContent = "等待后端启动";
  $("progress-percent").textContent = "0%";
  $("progress-count").textContent = "0 / 0";
  $("progress-bar").style.width = "0%";
  setRunStatus("running", "启动中");
  show("run-panel");
  hide("result-panel");
  setBusy(true);
  updatePipeline("predict", "running");
  $("run-panel").scrollIntoView({ behavior: "smooth", block: "start" });

  try {
    const data = await fetchJSON("/api/workflow/start", {
      method: "POST",
      body: JSON.stringify(payload)
    });
    state.currentJobId = data.job_id;
    state.currentRun = data.run_name;
    $("active-run-tag").textContent = data.run_name;
    $("active-run-content").innerHTML = `<div class="active-run-row"><strong>${escapeHtml(data.run_name)}</strong><span>完整工作流正在执行</span></div>`;
    appendLog(`任务已创建：${data.run_name}`);
    appendLog("OpenCode 主 Agent 已接收任务，将统一调用 predict、批次 Agent 和 finish。");
    startPolling();
    loadHistory();
  } catch (error) {
    setBusy(false);
    setRunStatus("error", "启动失败");
    appendLog(`启动失败：${error.message}`);
    showToast(error.message, "error");
    loadSystemStatus();
  }
}
function startPolling() {
  stopPolling();
  state.pollTimer = setInterval(pollJob, 850);
  pollJob();
}
function stopPolling() {
  if (state.pollTimer) clearInterval(state.pollTimer);
  state.pollTimer = null;
}
async function pollJob() {
  if (!state.currentJobId) return;
  try {
    const data = await fetchJSON(`/api/job/${encodeURIComponent(state.currentJobId)}`);
    (data.new_lines || []).forEach(appendLog);
    updateJobView(data);
    if (data.status === "completed" || data.status === "failed" || data.status === "cancelled") {
      stopPolling();
      setBusy(false);
      if (data.status === "completed") {
        showToast("完整工作流执行完成。", "success");
      } else if (data.status === "failed") {
        showToast(data.error || "工作流执行失败，请查看日志。", "error");
      } else {
        showToast("任务已取消。", "warning");
      }
      if (data.run_name) {
        state.currentRun = data.run_name;
        await loadRunResults(data.run_name);
      }
      await loadHistory();
      await loadSystemStatus();
    }
  } catch (error) {
    console.error("job polling:", error);
  }
}
async function cancelJob() {
  if (!state.currentJobId || !state.busy) return;
  $("btn-cancel").disabled = true;
  try {
    const data = await fetchJSON(`/api/job/${encodeURIComponent(state.currentJobId)}/cancel`, {
      method: "POST", body: JSON.stringify({})
    });
    showToast(data.message || "已请求停止任务。", "warning");
  } catch (error) {
    showToast(error.message, "error");
    $("btn-cancel").disabled = false;
  }
}
function clearLog() {
  $("log-output").innerHTML = '<div class="log-placeholder">日志显示已清空；后台任务继续运行。</div>';
}

// ---------------------------------------------------------------------------
// Run history and resume
// ---------------------------------------------------------------------------
async function loadHistory() {
  try {
    const runs = await fetchJSON("/api/runs");
    const container = $("history-list");
    if (!runs.length) {
      container.innerHTML = '<div class="history-empty">暂无运行记录。<br>完成一次分析后，历史记录会显示在这里。</div>';
      return;
    }
    container.innerHTML = runs.map(run => {
      const active = run.name === state.currentRun ? "active" : "";
      const cls = run.finished ? "finished" : (run.has_manifest ? "predicted" : "failed");
      const label = run.finished ? "已交付" : (run.has_manifest ? ((run.pending || 0) ? `待处理 ${run.pending}` : "待校验 / 交付") : "未完成");
      return `<div class="history-item ${active}" data-open-run="${escapeHtml(run.name)}">
        <div class="history-name"><span class="history-dot ${cls}"></span>${escapeHtml(run.name)}</div>
        <div class="history-meta">${escapeHtml(label)} · ${escapeHtml(formatDate(run.mtime))}</div>
      </div>`;
    }).join("");
  } catch (error) {
    $("history-list").innerHTML = `<div class="history-empty">读取失败：${escapeHtml(error.message)}</div>`;
  }
}
async function resumeRun(runName) {
  if (!runName || state.busy) return;
  try {
    const data = await fetchJSON(`/api/run/${encodeURIComponent(runName)}/resume`, {
      method: "POST", body: JSON.stringify({})
    });
    state.currentRun = data.run_name;
    state.currentJobId = data.job_id;
    $("log-output").innerHTML = "";
    $("run-subtitle").textContent = `恢复运行：${runName}`;
    setRunStatus("running", "恢复中");
    $("current-stage").textContent = "恢复 OpenCode 批次";
    $("current-item").textContent = "检查已有结果，跳过有效批次";
    $("progress-bar").style.width = "0%";
    $("progress-percent").textContent = "0%";
    $("progress-count").textContent = "0 / 0";
    show("run-panel");
    setBusy(true);
    startPolling();
    showToast("已开始恢复未完成批次。", "success");
  } catch (error) {
    showToast(error.message, "error");
  }
}

// ---------------------------------------------------------------------------
// Result dashboard
// ---------------------------------------------------------------------------
async function loadRunResults(runName) {
  if (!runName) return;
  state.currentRun = runName;
  try {
    const info = await fetchJSON(`/api/run/${encodeURIComponent(runName)}/info`);
    show("result-panel");
    $("result-subtitle").textContent = `${runName} · ${basename(info.manifest?.source || "")}`;
    $("result-status").textContent = info.finished ? "交付已完成" : "尚未完成";
    $("result-status").className = `status-pill ${info.finished ? "status-success" : "status-warning"}`;
    renderStats(info);
    renderActiveRun(info);
    const totalPending = (info.batches?.process?.pending || 0) + (info.batches?.b_tier?.pending || 0);
    $("btn-resume").hidden = Boolean(info.finished) || !info.can_resume;
    $("btn-resume").textContent = totalPending ? `继续未完成批次（${totalPending}）` : "继续校验与交付";
    await Promise.all([loadPairs(1), loadBatches(), loadFiles()]);
    $("result-panel").scrollIntoView({ behavior: "smooth", block: "start" });
    loadHistory();
  } catch (error) {
    showToast(`加载运行结果失败：${error.message}`, "error");
  }
}
function renderStats(info) {
  const batch = info.batches || {};
  const proc = batch.process || {};
  const desc = batch.b_tier || {};
  const stats = [
    { label: "完整候选", value: info.candidate_pairs || 0, note: "源码候选档案" },
    { label: "原子对", value: info.atomic_pairs || 0, note: "非复合边界" },
    { label: "采集对", value: info.collection_pairs || 0, note: "动作收尾视图" },
    { label: "A 档描述", value: info.a_desc_rows || 0, note: "源码注释描述" },
    { label: "工艺批次", value: `${proc.completed || 0}/${proc.total || 0}`, note: `待处理 ${proc.pending || 0}` },
    { label: "B 档批次", value: `${desc.completed || 0}/${desc.total || 0}`, note: `待处理 ${desc.pending || 0}` },
    { label: "待核验", value: info.review_rows || 0, note: "边界复核记录" },
    { label: "最终状态", value: info.finished ? "完成" : "进行中", note: info.delivery_xlsx || "尚未生成交付表" }
  ];
  $("stats-grid").innerHTML = stats.map(item => `<div class="stat-card">
    <span>${escapeHtml(item.label)}</span><strong>${escapeHtml(item.value)}</strong><small>${escapeHtml(item.note)}</small>
  </div>`).join("");
}
function renderActiveRun(info) {
  $("active-run-tag").textContent = state.currentRun || "未选择";
  const source = info.manifest?.source || "";
  const proc = info.batches?.process || {};
  const desc = info.batches?.b_tier || {};
  const trace = info.workflow_status?.trace_analysis;
  const coverage = info.workflow_status?.coverage;
  const traceText = trace && trace.matched_pairs !== undefined
    ? `Trace 匹配 ${trace.matched_pairs}/${trace.prediction_pairs}`
    : (info.manifest?.trace ? "Trace 已配置，等待分析结果" : "未配置 Trace");
  const coverageText = info.coverage_xlsx
    ? `覆盖评价：${basename(info.coverage_xlsx)}`
    : (info.manifest?.manual ? (coverage?.error ? `覆盖评价异常：${coverage.error}` : "人工表已配置，评价结果待检查") : "未配置人工评价表");
  const deliveryText = info.finished
    ? '<span class="status-pill status-success">交付表已生成</span>'
    : (info.delivery_error ? `<span class="status-pill status-error">交付表异常：${escapeHtml(info.delivery_error)}</span>` : '<span class="status-pill status-warning">仍有待执行或待校验内容</span>');
  const issues = info.workflow_status?.invalid_issues;
  const issueText = Array.isArray(issues)
    ? (issues.length ? `校验问题 ${issues.length} 条：${issues.slice(0, 2).join("；")}` : "Finish 校验问题：无")
    : "Finish 校验报告尚未生成";
  $("active-run-content").innerHTML = `<div class="active-run-row">
    <strong>${escapeHtml(state.currentRun)}</strong>
    <span>源码：${escapeHtml(basename(source))}</span>
    <span>工艺批次 ${proc.completed || 0}/${proc.total || 0} · B 档 ${desc.completed || 0}/${desc.total || 0}</span>
    <span>${escapeHtml(traceText)}</span>
    <span>${escapeHtml(coverageText)}</span>
    <span>${escapeHtml(issueText)}</span>
    <span>复核队列：${info.review_rows || 0} 条</span>
    ${deliveryText}
  </div>`;
}

// ---------------------------------------------------------------------------
// Pair table
// ---------------------------------------------------------------------------
async function loadPairs(page = 1) {
  if (!state.currentRun) return;
  const search = $("pairs-search").value.trim();
  try {
    const data = await fetchJSON(`/api/run/${encodeURIComponent(state.currentRun)}/pairs?page=${page}&per_page=100&search=${encodeURIComponent(search)}`);
    state.pairs = data;
    renderPairs();
  } catch (error) {
    $("pairs-tbody").innerHTML = `<tr><td class="table-empty">${escapeHtml(error.message)}</td></tr>`;
  }
}
function renderPairs() {
  const data = state.pairs;
  const columns = [
    { key: "__id", label: "ID", cls: "mono-cell" },
    { key: "开始标签", label: "开始标签", cls: "mono-cell" },
    { key: "结束标签", label: "结束标签", cls: "mono-cell" },
    { key: "动作描述", label: "动作描述", cls: "desc-cell" },
    { key: "构成要素", label: "构成要素", cls: "" },
    { key: "状态", label: "候选状态", cls: "" }
  ];
  $("pairs-thead").innerHTML = `<tr>${columns.map(col => `<th>${escapeHtml(col.label)}</th>`).join("")}</tr>`;
  if (!data.rows?.length) {
    $("pairs-tbody").innerHTML = `<tr><td colspan="${columns.length}" class="table-empty">当前条件下没有记录</td></tr>`;
  } else {
    $("pairs-tbody").innerHTML = data.rows.map(row => `<tr>${columns.map(col => {
      const value = col.key === "__id" ? (row["原子对ID"] || row["候选ID"] || row["来源"] || "") : (row[col.key] || "");
      return `<td class="${col.cls}" title="${escapeHtml(value)}">${escapeHtml(value || "—")}</td>`;
    }).join("")}</tr>`).join("");
  }
  $("pairs-count").textContent = `共 ${data.total || 0} 条记录`;
  $("pairs-source-tag").textContent = data.source_file || "候选文件";
  renderPagination(data.page || 1, data.total_pages || 0);
}
function renderPagination(page, totalPages) {
  const container = $("pairs-pagination");
  if (totalPages <= 1) { container.innerHTML = ""; return; }
  let html = `<button class="page-button" data-page="${page - 1}" ${page <= 1 ? "disabled" : ""}>‹</button>`;
  const start = Math.max(1, Math.min(page - 2, totalPages - 4));
  const end = Math.min(totalPages, start + 4);
  for (let i = start; i <= end; i++) html += `<button class="page-button ${i === page ? "active" : ""}" data-page="${i}">${i}</button>`;
  html += `<button class="page-button" data-page="${page + 1}" ${page >= totalPages ? "disabled" : ""}>›</button>`;
  container.innerHTML = html;
}

// ---------------------------------------------------------------------------
// Batch explorer
// ---------------------------------------------------------------------------
async function loadBatches() {
  if (!state.currentRun) return;
  try {
    const data = await fetchJSON(`/api/run/${encodeURIComponent(state.currentRun)}/batches`);
    renderBatchGroup("process", data.process || [], "process-batch-list", "process-batch-count");
    renderBatchGroup("b_tier", data.b_tier || [], "desc-batch-list", "desc-batch-count");
  } catch (error) {
    $("process-batch-list").innerHTML = `<div class="empty-state small">${escapeHtml(error.message)}</div>`;
  }
}
function renderBatchGroup(type, rows, listId, countId) {
  const completed = rows.filter(row => row.valid).length;
  $(countId).textContent = `${completed} / ${rows.length}`;
  if (!rows.length) {
    $(listId).innerHTML = '<div class="empty-state small">当前运行没有此类批次。</div>';
    return;
  }
  $(listId).innerHTML = rows.map(row => {
    const status = row.valid ? "done" : (row.has_result ? "invalid" : "pending");
    const label = row.valid ? "已通过" : (row.has_result ? "结果待修复" : "待处理");
    const batchId = row.batch_id;
    const routeId = row.route_id || batchId;
    return `<div class="batch-row" data-batch-type="${type}" data-batch-id="${escapeHtml(routeId)}">
      <div class="batch-row-main"><span class="status-dot ${row.valid ? "ok" : (row.has_result ? "bad" : "")}"></span>
        <div><strong>${escapeHtml(routeId)}</strong><small>${escapeHtml(row.message || "")}</small></div>
      </div><span class="batch-state ${status}">${label}</span>
    </div>`;
  }).join("");
}
async function viewBatch(type, batchId) {
  if (!state.currentRun) return;
  try {
    const data = await fetchJSON(`/api/run/${encodeURIComponent(state.currentRun)}/batch/${encodeURIComponent(type)}/${encodeURIComponent(batchId)}`);
    $("batch-detail-title").textContent = `${type === "process" ? "工艺批次" : "B 档描述"} · ${batchId} · ${data.message || ""}`;
    $("batch-input-json").textContent = data.input ? JSON.stringify(data.input, null, 2) : "输入文件不存在";
    $("batch-result-json").textContent = data.result ? JSON.stringify(data.result, null, 2) : "尚未生成 result.json";
    show("batch-detail-inline");
    $("batch-detail-inline").scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (error) {
    showToast(`读取批次失败：${error.message}`, "error");
  }
}
// ---------------------------------------------------------------------------
// Delivery files
// ---------------------------------------------------------------------------
async function loadFiles() {
  if (!state.currentRun) return;
  try {
    const files = await fetchJSON(`/api/run/${encodeURIComponent(state.currentRun)}/files`);
    if (!files.length) {
      $("downloads-list").innerHTML = '<div class="empty-state small">暂无交付文件。</div>';
      return;
    }
    $("downloads-list").innerHTML = files.map(file => {
      const ext = basename(file.name).split(".").pop().toUpperCase();
      const url = `/api/run/${encodeURIComponent(state.currentRun)}/download?file=${encodeURIComponent(file.name)}`;
      return `<div class="download-item">
        <div class="file-icon">${escapeHtml(ext.slice(0, 4))}</div>
        <div class="download-info"><div class="download-name" title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</div><div class="download-size">${formatSize(file.size)}</div></div>
        <a class="download-link" href="${url}" download>下载</a>
      </div>`;
    }).join("");
  } catch (error) {
    $("downloads-list").innerHTML = `<div class="empty-state small">${escapeHtml(error.message)}</div>`;
  }
}

// ---------------------------------------------------------------------------
// Tabs and navigation
// ---------------------------------------------------------------------------
function activateResultTab(name) {
  document.querySelectorAll(".result-tab").forEach(tab => tab.classList.toggle("active", tab.dataset.resultTab === name));
  ["pairs", "batches", "files"].forEach(key => $(`${key}-view`).classList.toggle("hidden", key !== name));
}
function scrollToPanel(id) {
  if ($(id).classList.contains("hidden")) {
    if (id === "result-panel" && state.currentRun) loadRunResults(state.currentRun);
    else if (id === "run-panel" && !state.currentJobId) showToast("尚未启动运行任务。", "warning");
  }
  $(id).scrollIntoView({ behavior: "smooth", block: "start" });
}
function bindEvents() {
  $("workflow-form").addEventListener("submit", startWorkflow);
  $("source").addEventListener("change", () => {
    if (!$("out").value.trim() && $("source").value.trim()) $("out").value = defaultOutputPath($("source").value);
  });
  $("source").addEventListener("blur", () => {
    if (!$("out").value.trim() && $("source").value.trim()) $("out").value = defaultOutputPath($("source").value);
  });
  document.querySelectorAll(".browse-button").forEach(button => button.addEventListener("click", () =>
    browseFile(button.dataset.target, button.dataset.filter, button.dataset.title)
  ));
  document.querySelectorAll(".nav-item").forEach(button => button.addEventListener("click", () => {
    document.querySelectorAll(".nav-item").forEach(item => item.classList.toggle("active", item === button));
    scrollToPanel(button.dataset.scroll);
  }));
  $("refresh-system").addEventListener("click", loadSystemStatus);
  $("retry-system").addEventListener("click", loadSystemStatus);
  $("refresh-history").addEventListener("click", loadHistory);
  $("btn-cancel").addEventListener("click", cancelJob);
  $("clear-log").addEventListener("click", clearLog);
  $("btn-resume").addEventListener("click", () => resumeRun(state.currentRun));
  $("pairs-search").addEventListener("input", () => {
    clearTimeout(state.searchTimer);
    state.searchTimer = setTimeout(() => loadPairs(1), 280);
  });
  $("pairs-pagination").addEventListener("click", event => {
    const button = event.target.closest("[data-page]");
    if (button && !button.disabled) loadPairs(Number(button.dataset.page));
  });
  $("process-batch-list").addEventListener("click", event => {
    const row = event.target.closest("[data-batch-id]");
    if (row) viewBatch(row.dataset.batchType, row.dataset.batchId);
  });
  $("desc-batch-list").addEventListener("click", event => {
    const row = event.target.closest("[data-batch-id]");
    if (row) viewBatch(row.dataset.batchType, row.dataset.batchId);
  });
  $("close-batch-detail").addEventListener("click", () => hide("batch-detail-inline"));
  document.querySelectorAll(".result-tab").forEach(tab => tab.addEventListener("click", () => activateResultTab(tab.dataset.resultTab)));
  $("history-list").addEventListener("click", event => {
    const row = event.target.closest("[data-open-run]");
    if (row) loadRunResults(row.dataset.openRun);
  });
}

document.addEventListener("DOMContentLoaded", async () => {
  bindEvents();
  setBusy(false);
  $("btn-cancel").classList.add("hidden");
  await Promise.all([loadSystemStatus(), loadHistory()]);
});
