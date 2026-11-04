// PLC 步序采集工作台 — 极简 Node 后端
// 职责：① 静态托管 web/  ② /api/oc/* → 反代 opencode serve  ③ 本地文件读取 + run 扫描  ④ tkinter 文件选择代理
// 无业务逻辑，纯管道。opencode serve (:19433) 是唯一工作流控制器。
"use strict";

const http = require("http");
const fs = require("fs");
const path = require("path");
const { execFile } = require("child_process");

const ROOT = __dirname;
const WEB_DIR = path.join(ROOT, "web");
const OUTPUTS_DIR = path.join(ROOT, "outputs");
const OC_HOST = "127.0.0.1";
const OC_PORT = 19433;
const PORT = 5173;

// ---------------------------------------------------------------------------
// 辅助函数（从 web_app.py 移植）
// ---------------------------------------------------------------------------
function procName(runName) {
  return (runName || "").replace(/_v\d+$/, "") || runName;
}

function resolveRun(name) {
  // 优先 .runlink，否则 outputs/<name>
  const link = path.join(OUTPUTS_DIR, `${name}.runlink`);
  if (fs.existsSync(link)) {
    try {
      const p = fs.readFileSync(link, "utf-8").trim();
      if (fs.statSync(p).isDirectory()) return p;
    } catch (e) {}
  }
  const direct = path.join(OUTPUTS_DIR, name);
  try {
    if (fs.statSync(direct).isDirectory()) return direct;
  } catch (e) {}
  return null;
}

function readJSON(p) {
  try { return JSON.parse(fs.readFileSync(p, "utf-8")); } catch (e) { return null; }
}

function listGlob(dir, pattern) {
  // 简单 glob：只支持 *.ext 形式
  try {
    return fs.readdirSync(dir).filter(f => {
      if (pattern === "*") return true;
      if (pattern.startsWith("*.")) return f.endsWith(pattern.slice(1));
      return f === pattern;
    });
  } catch (e) { return []; }
}

function runStatus(runDir, name) {
  const manifestP = path.join(runDir, "workflow_manifest.json");
  if (!fs.existsSync(manifestP)) return null;
  const name0 = name || path.basename(runDir);
  const info = { name: name0, proc_name: procName(name0) };
  const manifest = readJSON(manifestP);
  if (!manifest) return null;
  info.candidate_count = manifest.candidate_count || 0;
  info.batch_count = (manifest.batches || []).length;
  info.pattern = (manifest.filter || {}).pattern || null;
  info.source = manifest.source || "";
  info.manual = manifest.manual || null;
  info.trace = manifest.trace || null;
  info.status = manifest.status || "PREPARED";
  // 批次计数
  const bdir = path.join(runDir, "batches");
  const bdesc = path.join(runDir, "batches_desc");
  info.batches_input = listGlob(bdir, "*.input.json").length;
  info.batches_result = listGlob(bdir, "*.result.json").length;
  info.bdesc_input = listGlob(bdesc, "*.input.json").length;
  info.bdesc_result = listGlob(bdesc, "*.result.json").length;
  // finish 状态
  const statusP = path.join(runDir, "workflow_status.json");
  if (fs.existsSync(statusP)) {
    const st = readJSON(statusP);
    if (st) {
      info.finish_success = st.success;
      info.final_rows = st.final_rows;
      info.workstation_xlsx = st.workstation_xlsx || {};
      info.coverage = st.coverage;
      info.trace_analysis = st.trace_analysis;
      info.finish_status = st.success ? "success" : "failed";
    } else {
      info.finish_status = "unknown";
    }
  } else {
    info.finish_status = "pending";
  }
  // 交付文件
  const xlsxFiles = listGlob(runDir, "*_步序采集表.xlsx");
  info.delivery_xlsx = xlsxFiles[0] || null;
  const covDir = path.join(runDir, "评价");
  const covFiles = listGlob(covDir, "*_覆盖评价.xlsx");
  info.coverage_xlsx = covFiles[0] ? ("评价/" + covFiles[0]) : null;
  const traceDir = path.join(runDir, "trace_analysis");
  try {
    info.has_trace_analysis = fs.statSync(traceDir).isDirectory() &&
      fs.readdirSync(traceDir).length > 0;
  } catch (e) { info.has_trace_analysis = false; }
  return info;
}

// CSV 解析（简单版，支持引号）
function parseCSV(text) {
  const lines = text.replace(/^\uFEFF/, "").split(/\r?\n/).filter(l => l.length);
  if (!lines.length) return { headers: [], rows: [] };
  function parseLine(line) {
    const out = []; let cur = ""; let inQ = false;
    for (let i = 0; i < line.length; i++) {
      const c = line[i];
      if (inQ) {
        if (c === '"') { if (line[i+1] === '"') { cur += '"'; i++; } else inQ = false; }
        else cur += c;
      } else {
        if (c === '"') inQ = true;
        else if (c === ",") { out.push(cur); cur = ""; }
        else cur += c;
      }
    }
    out.push(cur); return out;
  }
  const headers = parseLine(lines[0]);
  const rows = lines.slice(1).map(parseLine).filter(r => r.length === headers.length);
  return { headers, rows };
}

function readPrediction(runDir, offset, limit, q) {
  let predP = path.join(runDir, "final_prediction_pairs.csv");
  if (!fs.existsSync(predP)) predP = path.join(runDir, "prediction_collection_pairs.csv");
  if (!fs.existsSync(predP)) return null;
  const text = fs.readFileSync(predP, "utf-8");
  const { headers, rows } = parseCSV(text);
  const idx = {}; headers.forEach((h, i) => idx[h] = i);
  // desc_map
  const descMap = {};
  for (const descName of ["action_desc_final.csv", "action_desc_a.csv"]) {
    const descP = path.join(runDir, descName);
    if (fs.existsSync(descP)) {
      const dtext = fs.readFileSync(descP, "utf-8");
      const { headers: dh, rows: dr } = parseCSV(dtext);
      const di = {}; dh.forEach((h, i) => di[h] = i);
      for (const r of dr) {
        const key = r[di["开始标签"]] + "|" + r[di["结束标签"]];
        if (!(key in descMap)) {
          descMap[key] = {
            desc: r[di["动作描述"]] || r[di["A档描述"]] || "",
            var: r[di["动作变量"]] || "",
            source: r[di["描述来源"]] || "",
          };
        }
      }
      break;
    }
  }
  let out = rows.map(r => {
    const row = {
      "构成要素": r[idx["构成要素"]] || "",
      "动作描述": r[idx["动作描述"]] || "",
      "开始标签": r[idx["开始标签"]] || "",
      "结束标签": r[idx["结束标签"]] || "",
      "状态": r[idx["状态"]] || "",
      "备注": r[idx["备注"]] || "",
    };
    const key = row["开始标签"] + "|" + row["结束标签"];
    if (key in descMap) {
      const dm = descMap[key];
      if (dm.desc) row["动作描述"] = dm.desc;
      row["动作变量"] = dm.var;
      row["描述来源"] = dm.source;
    } else {
      row["动作变量"] = "";
      row["描述来源"] = "";
    }
    return row;
  });
  if (q) {
    const ql = q.toLowerCase();
    out = out.filter(r => (r["构成要素"] + r["动作描述"] + r["开始标签"] + r["结束标签"] + (r["动作变量"] || "")).toLowerCase().includes(ql));
  }
  const total = out.length;
  const page = out.slice(offset, offset + limit);
  return { rows: page, total, offset, limit };
}

// ---------------------------------------------------------------------------
// 反向代理 /api/oc/* → opencode serve /api/*
// ---------------------------------------------------------------------------
function proxyToServe(req, res, body) {
// 去掉 /api/oc 前缀，补回 /api（/api/oc/event → /api/event）
const servePath = req.url.replace(/^\/api\/oc/, "/api");
  const headers = { ...req.headers };
  delete headers.host;
  delete headers["content-length"];
  if (body) headers["content-length"] = Buffer.byteLength(body);
  const opts = {
    host: OC_HOST, port: OC_PORT, path: servePath, method: req.method, headers,
  };
  const proxy = http.request(opts, pres => {
    res.writeHead(pres.statusCode, pres.headers);
    pres.pipe(res);
  });
  proxy.on("error", e => {
    if (!res.headersSent) {
      res.writeHead(502, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: "opencode serve 不可达: " + e.message }));
    }
  });
  if (body) proxy.write(body);
  proxy.end();
}

// ---------------------------------------------------------------------------
// 路由
// ---------------------------------------------------------------------------
function handleLocal(req, res, body) {
  const url = new URL(req.url, "http://x");
  const p = url.pathname;

  // 历史 run 列表
  if (p === "/api/runs" && req.method === "GET") {
    const runs = [];
    const seen = new Set();
    try {
      if (fs.statSync(OUTPUTS_DIR).isDirectory()) {
        const entries = fs.readdirSync(OUTPUTS_DIR);
        const items = [];
        for (const e of entries) {
          const full = path.join(OUTPUTS_DIR, e);
          let runDir = null, name = null;
          try {
            if (fs.statSync(full).isDirectory()) { runDir = full; name = e; }
            else if (e.endsWith(".runlink")) {
              const target = fs.readFileSync(full, "utf-8").trim();
              if (fs.statSync(target).isDirectory()) { runDir = target; name = e.replace(/\.runlink$/, ""); }
            }
          } catch (err) {}
          if (runDir && !seen.has(runDir)) {
            seen.add(runDir);
            let mtime = 0;
            try { mtime = fs.statSync(runDir).mtimeMs; } catch (e) {}
            items.push({ mtime, runDir, name });
          }
        }
        items.sort((a, b) => b.mtime - a.mtime);
        for (const it of items) {
          const info = runStatus(it.runDir, it.name);
          if (info) runs.push(info);
        }
      }
    } catch (e) {}
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ runs }));
    return true;
  }

  // run 状态
  const m1 = p.match(/^\/api\/run\/([^/]+)\/status$/);
  if (m1 && req.method === "GET") {
    const d = resolveRun(m1[1]);
    if (!d) { res.writeHead(404, {"Content-Type":"application/json"}); res.end(JSON.stringify({error:"run不存在"})); return true; }
    const info = runStatus(d, m1[1]);
    if (!info) { res.writeHead(400, {"Content-Type":"application/json"}); res.end(JSON.stringify({error:"无效run"})); return true; }
    res.writeHead(200, {"Content-Type":"application/json"}); res.end(JSON.stringify(info)); return true;
  }

  // prediction CSV
  const m2 = p.match(/^\/api\/run\/([^/]+)\/prediction$/);
  if (m2 && req.method === "GET") {
    const d = resolveRun(m2[1]);
    if (!d) { res.writeHead(404, {"Content-Type":"application/json"}); res.end(JSON.stringify({error:"run不存在"})); return true; }
    const offset = parseInt(url.searchParams.get("offset") || "0");
    const limit = parseInt(url.searchParams.get("limit") || "50");
    const q = (url.searchParams.get("q") || "").trim();
    const data = readPrediction(d, offset, limit, q);
    if (!data) { res.writeHead(404, {"Content-Type":"application/json"}); res.end(JSON.stringify({error:"无预测文件"})); return true; }
    res.writeHead(200, {"Content-Type":"application/json"}); res.end(JSON.stringify(data)); return true;
  }

  // 文件下载
  const m3 = p.match(/^\/api\/run\/([^/]+)\/file\/(.+)$/);
  if (m3 && req.method === "GET") {
    const d = resolveRun(m3[1]);
    if (!d) { res.writeHead(404, {"Content-Type":"application/json"}); res.end(JSON.stringify({error:"run不存在"})); return true; }
    const rel = decodeURIComponent(m3[2]);
    const fp = path.resolve(path.join(d, rel));
    // 防路径穿越
    if (!fp.startsWith(path.resolve(d))) { res.writeHead(400, {"Content-Type":"application/json"}); res.end(JSON.stringify({error:"非法路径"})); return true; }
    if (!fs.existsSync(fp) || !fs.statSync(fp).isFile()) { res.writeHead(404, {"Content-Type":"application/json"}); res.end(JSON.stringify({error:"文件不存在"})); return true; }
    const stat = fs.statSync(fp);
    res.writeHead(200, {
      "Content-Type": "application/octet-stream",
      "Content-Disposition": `attachment; filename="${encodeURIComponent(path.basename(fp))}"`,
      "Content-Length": stat.size,
    });
    fs.createReadStream(fp).pipe(res);
    return true;
  }

  // tkinter 文件选择
  if (p === "/api/browse" && req.method === "POST") {
    let data;
    try { data = JSON.parse(body); } catch (e) { data = {}; }
    const mode = data.mode || "file";
    const title = data.title || "选择文件";
    const browsePy = path.join(ROOT, "web_browse.py");
    // 找 python
    const py = process.env.PLC_PYTHON || "python";
    execFile(py, [browsePy, mode, title], { windowsHide: true, timeout: 300000 }, (err, stdout) => {
      if (err) {
        res.writeHead(200, {"Content-Type":"application/json"});
        res.end(JSON.stringify({ path: "", error: err.message }));
        return;
      }
      res.writeHead(200, {"Content-Type":"application/json"});
      res.end(JSON.stringify({ path: (stdout || "").trim() }));
    });
    return true;
  }

  // 发起任务 / 继续：调 opencode serve 创建会话 + 发 prompt
  if (p === "/api/predict" && req.method === "POST") {
    return handlePredict(req, res, body);
  }
  const m4 = p.match(/^\/api\/run\/([^/]+)\/continue$/);
  if (m4 && req.method === "POST") {
    return handleContinue(req, res, body, m4[1]);
  }

  return false;
}

function ocReq(method, servePath, bodyObj) {
  return new Promise((resolve, reject) => {
    const body = bodyObj ? JSON.stringify(bodyObj) : null;
    const headers = { "Content-Type": "application/json" };
    if (body) headers["Content-Length"] = Buffer.byteLength(body);
    const r = http.request({ host: OC_HOST, port: OC_PORT, path: servePath, method, headers }, pres => {
      let d = ""; pres.on("data", c => d += c);
      pres.on("end", () => {
        let parsed; try { parsed = JSON.parse(d); } catch (e) { parsed = d; }
        resolve({ status: pres.statusCode, data: parsed });
      });
    });
    r.on("error", reject);
    r.setTimeout(10000, () => { r.destroy(); reject(new Error("serve 超时")); });
    if (body) r.write(body);
    r.end();
  });
}

async function handlePredict(req, res, body) {
  let data;
  try { data = JSON.parse(body); } catch (e) { res.writeHead(400, {"Content-Type":"application/json"}); res.end(JSON.stringify({error:"无效JSON"})); return true; }
  const source = (data.source || "").trim();
  const pattern = (data.pattern || "").trim();
  const outputRoot = (data.output_root || "").trim();
  const manual = (data.manual || "").trim();
  const trace = (data.trace || "").trim();
  if (!source) { res.writeHead(400, {"Content-Type":"application/json"}); res.end(JSON.stringify({error:"源码XML必填"})); return true; }

  const srcPath = path.parse(source);
  const outRoot = outputRoot || path.join(srcPath.dir, "output");
  const runName = srcPath.name;
  const processPath = path.join(outRoot, "projects", runName);

  // 拼指令
  const parts = [source];
  if (pattern) parts.push(pattern);
  parts.push(`输出目录：${processPath}`);
  if (trace) parts.push(`trace：${trace}`);
  if (manual) parts.push(`人工表：${manual}`);
  parts.push("请按AGENTS.md完整执行全流程，不要中途停止：1.predict 2.逐批处理batches/B*.input.json写result.json（禁止写脚本批量生成，必须逐批用你的分析能力）3.逐批处理batches_desc写result.json 4.finish直到workflow_status.json success。");
  const message = parts.join(" ");

  // 写 runlink
  try {
    if (!fs.existsSync(OUTPUTS_DIR)) fs.mkdirSync(OUTPUTS_DIR, { recursive: true });
    fs.writeFileSync(path.join(OUTPUTS_DIR, `${runName}.runlink`), processPath, "utf-8");
  } catch (e) {}

  // 最简方案：写临时 bat，在可见窗口运行 opencode run "消息"
  const { exec } = require("child_process");
  const tmpBat = path.join(require("os").tmpdir(), `oc_run_${Date.now()}.bat`);
  // 消息里的双引号转义为 bat 的 "" 形式（cmd 里两个双引号代表一个）
  const escapedMsg = message.replace(/"/g, '""');
  const batContent = `@echo off\r\nchcp 65001 >nul\r\ncd /d "${ROOT}"\r\ntitle opencode - ${runName}\r\nopencode run --agent plc-sequence "${escapedMsg}"\r\necho.\r\necho ============================================\r\necho opencode 已退出，按任意键关闭此窗口\r\necho ============================================\r\npause >nul\r\n`;
  fs.writeFileSync(tmpBat, batContent, "utf-8");
  exec(`start "opencode - ${runName}" cmd /c "${tmpBat}"`, { cwd: ROOT });

  res.writeHead(200, {"Content-Type":"application/json"});
  res.end(JSON.stringify({
    message: message,
    output_root: outRoot,
    run_name: runName,
    process_path: processPath,
  }));
  return true;
}

async function handleContinue(req, res, body, name) {
  const d = resolveRun(name);
  if (!d) { res.writeHead(404, {"Content-Type":"application/json"}); res.end(JSON.stringify({error:"run不存在"})); return true; }
  const message = `继续处理这个run的剩余批次并完成finish，不要中途停止：${d} 请先检查batches/和B*.result.json确认哪些批次未处理，逐批处理未完成的写result.json（禁止写脚本批量生成，必须逐批用你的分析能力），然后处理batches_desc，最后运行finish直到workflow_status.json success。`;
  const { exec } = require("child_process");
  const tmpBat = path.join(require("os").tmpdir(), `oc_cont_${Date.now()}.bat`);
  const escapedMsg = message.replace(/"/g, '""');
  const batContent = `@echo off\r\nchcp 65001 >nul\r\ncd /d "${ROOT}"\r\ntitle opencode - continue ${name}\r\nopencode run --agent plc-sequence "${escapedMsg}"\r\necho.\r\necho ============================================\r\necho opencode 已退出，按任意键关闭此窗口\r\necho ============================================\r\npause >nul\r\n`;
  fs.writeFileSync(tmpBat, batContent, "utf-8");
  exec(`start "opencode - continue" cmd /c "${tmpBat}"`, { cwd: ROOT });
  res.writeHead(200, {"Content-Type":"application/json"});
  res.end(JSON.stringify({
    message: message,
    run_name: name,
    process_path: d,
  }));
  return true;
}

// ---------------------------------------------------------------------------
// 静态文件服务
// ---------------------------------------------------------------------------
const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png", ".svg": "image/svg+xml", ".ico": "image/x-icon",
};
function serveStatic(req, res) {
  let p = req.url.split("?")[0];
  if (p === "/" || p === "") p = "/index.html";
  // 防路径穿越
  const fp = path.join(WEB_DIR, path.normalize(p).replace(/^([\/\\])+/, ""));
  if (!fp.startsWith(WEB_DIR)) { res.writeHead(403); res.end("Forbidden"); return; }
  fs.readFile(fp, (err, data) => {
    if (err) { res.writeHead(404, {"Content-Type":"text/plain"}); res.end("Not Found: " + p); return; }
    const ext = path.extname(fp).toLowerCase();
    res.writeHead(200, { "Content-Type": MIME[ext] || "application/octet-stream" });
    res.end(data);
  });
}

// ---------------------------------------------------------------------------
// 主服务器
// ---------------------------------------------------------------------------
const server = http.createServer((req, res) => {
  // 收集 body
  let body = "";
  req.on("data", c => { body += c; if (body.length > 10 * 1024 * 1024) req.destroy(); });
  req.on("end", () => {
    // 1. 本地 API
    if (req.url.startsWith("/api/runs") || req.url.startsWith("/api/run/") ||
        req.url === "/api/browse" || req.url === "/api/predict") {
      if (handleLocal(req, res, body)) return;
    }
    // 2. 反代 serve（/api/oc/* 或 /api/event SSE）
    if (req.url.startsWith("/api/oc")) {
      proxyToServe(req, res, ["POST","PUT","PATCH"].includes(req.method) ? body : null);
      return;
    }
    // 3. 静态文件
    serveStatic(req, res);
  });
});

server.listen(PORT, "127.0.0.1", () => {
  console.log("=".repeat(50));
  console.log("  PLC 步序采集工作台 (Node + opencode serve)");
  console.log("  访问: http://127.0.0.1:" + PORT);
  console.log("  项目根: " + ROOT);
  console.log("  opencode serve: " + OC_HOST + ":" + OC_PORT);
  console.log("  按 Ctrl+C 停止");
  console.log("=".repeat(50));
});
