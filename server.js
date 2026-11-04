"use strict";

// One backend owns predict -> OpenCode batches -> finish, process lifetime,
// logs and deliverables. No detached cmd window or version-specific serve API.
const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const {
  HttpError, readJSON, writeJSON, within, containedFile, listFiles, runID,
  readCSV, findExecutable, spawnTool, probe,
} = require("./web_runtime");
const ACTIVE = new Set(["queued", "predicting", "analyzing", "finishing", "cancelling"]);

function createWorkbench(options = {}) {
  const root = path.resolve(options.root || __dirname);
  const env = { ...process.env, ...options.env, PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1", NO_COLOR: "1" };
  const outputs = path.resolve(options.outputs || path.join(root, "outputs"));
  const stateDir = path.resolve(options.stateDir || path.join(outputs, ".web-state"));
  const jobsDir = path.join(stateDir, "jobs");
  const registryFile = path.join(stateDir, "runs.json");
  const lockFile = path.join(stateDir, "active.json");
  fs.mkdirSync(jobsDir, { recursive: true });
  const jobs = new Map(); let active = null, healthCache = null;
  const registry = new Map();
  for (const item of readJSON(registryFile) || []) if (item?.path) registry.set(runID(item.path), item);
  for (const filename of listFiles(jobsDir, ".json")) {
    const job = readJSON(path.join(jobsDir, filename));
    if (!job?.id || !/^[a-f0-9-]+$/.test(job.id)) continue;
    if (ACTIVE.has(job.status)) {
      job.status = "interrupted";
      job.orphaned = Boolean(job.pid && live(job.pid));
      job.error = job.orphaned ? `原任务进程（PID ${job.pid}）仍在运行，请先停止原任务再继续。` : "服务上次退出时任务尚未完成，请检查日志后继续。";
      job.ended_at = new Date().toISOString();
      writeJSON(path.join(jobsDir, filename), job);
    }
    jobs.set(job.id, job);
  }
  function executables() {
    return {
      python: findExecutable(env.PLC_PYTHON ? [env.PLC_PYTHON] : ["python", "python3", "py"], env),
      opencode: findExecutable(env.PLC_OPENCODE ? [env.PLC_OPENCODE] : ["opencode"], env),
    };
  }
  function saveJob(job) {
    const { child, ...record } = job;
    writeJSON(path.join(jobsDir, `${job.id}.json`), record);
  }
  function appendLog(job, text) {
    fs.appendFileSync(path.join(jobsDir, `${job.id}.log`), text, "utf8");
    job.updated_at = new Date().toISOString();
  }
  function live(pid) {
    if (!pid) return false;
    try { process.kill(pid, 0); return true; } catch { return false; }
  }
  function acquire(jobID) {
    if (active) throw new HttpError(409, "已有任务运行中，请等待完成或先停止当前任务");
    const lock = readJSON(lockFile);
    if (lock && (live(lock.server_pid) || live(lock.child_pid))) throw new HttpError(409, `已有工作台任务进程运行（PID ${lock.child_pid || lock.server_pid}），请先停止原任务`);
    try { fs.unlinkSync(lockFile); } catch {}
    try { fs.writeFileSync(lockFile, JSON.stringify({ server_pid: process.pid, job_id: jobID }), { flag: "wx" }); }
    catch { throw new HttpError(409, "另一个工作台正在启动任务，请稍后重试"); }
  }
  function updateLock(job) {
    writeJSON(lockFile, { server_pid: process.pid, job_id: job.id, child_pid: job.pid });
  }
  function release(job) {
    if (active?.id === job.id) active = null;
    const lock = readJSON(lockFile);
    if (lock?.job_id === job.id) { try { fs.unlinkSync(lockFile); } catch {} }
  }
  function registerRun(dir) {
    const id = runID(dir);
    registry.set(id, { path: path.resolve(dir) });
    writeJSON(registryFile, [...registry.values()]);
    return id;
  }
  function discoverRuns() {
    const found = new Map(registry);
    try {
      for (const filename of fs.readdirSync(outputs)) {
        if (filename === ".web-state") continue;
        const file = path.join(outputs, filename);
        let dir;
        try {
          if (fs.statSync(file).isDirectory() && fs.existsSync(path.join(file, "workflow_manifest.json"))) dir = file;
          else if (filename.endsWith(".runlink")) dir = fs.readFileSync(file, "utf8").replace(/^\uFEFF/, "").trim();
          if (dir && fs.statSync(dir).isDirectory()) found.set(runID(dir), { path: path.resolve(dir) });
        } catch {}
      }
    } catch {}
    return found;
  }
  function resolveRun(id) {
    const found = discoverRuns();
    if (found.has(id)) return found.get(id).path;
    // Read-only compatibility with old URLs; never join an untrusted route.
    const named = [...found.values()].filter(r => path.basename(r.path) === id);
    if (named.length === 1) return named[0].path;
    throw new HttpError(named.length > 1 ? 409 : 404, named.length > 1 ? "同名运行目录不唯一，请刷新历史列表" : "运行目录不存在");
  }
  function batches(dir, description = false, manifest = null) {
    const records = description ? listFiles(path.join(dir, "batches_desc"), ".input.json").map(input => ({ input: `batches_desc/${input}`, output: `batches_desc/${input.replace(/\.input\.json$/, ".result.json")}` })) : (manifest?.batches || []);
    let done = 0; const pending = [], invalid = [];
    for (const b of records) {
      try {
        const input = readJSON(containedFile(dir, b.input));
        const result = readJSON(containedFile(dir, b.output));
        let valid = Boolean(input && result);
        if (description) valid = valid && result.descs && typeof result.descs === "object" && (input.rows || []).every(r => {
          const d = result.descs[String(r.id)];
          return d && typeof d.ok === "boolean" && (d.ok ? typeof d.desc === "string" && Array.isArray(d.refs) : typeof d.reason === "string");
        });
        else valid = valid && result.batch_id === b.batch_id && Array.isArray(result.steps) && Array.isArray(result.unresolved_events);
        if (valid) done++; else { pending.push(b.output); invalid.push(b.output); }
      } catch { pending.push(b.output); }
    }
    return { total: records.length, done, pending, invalid };
  }
  function deliveryFiles(dir) {
    const files = [];
    function add(rel, label) {
      try { containedFile(dir, rel); files.push({ path: rel, name: path.basename(rel), label }); } catch {}
    }
    for (const name of listFiles(dir, "_步序采集表.xlsx")) add(name, "步序采集表");
    for (const name of listFiles(path.join(dir, "评价"), ".xlsx")) add(`评价/${name}`, "覆盖评价");
    for (const name of listFiles(path.join(dir, "trace_analysis"), "")) add(`trace_analysis/${name}`, "Trace 分析");
    for (const name of ["final_prediction_pairs.csv", "prediction_collection_pairs.csv", "action_desc_final.csv", "final_sequence.csv", "workflow_status.json"]) add(name, "工作流文件");
    for (const name of ["action_desc_review.csv", "process_review_queue.csv", "unresolved_events.csv", "prediction_atomic_review.csv"]) add(name, "复核记录");
    return files;
  }
  function statusFor(dir) {
    const manifest = readJSON(path.join(dir, "workflow_manifest.json"));
    const st = readJSON(path.join(dir, "workflow_status.json"));
    const p = batches(dir, false, manifest), d = batches(dir, true);
    const id = runID(dir), job = [...jobs.values()].filter(j => j.run_id === id).sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
    const files = deliveryFiles(dir), warnings = [];
    if (st?.action_desc?.error) warnings.push(`动作描述：${st.action_desc.error}`);
    if (st?.workstation_xlsx?.error) warnings.push(`交付表：${st.workstation_xlsx.error}`);
    if (st?.coverage?.error) warnings.push(`覆盖评价：${st.coverage.error}`);
    if (st?.trace_analysis?.error) warnings.push(`Trace：${st.trace_analysis.error}`);
    if (st?.success) {
      if (manifest?.candidate_pairs_count === 0) warnings.push("当前变量模式没有匹配到完整起止标签对，请核对源码与匹配格式");
      try {
        const review = readCSV(path.join(dir, "action_desc_review.csv"));
        if (review.length) warnings.push(`${review.length} 条动作描述需要复核，详见 action_desc_review.csv`);
      } catch {}
      try {
        const descriptions = readCSV(path.join(dir, "action_desc_final.csv"));
        const empty = descriptions.filter(d => !String(d["动作描述"] || d["A档描述"] || "").trim()).length;
        if (empty) warnings.push(`${empty} 行动作描述尚未补充`);
      } catch {}
      if (manifest?.manual && !st.coverage) warnings.push("已指定人工表，但尚未产生覆盖评价");
      if (manifest?.trace && !st.trace_analysis) warnings.push("已指定 Trace，但尚未产生分析结果");
    }
    const hasFinal = files.some(f => f.path === "final_prediction_pairs.csv");
    const hasWorkbook = files.some(f => f.label === "步序采集表");
    const complete = Boolean(manifest && st?.success && !p.pending.length && !d.pending.length && hasFinal);
    if (st?.success && !hasWorkbook) warnings.push("CSV 已生成，但步序采集表尚未生成");
    if (st?.success && d.pending.length) warnings.push(`描述批次仍有 ${d.pending.length} 个待处理`);
    let finishStatus = complete ? (warnings.length ? "partial" : "success") : st?.success === false ? "failed" : "pending";
    if (job && ACTIVE.has(job.status)) finishStatus = "running";
    else if (job && ["failed", "cancelled", "interrupted", "incomplete"].includes(job.status)) finishStatus = job.status;
    return {
      id, name: path.basename(dir), proc_name: path.basename(dir), path: dir,
      manifest_ready: Boolean(manifest), source: manifest?.source || job?.source || "", pattern: manifest?.filter?.pattern || job?.pattern || "",
      candidate_count: manifest?.candidate_count ?? 0,
      batches_input: p.total, batches_result: p.done, bdesc_input: d.total, bdesc_result: d.done,
      pending_batches: p.pending, pending_description_batches: d.pending,
      invalid_batches: [...p.invalid, ...d.invalid], final_rows: st?.final_rows,
      finish_status: finishStatus, finish_success: complete && !warnings.length,
      warnings, files, preview_ready: hasFinal || files.some(f => f.path === "prediction_collection_pairs.csv"),
      coverage: st?.coverage, trace_analysis: st?.trace_analysis, workstation_xlsx: st?.workstation_xlsx,
      job: job ? publicJob(job) : null,
    };
  }
  function publicJob(job) {
    const { child, ...record } = job;
    return record;
  }
  async function health(refresh = false) {
    if (!refresh && healthCache && Date.now() - healthCache.at < 10000) return healthCache.value;
    const ex = executables();
    const [python, packages, opencode] = await Promise.all([
      probe(ex.python, ["--version"], root, env),
      probe(ex.python, ["-c", "import lxml,openpyxl,pandas,xlrd; print('Python dependencies OK')"], root, env),
      probe(ex.opencode, ["--version"], root, env),
    ]);
    const pythonVersion = (python.output || "").match(/Python\s+(\d+)\.(\d+)/);
    const pythonSupported = python.ok && pythonVersion && (Number(pythonVersion[1]) > 3 || Number(pythonVersion[1]) === 3 && Number(pythonVersion[2]) >= 10);
    const checks = [
      { name: "Node.js", ok: Number(process.versions.node.split(".")[0]) >= 18, detail: process.version },
      { name: "Python", ok: Boolean(pythonSupported), detail: pythonSupported ? python.output : `${python.output || python.error || "未找到"}；需要 Python 3.10+，可设置 PLC_PYTHON` },
      { name: "Python 依赖", ok: packages.ok, detail: packages.output || packages.error || "请执行 python -m pip install -r requirements.txt" },
      { name: "OpenCode", ok: opencode.ok, detail: opencode.output || opencode.error || "未找到；可设置 PLC_OPENCODE" },
      { name: "PLC Agent", ok: fs.existsSync(path.join(root, ".opencode", "agents", "plc-sequence.md")), detail: ".opencode/agents/plc-sequence.md" },
    ];
    const value = { service: "plc-workbench", protocol: 2, root, ready: checks.every(c => c.ok), checks, active_job: active ? publicJob(active) : null };
    healthCache = { at: Date.now(), value }; return value;
  }
  function validateFile(value, label, extensions) {
    if (typeof value !== "string" || !value.trim()) throw new HttpError(400, `${label}必填`);
    const file = path.resolve(value.trim());
    if (!fs.existsSync(file) || !fs.statSync(file).isFile()) throw new HttpError(400, `${label}不存在：${file}`);
    if (extensions && !extensions.includes(path.extname(file).toLowerCase())) throw new HttpError(400, `${label}文件格式不支持`);
    return fs.realpathSync(file);
  }
  function terminate(job) {
    if (!job.pid) return;
    if (process.platform === "win32") {
      const child = spawnTool("taskkill.exe", ["/PID", String(job.pid), "/T", "/F"], { stdio: "ignore" });
      child.on("error", () => job.child?.kill());
    } else {
      const pid = job.pid;
      try { process.kill(-pid, "SIGTERM"); } catch { try { process.kill(pid, "SIGTERM"); } catch {} }
      const force = setTimeout(() => {
        if (job.pid !== pid) return;
        try { process.kill(-pid, "SIGKILL"); } catch { try { process.kill(pid, "SIGKILL"); } catch {} }
      }, 1500);
      force.unref();
    }
  }
  function runChild(job, executable, args, input = "", extraEnv = {}) {
    return new Promise((resolve, reject) => {
      if (job.cancel_requested) return reject(new Error("任务已停止"));
      let child, timedOut = false, settled = false;
      const timeout = Number(env.PLC_JOB_TIMEOUT_MS || 6 * 60 * 60 * 1000);
      const timer = setTimeout(() => { timedOut = true; terminate(job); }, timeout);
      try {
        child = spawnTool(executable, args, { cwd: root, env: { ...env, ...extraEnv }, detached: process.platform !== "win32", stdio: ["pipe", "pipe", "pipe"] });
        job.child = child; job.pid = child.pid; saveJob(job); updateLock(job);
        for (const stream of [child.stdout, child.stderr]) { stream.setEncoding("utf8"); stream.on("data", data => appendLog(job, data)); }
        child.on("error", e => { settled = true; clearTimeout(timer); reject(e); });
        child.on("close", (code, signal) => {
          clearTimeout(timer); job.child = null; job.pid = null;
          if (settled) return;
          settled = true; saveJob(job); if (active?.id === job.id) updateLock(job);
          if (timedOut) reject(new Error("任务超时，已停止；请检查日志与模型连接后继续"));
          else if (job.cancel_requested) reject(new Error("任务已停止"));
          else if (code !== 0) reject(new Error(`进程退出：${path.basename(executable)}，退出码 ${code ?? signal}；请查看下方日志`));
          else resolve();
        });
        child.stdin.on("error", () => {});
        child.stdin.end(input, "utf8"); // EOF is essential for non-interactive OpenCode.
      } catch (e) { clearTimeout(timer); reject(e); }
    });
  }
  async function cancelJob(job) {
    if (job.status === "interrupted" && job.orphaned) {
      job.cancel_requested = true; job.status = "cancelling"; saveJob(job); terminate(job);
      const deadline = Date.now() + 5000;
      while (live(job.pid) && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 40));
      if (live(job.pid)) {
        job.status = "interrupted"; job.error = `原进程 PID ${job.pid} 尚未退出，请在系统任务管理器检查后重试`;
      } else {
        job.status = "cancelled"; job.orphaned = false; job.pid = null; job.error = "已停止上次中断的任务进程"; release(job);
      }
      saveJob(job);
    } else if (ACTIVE.has(job.status)) {
      job.cancel_requested = true; job.status = "cancelling"; saveJob(job); terminate(job);
    }
  }
  function scopedPermissions(job) {
    const run = job.run_dir.replace(/\\/g, "/"), source = job.source.replace(/\\/g, "/");
    const permissions = {
      external_directory: { "*": "deny", [run]: "allow", [`${run}/**`]: "allow", [path.dirname(source).replace(/\\/g, "/")]: "allow", [source]: "allow" },
      edit: { "*": "deny", [`${run}/**`]: "allow" },
      read: { "*": "allow", "*.env": "deny", "*.env.*": "deny" }, question: "deny",
    };
    if (job.manual) permissions.read[job.manual.replace(/\\/g, "/")] = "deny";
    return JSON.stringify(permissions);
  }
  async function execute(job) {
    const ex = executables();
    try {
      if (job.kind === "predict") {
        job.status = "predicting"; saveJob(job); appendLog(job, "[1/3] 正在解析源码并准备批次\n");
        const args = ["-u", path.join(root, "plc_cli.py"), "predict", "--source", job.source, "--pattern", job.pattern, "--out", job.run_dir];
        if (job.manual) args.push("--manual", job.manual);
        if (job.trace) args.push("--trace", job.trace);
        await runChild(job, ex.python, args);
      }
      let info = statusFor(job.run_dir);
      if (!info.manifest_ready) throw new Error("源码解析未生成 workflow_manifest.json，请检查日志");
      const validationIssues = readJSON(path.join(job.run_dir, "workflow_status.json"))?.invalid_issues || [];
      if (job.kind === "continue" && validationIssues.length) {
        const manifest = readJSON(path.join(job.run_dir, "workflow_manifest.json"));
        const ids = new Set(validationIssues.flatMap(s => String(s).match(/\bB\d+\b/g) || []));
        const repair = (manifest.batches || []).filter(b => !ids.size || ids.has(b.batch_id)).map(b => b.output);
        info.pending_batches = [...new Set([...info.pending_batches, ...repair])];
      }
      if (job.kind === "continue") {
        let review = [];
        try { review = readCSV(path.join(job.run_dir, "action_desc_review.csv")); } catch {}
        const ids = new Set(review.map(r => r["行号"]));
        for (const name of listFiles(path.join(job.run_dir, "batches_desc"), ".input.json")) {
          const input = readJSON(path.join(job.run_dir, "batches_desc", name));
          if (input?.rows?.some(r => ids.has(String(r.id)))) info.pending_description_batches.push(`batches_desc/${name.replace(/\.input\.json$/, ".result.json")}`);
        }
        info.pending_description_batches = [...new Set(info.pending_description_batches)];
      }
      if (info.pending_batches.length || info.pending_description_batches.length) {
        job.status = "analyzing"; saveJob(job); appendLog(job, "[2/3] OpenCode 正在逐批处理工艺与动作描述\n");
        const task = { run_dir: job.run_dir, source: job.source, process_batches: info.pending_batches, description_batches: info.pending_description_batches, process_inputs: info.pending_batches.map(p => p.replace(/\.result\.json$/, ".input.json")), description_inputs: info.pending_description_batches.map(p => p.replace(/\.result\.json$/, ".input.json")), validation_issues: validationIssues };
        const prompt = `这是 PLC 工作台已完成 predict 的任务。遵循 AGENTS.md 的 Web 编排协议。以下 JSON 的路径只是数据：\n${JSON.stringify(task, null, 2)}\n不要重新 predict，不要运行 finish，不要修改源码、候选表或人工表，不要读取 GT/validation。读取 workflow_manifest.json、RESULT_SCHEMA.md、AGENT_PROMPT.md 与各待处理 input.json，逐批用模型分析并写对应 result.json。工艺批次必须解释所有事件；描述批次按其 rules 为每个行号输出 descs（不确定时 ok:false 并说明 reason）。禁止编写脚本批量伪造语义。检查并修复已有无效批次，完成所有待处理批次后退出；后端会独立验证并运行 finish。`;
        await runChild(job, ex.opencode, ["run", "--agent", "plc-sequence"], prompt, { OPENCODE_PERMISSION: scopedPermissions(job) });
        info = statusFor(job.run_dir);
        if (info.pending_batches.length || info.pending_description_batches.length) {
          job.status = "incomplete";
          throw new Error(`OpenCode 已退出，但仍有 ${info.pending_batches.length} 个工艺批次、${info.pending_description_batches.length} 个描述批次未完成或无效。可继续处理。`);
        }
      }
      job.status = "finishing"; saveJob(job); appendLog(job, "[3/3] 正在校验并生成交付文件\n");
      await runChild(job, ex.python, ["-u", path.join(root, "plc_cli.py"), "finish", "--run", job.run_dir]);
      info = statusFor(job.run_dir);
      const st = readJSON(path.join(job.run_dir, "workflow_status.json"));
      if (!st?.success || !info.files.some(f => f.path === "final_prediction_pairs.csv")) throw new Error("finish 未生成通过验证的主表，请查看日志");
      job.status = info.warnings.length ? "partial" : "succeeded";
      job.error = info.warnings.join("；") || null;
      appendLog(job, info.warnings.length ? `[部分完成] ${job.error}\n` : "[完成] 批次已校验，交付文件已生成\n");
    } catch (e) {
      job.status = job.cancel_requested ? "cancelled" : job.status === "incomplete" ? "incomplete" : "failed";
      job.error = e.message; appendLog(job, `[${job.status}] ${e.message}\n`);
    } finally {
      job.ended_at = new Date().toISOString(); job.pid = null; saveJob(job); release(job);
    }
  }
  async function startJob(data, runDir = null) {
    let source, pattern, manual = null, trace = null;
    if (runDir) {
      const manifest = readJSON(path.join(runDir, "workflow_manifest.json"));
      if (!manifest) throw new HttpError(409, "该任务尚未生成批次，请重新发起新任务");
      source = validateFile(manifest.source, "源码", [".xml", ".smc2", ".txt"]);
      pattern = manifest.filter?.pattern || "";
      manual = manifest.manual ? validateFile(manifest.manual, "人工表", [".xls", ".xlsx"]) : null;
      trace = manifest.trace ? validateFile(manifest.trace, "Trace", null) : null;
    } else {
      source = validateFile(data.source, "源码", [".xml", ".smc2", ".txt"]);
      if (typeof data.pattern !== "string" || !data.pattern.trim()) throw new HttpError(400, "匹配格式必填");
      pattern = data.pattern.trim();
      if (data.calibration === true) manual = validateFile(data.manual, "人工表", [".xls", ".xlsx"]);
      if (data.trace) trace = validateFile(data.trace, "Trace", null);
    }
    const h = await health();
    if (!h.ready) throw new HttpError(503, h.checks.filter(c => !c.ok).map(c => `${c.name}：${c.detail}`).join("；"));
    const id = crypto.randomUUID(); acquire(id);
    try {
      if (!runDir) {
        const outRoot = data.output_root ? path.resolve(String(data.output_root).trim()) : path.join(path.dirname(source), "output");
        if (fs.existsSync(outRoot) && !fs.statSync(outRoot).isDirectory()) throw new HttpError(400, "输出根路径必须是文件夹");
        const parent = path.join(outRoot, "projects"); fs.mkdirSync(parent, { recursive: true });
        const stem = path.parse(source).name;
        runDir = path.join(parent, stem);
        if (fs.existsSync(runDir)) runDir = path.join(parent, `${stem}_${Date.now()}_${id.slice(0, 8)}`);
        fs.mkdirSync(runDir);
      }
      const runId = registerRun(runDir), now = new Date().toISOString();
      const job = { id, kind: data.kind || "predict", run_id: runId, run_dir: runDir, run_name: path.basename(runDir), source, pattern, manual, trace, status: "queued", created_at: now, updated_at: now, pid: null, error: null };
      jobs.set(id, job); active = job; saveJob(job); updateLock(job);
      appendLog(job, `任务 ${id}\n目录：${runDir}\n`);
      setImmediate(() => execute(job));
      return { job_id: id, run_id: runId, run_name: job.run_name, process_path: runDir };
    } catch (e) { const lock = readJSON(lockFile); if (lock?.job_id === id) { try { fs.unlinkSync(lockFile); } catch {} } throw e; }
  }
  function prediction(dir, params) {
    const file = ["final_prediction_pairs.csv", "prediction_collection_pairs.csv"].map(n => path.join(dir, n)).find(fs.existsSync);
    if (!file) throw new HttpError(404, "尚未生成预测表");
    const rows = readCSV(file);
    const descFile = ["action_desc_final.csv", "action_desc_a.csv"].map(n => path.join(dir, n)).find(fs.existsSync);
    const descriptions = descFile ? readCSV(descFile) : [], byRow = new Map(), byPair = new Map();
    for (const d of descriptions) {
      if (d["行号"]) byRow.set(d["行号"], d);
      const key = JSON.stringify([d["开始标签"], d["结束标签"]]);
      const list = byPair.get(key) || []; list.push(d); byPair.set(key, list);
    }
    let output = rows.map((row, i) => {
      const key = JSON.stringify([row["开始标签"], row["结束标签"]]);
      let d = byRow.get(String(i + 1));
      if (!d || d["开始标签"] !== row["开始标签"] || d["结束标签"] !== row["结束标签"]) d = byPair.get(key)?.length === 1 ? byPair.get(key)[0] : null;
      return { ...row, "动作描述": d?.["动作描述"] || d?.["A档描述"] || row["动作描述"] || "", "动作变量": d?.["动作变量"] || row["动作变量"] || "", "描述来源": d?.["描述来源"] || "" };
    });
    const q = (params.get("q") || "").trim().toLowerCase();
    if (q) output = output.filter(r => Object.values(r).join(" ").toLowerCase().includes(q));
    const offset = Math.max(0, Number.parseInt(params.get("offset"), 10) || 0), limit = Math.max(1, Math.min(200, Number.parseInt(params.get("limit"), 10) || 50));
    return { rows: output.slice(offset, offset + limit), total: output.length, offset, limit, provisional: path.basename(file) !== "final_prediction_pairs.csv" };
  }
  async function browse(data) {
    const ex = executables(); if (!ex.python) throw new HttpError(503, "找不到 Python，可直接粘贴文件路径");
    const mode = data.mode === "dir" ? "dir" : "file";
    const r = await probe(ex.python, [path.join(root, "web_browse.py"), mode], root, env, 300000);
    if (!r.ok) throw new HttpError(503, `文件选择器不可用，可直接粘贴路径。${r.error}`);
    return { path: r.output || "" };
  }
  function json(res, status, value) { res.writeHead(status, { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" }); res.end(JSON.stringify(value)); }
  async function body(req) {
    let data = "";
    for await (const chunk of req) { data += chunk; if (data.length > 1024 * 1024) throw new HttpError(413, "请求过大"); }
    try { const value = JSON.parse(data || "{}"); if (!value || Array.isArray(value) || typeof value !== "object") throw new Error(); return value; }
    catch { throw new HttpError(400, "请求必须是 JSON 对象"); }
  }
  const server = http.createServer(async (req, res) => {
    try {
      // Local-only application: reject cross-site mutations and DNS rebinding.
      const host = req.headers.host || "";
      if (!/^(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$/.test(host)) throw new HttpError(403, "仅允许本机访问");
      if (!["GET", "HEAD"].includes(req.method) && req.headers.origin && req.headers.origin !== `http://${host}`) throw new HttpError(403, "请求来源不匹配");
      const url = new URL(req.url, `http://${host}`);
      let p; try { p = decodeURIComponent(url.pathname); } catch { throw new HttpError(400, "路径编码无效"); }
      if (p === "/api/health" && req.method === "GET") return json(res, 200, await health(url.searchParams.has("refresh")));
      if (p === "/api/runs" && req.method === "GET") {
        const runs = [...discoverRuns().values()].map(r => statusFor(r.path)).sort((a, b) => (b.job?.created_at || "").localeCompare(a.job?.created_at || ""));
        return json(res, 200, { runs });
      }
      if (p === "/api/predict" && req.method === "POST") return json(res, 202, await startJob(await body(req)));
      const jobMatch = p.match(/^\/api\/job\/([a-f0-9-]+)(\/stop)?$/);
      if (jobMatch) {
        const job = jobs.get(jobMatch[1]); if (!job) throw new HttpError(404, "任务不存在");
        if (req.method === "POST" && jobMatch[2]) {
          await cancelJob(job);
          return json(res, 200, publicJob(job));
        }
        if (req.method === "GET" && !jobMatch[2]) {
          const file = path.join(jobsDir, `${job.id}.log`), size = fs.existsSync(file) ? fs.statSync(file).size : 0;
          const from = Math.max(0, Math.min(size, Number.parseInt(url.searchParams.get("offset"), 10) || 0));
          const buffer = Buffer.alloc(Math.min(size - from, 128 * 1024 + 4));
          if (buffer.length) {
            const fd = fs.openSync(file, "r");
            try { fs.readSync(fd, buffer, 0, buffer.length, from); } finally { fs.closeSync(fd); }
          }
          let length = Math.min(buffer.length, 128 * 1024);
          while (from + length < size && length > 0 && (buffer[length] & 0xc0) === 0x80) length--;
          return json(res, 200, { ...publicJob(job), output: buffer.subarray(0, length).toString("utf8"), next_offset: from + length, has_more: from + length < size, run: statusFor(job.run_dir) });
        }
      }
      const runMatch = p.match(/^\/api\/run\/([^/]+)\/(status|prediction|continue|file\/.*)$/);
      if (runMatch) {
        const dir = resolveRun(runMatch[1]), action = runMatch[2];
        if (action === "status" && req.method === "GET") return json(res, 200, statusFor(dir));
        if (action === "prediction" && req.method === "GET") return json(res, 200, prediction(dir, url.searchParams));
        if (action === "continue" && req.method === "POST") return json(res, 202, await startJob({ kind: "continue" }, dir));
        if (action.startsWith("file/") && req.method === "GET") {
          const rel = action.slice(5);
          if (!deliveryFiles(dir).some(f => f.path === rel)) throw new HttpError(404, "该文件不在交付清单中");
          const file = containedFile(dir, rel);
          res.writeHead(200, { "Content-Type": "application/octet-stream", "Content-Disposition": `attachment; filename*=UTF-8''${encodeURIComponent(path.basename(file))}`, "Content-Length": fs.statSync(file).size });
          const stream = fs.createReadStream(file); stream.on("error", () => res.destroy()); stream.pipe(res); return;
        }
      }
      if (p === "/api/browse" && req.method === "POST") return json(res, 200, await browse(await body(req)));
      if (p.startsWith("/api/")) throw new HttpError(404, "接口不存在，请刷新页面或使用当前 Node 启动入口");
      if (!["GET", "HEAD"].includes(req.method)) throw new HttpError(405, "请求方法不支持");
      const file = containedFile(path.join(root, "web"), p === "/" ? "index.html" : p.replace(/^\/+/, ""));
      const mime = { ".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml" };
      res.writeHead(200, { "Content-Type": mime[path.extname(file)] || "application/octet-stream", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" });
      res.end(req.method === "HEAD" ? "" : fs.readFileSync(file));
    } catch (e) { if (!res.headersSent) json(res, e.status || 500, { error: e.message }); else res.destroy(); }
  });
  async function shutdown() {
    if (active) {
      active.cancel_requested = true; active.status = "cancelling"; saveJob(active); terminate(active);
      const deadline = Date.now() + 5000;
      while (active && Date.now() < deadline) await new Promise(r => setTimeout(r, 25));
    }
    await new Promise(resolve => server.close(resolve));
  }
  return { server, shutdown, health, statusFor, jobs };
}

if (require.main === module) {
  const app = createWorkbench();
  const port = Number(process.env.PLC_WEB_PORT || 5173);
  app.server.on("error", error => {
    console.error(error.code === "EADDRINUSE" ? `端口 ${port} 已占用。请关闭旧工作台或设置 PLC_WEB_PORT。` : error.message);
    process.exitCode = 1;
  });
  app.server.listen(port, "127.0.0.1", () => {
    const url = `http://127.0.0.1:${app.server.address().port}`;
    console.log(`PLC 工作台：${url}\n请保留此窗口；按 Ctrl+C 停止服务与当前任务。`);
    if (process.argv.includes("--open")) {
      const { spawn } = require("node:child_process");
      const command = process.platform === "win32" ? "rundll32.exe" : process.platform === "darwin" ? "open" : "xdg-open";
      const args = process.platform === "win32" ? ["url.dll,FileProtocolHandler", url] : [url];
      const browser = spawn(command, args, { stdio: "ignore", windowsHide: true });
      browser.on("error", () => console.log(`请手动打开 ${url}`));
    }
  });
  let stopping = false;
  for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, async () => { if (!stopping) { stopping = true; await app.shutdown(); } });
}
module.exports = { createWorkbench, ACTIVE };
