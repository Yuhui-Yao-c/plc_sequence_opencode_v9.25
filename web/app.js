// PLC 步序采集工作台 — 前端交互（简化版）
// 前端输入 → serve 后台跑 → 轮询状态 → 显示结果
"use strict";

let currentRun = null;
let calibMode = false;
let pollTimer = null;
let resultPage = { total: 0, offset: 0, limit: 50, q: "" };

// ---------------------------------------------------------------------------
// 工具
// ---------------------------------------------------------------------------
async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || res.statusText);
  }
  return res.json();
}
function el(id) { return document.getElementById(id); }
function esc(s) {
  return String(s || "").replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---------------------------------------------------------------------------
// 校准模式
// ---------------------------------------------------------------------------
function setCalibMode(on) {
  calibMode = on;
  el("calibCheck").checked = on;
  el("calibState").textContent = on ? "开启" : "关闭";
  el("calibState").className = "calib-state" + (on ? " on" : "");
  el("rowManual").style.display = on ? "" : "none";
  if (!on) el("sectionCoverage").style.display = "none";
  if (currentRun) selectRun(currentRun);
}

// ---------------------------------------------------------------------------
// Run 列表
// ---------------------------------------------------------------------------
async function loadRuns() {
  try {
    const data = await api("/api/runs");
    renderRunList(data.runs);
  } catch (e) {
    el("runList").innerHTML = '<li class="run-empty">加载失败</li>';
  }
}

function renderRunList(runs) {
  const ul = el("runList");
  if (!runs.length) { ul.innerHTML = '<li class="run-empty">暂无 Run</li>'; return; }
  ul.innerHTML = runs.map(r => {
    let dot = "prepared", txt = "待处理";
    if (r.finish_status === "success") { dot = "success"; txt = "已完成"; }
    else if (r.finish_status === "failed") { dot = "failed"; txt = "失败"; }
    else if (r.batches_result > 0 && r.batches_result < r.batches_input) { dot = "pending"; txt = "进行中"; }
    const active = r.name === currentRun ? " active" : "";
    return `<li class="${active}" onclick="selectRun('${esc(r.name)}')">
      <span class="run-dot ${dot}" title="${txt}"></span>
      <span class="run-name">${esc(r.proc_name || r.name)}</span>
    </li>`;
  }).join("");
}

// ---------------------------------------------------------------------------
// 选中历史 Run
// ---------------------------------------------------------------------------
async function selectRun(name) {
  currentRun = name;
  loadRuns();
  el("emptyState").style.display = "none";
  el("sectionNew").style.display = "none";
  el("sectionProgress").style.display = "none";
  try {
    const info = await api(`/api/run/${encodeURIComponent(name)}/status`);
    renderRunDetail(info);
  } catch (e) {
    alert("加载失败: " + e.message);
  }
}

function renderRunDetail(info) {
  el("currentRunName").textContent = info.proc_name || info.name;
  const sb = el("currentRunStatus");
  if (info.finish_status === "success") { sb.textContent = "已完成"; sb.className = "badge badge-success"; }
  else if (info.finish_status === "failed") { sb.textContent = "失败"; sb.className = "badge badge-danger"; }
  else { sb.textContent = "待处理"; sb.className = "badge badge-warn"; }

  el("sectionResultSummary").style.display = "";
  const cards = [];
  cards.push(card("候选总数", info.candidate_count || 0, ""));
  cards.push(card("工艺批次", `${info.batches_result}/${info.batches_input}`, info.batches_result >= info.batches_input && info.batches_input > 0 ? "success" : "warn"));
  if (info.final_rows != null) {
    cards.push(card("交付行数", info.final_rows, info.finish_success ? "success" : "danger"));
  }
  if (info.workstation_xlsx && info.workstation_xlsx.workstation_groups) {
    cards.push(card("工位数", info.workstation_xlsx.workstation_groups, "success"));
  }
  el("cardsContainer").innerHTML = cards.join("");

  const resultSummary = el("resultSummary");
  if (info.finish_status !== "success" && info.batches_input > 0) {
    const pending = info.batches_input - info.batches_result;
    const dPending = (info.bdesc_input || 0) - (info.bdesc_result || 0);
    resultSummary.innerHTML = `
      <div class="resume-box">
        <div class="resume-info">
          <span class="resume-icon">⏸️</span>
          <div>
            <div class="resume-title">任务未完成</div>
            <div class="resume-desc">工艺批次剩余 ${pending} 个，描述批次剩余 ${dPending} 个未处理</div>
          </div>
        </div>
        <button class="btn btn-primary" onclick="continueRun('${esc(info.name)}')">继续处理</button>
      </div>`;
  } else if (info.finish_status === "success") {
    resultSummary.innerHTML = `<div class="resume-box resume-done"><div class="resume-info"><span class="resume-icon">✅</span><div><div class="resume-title">已完成</div><div class="resume-desc">所有批次已处理，交付表已生成</div></div></div></div>`;
  } else {
    resultSummary.innerHTML = "";
  }

  if (calibMode) {
    el("sectionCoverage").style.display = "";
    renderCoverage(info);
  }

  if (info.finish_status === "success") {
    el("sectionResult").style.display = "";
    el("sectionDownload").style.display = "";
    loadPrediction(0);
    renderDownload(info);
  } else {
    el("sectionResult").style.display = "none";
    el("sectionDownload").style.display = "none";
  }
}

function renderCoverage(info) {
  if (!info.coverage) {
    el("coverageBox").innerHTML = '<div class="coverage-empty">尚未生成覆盖率评价</div>';
    return;
  }
  const cov = info.coverage;
  const m = [];
  m.push(`<div class="coverage-metric"><div class="cm-label">状态</div><div class="cm-value">已生成</div></div>`);
  m.push(`<div class="coverage-metric"><div class="cm-label">工序</div><div class="cm-value" style="font-size:14px">${esc(cov.proc_name||"—")}</div></div>`);
  let link = "";
  if (info.coverage_xlsx) {
    link = `<div class="coverage-link"><a href="/api/run/${encodeURIComponent(info.name)}/file/${encodeURIComponent(info.coverage_xlsx)}" download>📊 下载覆盖评价</a></div>`;
  }
  el("coverageBox").innerHTML = m.join("") + link;
}

function card(label, value, cls) {
  return `<div class="card"><div class="card-label">${esc(label)}</div><div class="card-value ${cls}">${esc(value)}</div></div>`;
}

function renderDownload(info) {
  const items = [];
  if (info.delivery_xlsx) items.push(dl(info.delivery_xlsx, "步序采集表", "📄", `/api/run/${encodeURIComponent(info.name)}/file/${encodeURIComponent(info.delivery_xlsx)}`));
  if (info.coverage_xlsx) items.push(dl(info.coverage_xlsx.split("/").pop(), "覆盖评价", "📊", `/api/run/${encodeURIComponent(info.name)}/file/${encodeURIComponent(info.coverage_xlsx)}`));
  if (info.has_trace_analysis) items.push(dl("trace_step_runtime.csv", "trace分析", "📈", `/api/run/${encodeURIComponent(info.name)}/file/trace_analysis/trace_step_runtime.csv`));
  items.push(dl("final_prediction_pairs.csv", "预测主表", "📋", `/api/run/${encodeURIComponent(info.name)}/file/final_prediction_pairs.csv`));
  el("downloadList").innerHTML = items.join("");
}
function dl(name, desc, icon, href) {
  return `<a class="download-item" href="${href}" download><span class="download-icon">${icon}</span><div><div class="download-name">${esc(name)}</div><div class="download-desc">${esc(desc)}</div></div></a>`;
}

// ---------------------------------------------------------------------------
// 结果表格
// ---------------------------------------------------------------------------
async function loadPrediction(offset) {
  if (!currentRun) return;
  resultPage.offset = offset || 0;
  resultPage.q = el("searchBox").value.trim();
  const params = new URLSearchParams({ offset: resultPage.offset, limit: resultPage.limit, q: resultPage.q });
  try {
    const data = await api(`/api/run/${encodeURIComponent(currentRun)}/prediction?${params}`);
    renderTable(data);
  } catch (e) {
    el("resultBody").innerHTML = `<tr><td colspan="6" class="empty-cell">加载失败</td></tr>`;
  }
}

function renderTable(data) {
  const rows = data.rows || [];
  el("resultCount").textContent = `共 ${data.total} 行`;
  if (!rows.length) { el("resultBody").innerHTML = '<tr><td colspan="6" class="empty-cell">无数据</td></tr>'; el("pagination").innerHTML=""; return; }
  el("resultBody").innerHTML = rows.map(r => {
    const review = (r["备注"]||"").includes("待复核") ? " cell-review" : "";
    const ds = r["描述来源"] ? ` <span class="desc-tag">${esc(r["描述来源"])}</span>` : "";
    return `<tr><td>${esc(r["构成要素"]||"")}</td><td class="cell-desc">${esc(r["动作描述"]||"")}${ds}</td><td class="cell-var">${esc(r["动作变量"]||"")}</td><td class="cell-tag">${esc(r["开始标签"]||"")}</td><td class="cell-tag">${esc(r["结束标签"]||"")}</td><td class="${review}">${esc(r["备注"]||"")}</td></tr>`;
  }).join("");
  const total=data.total, limit=data.limit, cur=Math.floor(data.offset/limit), pages=Math.ceil(total/limit);
  if (pages<=1) { el("pagination").innerHTML=""; return; }
  let h=`<button class="page-btn" ${cur===0?"disabled":""} onclick="loadPrediction(${(cur-1)*limit})">‹</button>`;
  const s=Math.max(0,cur-2), e=Math.min(pages,s+5);
  for(let i=s;i<e;i++) h+=`<button class="page-btn ${i===cur?"active":""}" onclick="loadPrediction(${i*limit})">${i+1}</button>`;
  h+=`<button class="page-btn" ${cur>=pages-1?"disabled":""} onclick="loadPrediction(${(cur+1)*limit})">›</button>`;
  el("pagination").innerHTML=h;
}

// ---------------------------------------------------------------------------
// 文件选择
// ---------------------------------------------------------------------------
async function browseFile(inputId, mode, title) {
  try {
    const data = await api("/api/browse", { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({mode, title}) });
    if (data.path) {
      el(inputId).value = data.path;
      if (inputId === "inpSource") {
        const src = data.path;
        const slash = Math.max(src.lastIndexOf("/"), src.lastIndexOf("\\"));
        if (slash >= 0 && !el("inpOutputRoot").value) {
          el("inpOutputRoot").value = src.substring(0, slash) + "\\output";
        }
      }
    }
  } catch (e) { alert("选择失败: " + e.message); }
}

// ---------------------------------------------------------------------------
// 发起任务
// ---------------------------------------------------------------------------
function showNewTask() {
  el("emptyState").style.display = "none";
  ["sectionResultSummary","sectionProgress","sectionResult","sectionDownload"].forEach(id => el(id).style.display="none");
  if (!calibMode) el("sectionCoverage").style.display="none";
  el("sectionNew").style.display = "";
  el("currentRunName").textContent = "新任务";
  el("currentRunStatus").textContent = "新建";
  el("currentRunStatus").className = "badge badge-info";
  currentRun = null;
  loadRuns();
}

async function runPredict() {
  const source = el("inpSource").value.trim();
  const pattern = el("inpPattern").value.trim();
  const output_root = el("inpOutputRoot").value.trim();
  const manual = el("inpManual").value.trim();
  const trace = el("inpTrace").value.trim();
  if (!source) { alert("请选择源码 XML"); return; }
  if (!pattern) { alert("请填写匹配格式"); return; }
  if (calibMode && !manual) { alert("校准模式下人工表必填"); return; }

  el("btnPredict").disabled = true;
  el("btnPredict").textContent = "执行中...";
  el("sectionNew").style.display = "none";
  el("sectionProgress").style.display = "";

  try {
    const data = await api("/api/predict", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ source, pattern, output_root, manual, trace }),
    });
    window._lastRunName = data.run_name;
    el("phaseHeroIcon").textContent = "⏳";
    el("phaseHeroName").textContent = "运行中";
    el("phaseHeroDesc").textContent = "OpenCode 正在后台执行，请查看后台窗口了解实时进度。";
    el("jobStatusBadge").textContent = "运行中";
    el("jobStatusBadge").className = "badge badge-warn";
    startPolling(data.run_name);
  } catch (e) {
    alert("启动失败: " + e.message);
    el("btnPredict").disabled = false;
    el("btnPredict").textContent = "开始执行";
    el("sectionProgress").style.display = "none";
    el("sectionNew").style.display = "";
  }
}

// ---------------------------------------------------------------------------
// 继续处理
// ---------------------------------------------------------------------------
async function continueRun(name) {
  if (!confirm(`确认继续处理 run "${name}"？`)) return;
  el("sectionResultSummary").style.display = "none";
  el("sectionProgress").style.display = "";
  el("phaseHeroIcon").textContent = "⏳";
  el("phaseHeroName").textContent = "继续处理";
  el("phaseHeroDesc").textContent = "OpenCode 正在后台继续处理，请查看后台窗口。";
  el("phaseHero").className = "phase-hero";
  el("jobStatusBadge").textContent = "运行中";
  el("jobStatusBadge").className = "badge badge-warn";
  window._lastRunName = name;
  try {
    const data = await api(`/api/run/${encodeURIComponent(name)}/continue`, {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: "{}",
    });
    startPolling(name);
  } catch (e) {
    alert("启动失败: " + e.message);
    el("sectionProgress").style.display = "none";
    el("sectionResultSummary").style.display = "";
  }
}

// ---------------------------------------------------------------------------
// 轮询状态（简单版：定期读 workflow_status.json）
// ---------------------------------------------------------------------------
function startPolling(runName) {
  if (pollTimer) clearInterval(pollTimer);
  const poll = async () => {
    try {
      const info = await api(`/api/run/${encodeURIComponent(runName)}/status`);
      // 更新阶段描述
      let phase = "运行中";
      if (info.batches_input > 0 && info.batches_result < info.batches_input) {
        phase = `批次处理中 ${info.batches_result}/${info.batches_input}`;
      } else if (info.batches_input > 0 && info.batches_result >= info.batches_input) {
        phase = "组装交付";
      } else if (info.candidate_count > 0) {
        phase = "源码解析完成";
      }
      el("phaseHeroName").textContent = phase;
      el("phaseHeroDesc").textContent = `候选 ${info.candidate_count||0} · 批次 ${info.batches_result||0}/${info.batches_input||0} · ${info.finish_status||"运行中"}`;
      el("progressStats").textContent = phase;

      if (info.finish_status === "success") {
        clearInterval(pollTimer); pollTimer = null;
        el("phaseHeroIcon").textContent = "✅";
        el("phaseHeroName").textContent = "已完成";
        el("phaseHero").className = "phase-hero phase-hero-success";
        el("jobStatusBadge").textContent = "成功";
        el("jobStatusBadge").className = "badge badge-success";
        el("btnPredict").disabled = false;
        el("btnPredict").textContent = "开始执行";
        setTimeout(() => {
          loadRuns();
          if (window._lastRunName) setTimeout(() => selectRun(window._lastRunName), 500);
        }, 1500);
      } else if (info.finish_status === "failed") {
        clearInterval(pollTimer); pollTimer = null;
        el("phaseHeroIcon").textContent = "❌";
        el("phaseHeroName").textContent = "失败";
        el("phaseHero").className = "phase-hero phase-hero-failed";
        el("jobStatusBadge").textContent = "失败";
        el("jobStatusBadge").className = "badge badge-danger";
        el("btnPredict").disabled = false;
        el("btnPredict").textContent = "开始执行";
      }
    } catch (e) {}
  };
  poll();
  pollTimer = setInterval(poll, 3000);
}

// ---------------------------------------------------------------------------
// 初始化
// ---------------------------------------------------------------------------
window.addEventListener("DOMContentLoaded", () => {
  el("btnNewTask").onclick = showNewTask;
  el("btnCloseNew").onclick = () => { el("sectionNew").style.display = "none"; };
  el("btnPredict").onclick = runPredict;
  el("calibCheck").onchange = e => setCalibMode(e.target.checked);
  el("btnCollapseTable").onclick = () => {
    const area = el("tableArea");
    area.style.display = area.style.display === "none" ? "" : "none";
    el("btnCollapseTable").textContent = area.style.display === "none" ? "展开" : "收起";
  };
  let st = null;
  el("searchBox").addEventListener("input", () => { clearTimeout(st); st = setTimeout(() => loadPrediction(0), 350); });
  loadRuns();
});
