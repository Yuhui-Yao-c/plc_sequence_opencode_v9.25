/* ===================== PLC 步序采集工作流 — 前端逻辑 ===================== */

const API = "";
let currentRun = null;
let currentJobId = null;
let pollTimer = null;
let pairsData = { columns: [], rows: [], total: 0, page: 1, per_page: 100, total_pages: 1 };
let searchDebounceTimer = null;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function $(id) { return document.getElementById(id); }

function show(cardId) { $(cardId).classList.remove("hidden"); }
function hide(cardId) { $(cardId).classList.add("hidden"); }

async function fetchJSON(url, options) {
    const res = await fetch(url, options);
    if (!res.ok) {
        const err = await res.json().catch(() => ({ error: res.statusText }));
        throw new Error(err.error || `HTTP ${res.status}`);
    }
    return res.json();
}

function formatSize(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / 1024 / 1024).toFixed(1) + " MB";
}

function escapeHtml(str) {
    if (str == null) return "";
    return String(str)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

function basename(path) {
    if (!path) return "";
    return path.replace(/\\/g, "/").split("/").pop();
}

function stripExt(name) {
    return name.replace(/\.[^.]+$/, "");
}

// ---------------------------------------------------------------------------
// File Browse
// ---------------------------------------------------------------------------
async function browseFile(inputId, mode, filter, title) {
    try {
        const data = await fetchJSON(`${API}/api/browse`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ mode, filter, title: title || "选择文件" }),
        });
        if (data.path) {
            $(inputId).value = data.path;
            // 自动填充输出目录
            if (inputId === "source" && !$("out").value) {
                const name = stripExt(basename(data.path));
                // 去掉常见前缀数字
                const cleanName = name.replace(/^\d+\./, "");
                $("out").value = "outputs/" + cleanName;
            }
            updateCommandPreview();
        }
    } catch (e) {
        alert("文件选择失败: " + e.message);
    }
}

// ---------------------------------------------------------------------------
// Command Preview
// ---------------------------------------------------------------------------
function updateCommandPreview() {
    const source = $("source").value.trim();
    const pattern = $("pattern").value.trim() || "*";
    const out = $("out").value.trim();
    const trace = $("trace").value.trim();
    const manual = $("manual").value.trim();
    const force = $("force").checked;

    let cmd = `python plc_cli.py predict --source "${source}" --pattern "${pattern}" --out "${out}"`;
    if (trace) cmd += ` --trace "${trace}"`;
    if (manual) cmd += ` --manual "${manual}"`;
    if (force) cmd += ` --force`;

    $("command-preview").textContent = cmd;
}

// ---------------------------------------------------------------------------
// Predict
// ---------------------------------------------------------------------------
async function startPredict() {
    const source = $("source").value.trim();
    const out = $("out").value.trim();
    if (!source) { alert("请填写源码 XML 路径"); return; }
    if (!out) { alert("请填写输出目录"); return; }

    const pattern = $("pattern").value.trim() || "*";
    const trace = $("trace").value.trim();
    const manual = $("manual").value.trim();
    const force = $("force").checked;

    $("btn-predict").disabled = true;

    // 清空日志
    $("log-output").innerHTML = "";
    appendLog("启动预测...", "info");

    try {
        const data = await fetchJSON(`${API}/api/predict`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ source, pattern, out, trace, manual, force }),
        });

        currentJobId = data.job_id;
        currentRun = data.run_name;
        $("input-tag").textContent = `→ ${data.run_name}`;

        show("card-log");
        setLogStatus("running", "预测中");

        startPolling();
    } catch (e) {
        appendLog("启动失败: " + e.message, "error");
        $("btn-predict").disabled = false;
    }
}

// ---------------------------------------------------------------------------
// Finish
// ---------------------------------------------------------------------------
async function startFinish() {
    if (!currentRun) { alert("无当前运行"); return; }

    $("btn-finish").disabled = true;
    appendLog("\n--- 启动 Finish ---", "info");

    try {
        const data = await fetchJSON(`${API}/api/finish`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ run_name: currentRun }),
        });

        currentJobId = data.job_id;
        show("card-log");
        setLogStatus("running", "Finish 中");

        startPolling();
    } catch (e) {
        appendLog("Finish 启动失败: " + e.message, "error");
        $("btn-finish").disabled = false;
    }
}

// ---------------------------------------------------------------------------
// Job Polling
// ---------------------------------------------------------------------------
function startPolling() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(pollJob, 600);
}

function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

async function pollJob() {
    if (!currentJobId) return;
    try {
        const data = await fetchJSON(`${API}/api/job/${currentJobId}`);

        // 追加新日志
        for (const line of data.new_lines) {
            const cls = classifyLogLine(line);
            appendLog(line, cls);
        }

        $("log-time").textContent = `${data.elapsed}s`;

        if (data.status === "completed") {
            stopPolling();
            setLogStatus("success", data.type === "predict" ? "预测完成" : "Finish 完成");
            appendLog(`\n✓ 完成 (耗时 ${data.elapsed}s)`, "success");

            if (data.type === "predict") {
                $("btn-predict").disabled = false;
                currentRun = data.run_name;
                // 加载结果
                setTimeout(() => loadRunResults(currentRun), 500);
            } else {
                $("btn-finish").disabled = false;
                // 重新加载结果
                setTimeout(() => loadRunResults(currentRun), 500);
            }
        } else if (data.status === "failed") {
            stopPolling();
            setLogStatus("error", "失败");
            appendLog(`\n✗ 失败 (返回码 ${data.returncode})`, "error");
            if (data.error) appendLog(data.error, "error");
            $("btn-predict").disabled = false;
            $("btn-finish").disabled = false;
        }
    } catch (e) {
        console.error("Poll error:", e);
    }
}

function classifyLogLine(line) {
    if (/error|错误|失败|traceback|exception/i.test(line)) return "error";
    if (/warn|警告/i.test(line)) return "warn";
    if (/✓|完成|success|成功/i.test(line)) return "success";
    return "info";
}

function appendLog(text, cls) {
    const term = $("log-output");
    const div = document.createElement("div");
    div.className = `log-line log-${cls || "info"}`;
    div.textContent = text;
    term.appendChild(div);
    term.scrollTop = term.scrollHeight;
}

function setLogStatus(type, text) {
    const el = $("log-status");
    el.className = `badge badge-${type}`;
    el.textContent = text;
}

// ---------------------------------------------------------------------------
// Load Run Results
// ---------------------------------------------------------------------------
async function loadRunResults(runName) {
    currentRun = runName;
    // 重置 finish 按钮文字
    $("btn-finish").textContent = "▶ 执行 Finish";
    // 加载已有结果时折叠输入区，聚焦结果视图
    collapseInputCard(true);
    try {
        const info = await fetchJSON(`${API}/api/run/${encodeURIComponent(runName)}/info`);
        renderStats(info);
        show("card-results");

        // 批次状态
        renderBatchStatus(info);
        show("card-btier");

        // 预测对列表
        show("card-pairs");
        $("pairs-search").value = "";
        loadPairs(1);

        // 交付文件
        show("card-downloads");
        loadFiles();

        // 刷新历史
        loadHistory();
    } catch (e) {
        console.error("Load results error:", e);
    }
}

// ---------------------------------------------------------------------------
// Input Card Collapse
// ---------------------------------------------------------------------------
function toggleInputCard() {
    const collapsed = document.querySelector("#card-input .card-body").classList.toggle("collapsed");
    $("input-collapse").textContent = collapsed ? "+" : "−";
}

function collapseInputCard(collapsed) {
    document.querySelector("#card-input .card-body").classList.toggle("collapsed", collapsed);
    $("input-collapse").textContent = collapsed ? "+" : "−";
}

// ---------------------------------------------------------------------------
// Stats
// ---------------------------------------------------------------------------
function renderStats(info) {
    const grid = $("stats-grid");
    const cards = [];

    // 候选数
    cards.push({ value: info.candidate_pairs || 0, label: "完整候选", cls: "" });

    // 原子对
    const atomicSummary = info.prediction_atomic_summary || {};
    cards.push({ value: atomicSummary.unique_atomic_pairs || info.atomic_pairs || 0, label: "原子对", cls: "" });

    // 采集对
    const collSummary = info.prediction_collection_summary || {};
    cards.push({ value: collSummary.collection_pairs || info.collection_pairs || 0, label: "采集对", cls: "" });

    // A档描述
    cards.push({ value: info.a_desc_rows || 0, label: "A档描述", cls: "" });

    // 待核验
    cards.push({ value: info.review_rows || 0, label: "待核验", cls: info.review_rows ? "warning" : "success" });

    // B档批次
    const bt = info.batches?.b_tier || {};
    if (bt.total) {
        const cls = bt.pending === 0 ? "success" : "warning";
        cards.push({ value: `${bt.completed}/${bt.total}`, label: "B档批次", cls });
    }

    // 工艺批次
    const proc = info.batches?.process || {};
    if (proc.total) {
        const cls = proc.pending === 0 ? "success" : "warning";
        cards.push({ value: `${proc.completed}/${proc.total}`, label: "工艺批次", cls });
    }

    // 已完成
    if (info.finished) {
        cards.push({ value: "✓", label: "Finish 完成", cls: "success" });
    }

    // Trace
    if (info.workflow_status?.trace_analysis) {
        const ta = info.workflow_status.trace_analysis;
        cards.push({ value: ta.matched_pairs || 0, label: "Trace匹配", cls: "" });
        cards.push({ value: ta.total_jumps || 0, label: "跳变总数", cls: "" });
    }

    grid.innerHTML = cards.map(c =>
        `<div class="stat-card ${c.cls}">
            <div class="stat-value">${escapeHtml(c.value)}</div>
            <div class="stat-label">${escapeHtml(c.label)}</div>
        </div>`
    ).join("");

    // 覆盖评价
    if (info.workflow_status?.coverage) {
        const cov = info.workflow_status.coverage;
        $("coverage-info").innerHTML = `
            <div class="kv-item"><div class="kv-label">工序名</div><div class="kv-value">${escapeHtml(cov.proc_name || "")}</div></div>
            <div class="kv-item"><div class="kv-label">人工表</div><div class="kv-value">${escapeHtml(basename(cov.manual || ""))}</div></div>
            <div class="kv-item"><div class="kv-label">评价文件</div><div class="kv-value">${escapeHtml(basename(cov.out || ""))}</div></div>
        `;
        $("coverage-section").classList.remove("hidden");
    } else {
        $("coverage-section").classList.add("hidden");
    }

    // Trace 信息
    if (info.workflow_status?.trace_analysis) {
        const ta = info.workflow_status.trace_analysis;
        $("trace-info").innerHTML = `
            <div class="kv-item"><div class="kv-label">变量总数</div><div class="kv-value">${ta.variables_total || 0}</div></div>
            <div class="kv-item"><div class="kv-label">活跃变量</div><div class="kv-value">${ta.variables_active || 0}</div></div>
            <div class="kv-item"><div class="kv-label">跳变总数</div><div class="kv-value">${ta.total_jumps || 0}</div></div>
            <div class="kv-item"><div class="kv-label">预测对</div><div class="kv-value">${ta.prediction_pairs || 0}</div></div>
            <div class="kv-item"><div class="kv-label">已匹配</div><div class="kv-value">${ta.matched_pairs || 0}</div></div>
            <div class="kv-item"><div class="kv-label">检测周期</div><div class="kv-value">${ta.cycles_detected || 0}</div></div>
        `;
        $("trace-section").classList.remove("hidden");
    } else {
        $("trace-section").classList.add("hidden");
    }

    // 结果标签
    $("results-tag").textContent = info.finished ? "已 Finish" : "仅 Predict";
}

// ---------------------------------------------------------------------------
// Batch Status
// ---------------------------------------------------------------------------
function renderBatchStatus(info) {
    const container = $("batch-status");
    const proc = info.batches?.process || {};
    const bt = info.batches?.b_tier || {};

    const procPct = proc.total ? (proc.completed / proc.total * 100) : 0;
    const btPct = bt.total ? (bt.completed / bt.total * 100) : 0;
    const allDone = (!proc.total || proc.pending === 0) && (!bt.total || bt.pending === 0);

    container.innerHTML = `
        <div class="batch-status-card">
            <div class="batch-status-title">工艺批次 (batches/)</div>
            <div class="batch-progress">
                <span class="big">${proc.completed}</span>
                <span class="small">/ ${proc.total || 0}</span>
            </div>
            <div class="batch-bar">
                <div class="batch-bar-fill ${procPct === 100 ? 'complete' : ''}" style="width: ${procPct}%"></div>
            </div>
            <div class="batch-pending">待处理: ${proc.pending || 0}</div>
        </div>
        <div class="batch-status-card">
            <div class="batch-status-title">B档动作描述 (batches_desc/)</div>
            <div class="batch-progress">
                <span class="big">${bt.completed}</span>
                <span class="small">/ ${bt.total || 0}</span>
            </div>
            <div class="batch-bar">
                <div class="batch-bar-fill ${btPct === 100 ? 'complete' : ''}" style="width: ${btPct}%"></div>
            </div>
            <div class="batch-pending">待处理: ${bt.pending || 0}</div>
        </div>
    `;

    // 标签
    const tag = $("btier-tag");
    if (allDone && (proc.total || bt.total)) {
        tag.className = "badge badge-success";
        tag.textContent = "全部就绪";
        $("btn-finish").disabled = false;
    } else if (proc.total || bt.total) {
        tag.className = "badge badge-warning";
        tag.textContent = `${(proc.pending || 0) + (bt.pending || 0)} 待处理`;
        $("btn-finish").disabled = true;
    } else {
        tag.className = "badge";
        tag.textContent = "无批次";
        $("btn-finish").disabled = info.finished;
    }

    // 如果已 finish，禁用按钮
    if (info.finished) {
        $("btn-finish").disabled = true;
        $("btn-finish").textContent = "✓ 已 Finish";
    }
}

async function refreshBatches() {
    if (!currentRun) return;
    try {
        const info = await fetchJSON(`${API}/api/run/${encodeURIComponent(currentRun)}/info`);
        renderBatchStatus(info);
        renderStats(info);
    } catch (e) {
        alert("刷新失败: " + e.message);
    }
}

// ---------------------------------------------------------------------------
// Pairs Table
// ---------------------------------------------------------------------------
async function loadPairs(page) {
    if (!currentRun) return;
    const search = $("pairs-search").value.trim();
    const url = `${API}/api/run/${encodeURIComponent(currentRun)}/pairs?page=${page}&per_page=100&search=${encodeURIComponent(search)}`;

    try {
        const data = await fetchJSON(url);
        pairsData = data;
        renderPairsTable();
    } catch (e) {
        console.error("Load pairs error:", e);
    }
}

function renderPairsTable() {
    const { columns, rows, total, page, total_pages } = pairsData;

    // 选择显示的列
    const displayCols = [
        { key: "原子对ID", label: "ID", cls: "cell-mono", truncate: true },
        { key: "开始标签", label: "开始标签", cls: "cell-mono", truncate: true },
        { key: "结束标签", label: "结束标签", cls: "cell-mono", truncate: true },
        { key: "动作描述", label: "动作描述", cls: "cell-desc" },
        { key: "证据等级", label: "证据等级", cls: "cell-truncate" },
        { key: "构成要素", label: "构成要素", cls: "cell-truncate" },
    ];

    // 表头
    $("pairs-thead").innerHTML = "<tr>" +
        "<th>#</th>" +
        displayCols.map(c => `<th>${escapeHtml(c.label)}</th>`).join("") +
        "</tr>";

    // 表体
    if (rows.length === 0) {
        $("pairs-tbody").innerHTML = `<tr><td colspan="${displayCols.length + 1}" style="text-align:center;color:var(--text-muted);padding:24px;">无数据</td></tr>`;
    } else {
        $("pairs-tbody").innerHTML = rows.map((row, i) => {
            const idx = (page - 1) * 100 + i + 1;
            return "<tr>" +
                `<td class="text-muted">${idx}</td>` +
                displayCols.map(c => {
                    let val = row[c.key] || "";
                    return `<td class="${c.cls}" title="${escapeHtml(val)}">${escapeHtml(val)}</td>`;
                }).join("") +
                "</tr>";
        }).join("");
    }

    // 统计
    $("pairs-count").textContent = `共 ${total} 条`;

    // 来源标签
    const sourceTag = $("pairs-source-tag");
    const hasFinal = columns.length > 0; // 后端已处理优先级
    sourceTag.textContent = total > 0 ? `${rows.length > 0 ? '显示' : ''} ${((page - 1) * 100 + 1)}–${Math.min(page * 100, total)} / ${total}` : "";

    // 分页
    renderPagination(page, total_pages);
}

function renderPagination(page, total_pages) {
    const container = $("pairs-pagination");
    if (total_pages <= 1) { container.innerHTML = ""; return; }

    let html = "";
    // 上一页
    html += `<button class="page-btn" ${page <= 1 ? 'disabled' : ''} onclick="loadPairs(${page - 1})">‹</button>`;

    // 页码
    const maxShow = 7;
    let start = Math.max(1, page - 3);
    let end = Math.min(total_pages, start + maxShow - 1);
    start = Math.max(1, end - maxShow + 1);

    if (start > 1) {
        html += `<button class="page-btn" onclick="loadPairs(1)">1</button>`;
        if (start > 2) html += `<span class="text-muted">...</span>`;
    }
    for (let i = start; i <= end; i++) {
        html += `<button class="page-btn ${i === page ? 'active' : ''}" onclick="loadPairs(${i})">${i}</button>`;
    }
    if (end < total_pages) {
        if (end < total_pages - 1) html += `<span class="text-muted">...</span>`;
        html += `<button class="page-btn" onclick="loadPairs(${total_pages})">${total_pages}</button>`;
    }

    // 下一页
    html += `<button class="page-btn" ${page >= total_pages ? 'disabled' : ''} onclick="loadPairs(${page + 1})">›</button>`;

    container.innerHTML = html;
}

function debounceSearch() {
    if (searchDebounceTimer) clearTimeout(searchDebounceTimer);
    searchDebounceTimer = setTimeout(() => loadPairs(1), 350);
}

// ---------------------------------------------------------------------------
// Files / Downloads
// ---------------------------------------------------------------------------
async function loadFiles() {
    if (!currentRun) return;
    try {
        const files = await fetchJSON(`${API}/api/run/${encodeURIComponent(currentRun)}/files`);
        renderDownloads(files);
    } catch (e) {
        console.error("Load files error:", e);
    }
}

function renderDownloads(files) {
    const container = $("downloads-list");
    if (files.length === 0) {
        container.innerHTML = '<div class="text-muted">无文件</div>';
        return;
    }

    const iconFor = (name) => {
        if (name.endsWith(".xlsx") || name.endsWith(".xls")) return "📊";
        if (name.endsWith(".csv")) return "📄";
        if (name.endsWith(".json")) return "🔧";
        return "📄";
    };

    container.innerHTML = files.map(f => {
        const url = `${API}/api/run/${encodeURIComponent(currentRun)}/download?file=${encodeURIComponent(f.name)}`;
        return `
            <div class="download-item">
                <span class="download-icon">${iconFor(f.name)}</span>
                <div class="download-info">
                    <div class="download-name" title="${escapeHtml(f.name)}">${escapeHtml(f.name)}</div>
                    <div class="download-size">${formatSize(f.size)}</div>
                </div>
                <a href="${url}" class="btn btn-outline btn-sm" download>下载</a>
            </div>
        `;
    }).join("");
}

// ---------------------------------------------------------------------------
// History
// ---------------------------------------------------------------------------
async function loadHistory() {
    try {
        const runs = await fetchJSON(`${API}/api/runs`);
        const container = $("history-list");
        if (runs.length === 0) {
            container.innerHTML = '<div class="history-empty">暂无运行记录</div>';
            return;
        }
        container.innerHTML = runs.map(r => {
            const cls = r.finished ? "finished" : (r.has_manifest ? "predicted" : "empty");
            const label = r.finished ? "已完成" : (r.has_manifest ? "已预测" : "未完成");
            const srcName = basename(r.source) || "";
            const time = new Date(r.mtime * 1000).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
            const active = r.name === currentRun ? "active" : "";
            return `
                <div class="history-item ${active}" onclick="loadRunResults('${escapeHtml(r.name)}')">
                    <div class="history-name">
                        <span class="history-dot ${cls}"></span>
                        ${escapeHtml(r.name)}
                    </div>
                    <div class="history-meta">${label} · ${time}</div>
                </div>
            `;
        }).join("");
    } catch (e) {
        console.error("Load history error:", e);
    }
}

// ---------------------------------------------------------------------------
// New Run
// ---------------------------------------------------------------------------
function newRun() {
    currentRun = null;
    currentJobId = null;
    stopPolling();

    // 清空表单
    $("source").value = "";
    $("pattern").value = "*";
    $("out").value = "";
    $("trace").value = "";
    $("manual").value = "";
    $("force").checked = false;
    $("input-tag").textContent = "";

    // 隐藏结果卡片
    hide("card-log");
    hide("card-results");
    hide("card-btier");
    hide("card-pairs");
    hide("card-downloads");

    // 重置按钮
    $("btn-predict").disabled = false;
    $("btn-finish").disabled = true;
    $("btn-finish").textContent = "▶ 执行 Finish";

    // 展开输入区
    collapseInputCard(false);

    updateCommandPreview();
    loadHistory();
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
    updateCommandPreview();
    loadHistory();
});
