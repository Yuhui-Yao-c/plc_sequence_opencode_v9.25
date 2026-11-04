"use strict";

const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { spawn } = require("node:child_process");

class HttpError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}
function readJSON(file) {
  try { return JSON.parse(fs.readFileSync(file, "utf8").replace(/^\uFEFF/, "")); }
  catch { return null; }
}
function writeJSON(file, data) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const temp = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(temp, JSON.stringify(data, null, 2), "utf8");
  fs.renameSync(temp, file);
}
function within(root, target) {
  const rel = path.relative(root, target);
  return rel === "" || (!rel.startsWith(`..${path.sep}`) && rel !== ".." && !path.isAbsolute(rel));
}
function containedFile(root, relative) {
  const target = path.resolve(root, relative);
  if (!within(root, target)) throw new HttpError(400, "非法文件路径");
  if (!fs.existsSync(target) || !fs.statSync(target).isFile()) throw new HttpError(404, "文件不存在");
  if (!within(fs.realpathSync(root), fs.realpathSync(target))) throw new HttpError(400, "文件链接超出运行目录");
  return target;
}
function listFiles(dir, suffix) {
  try { return fs.readdirSync(dir).filter(n => n.endsWith(suffix)).sort(); }
  catch { return []; }
}
function runID(dir) {
  let canonical = path.resolve(dir);
  try { canonical = fs.realpathSync(canonical); } catch {}
  if (process.platform === "win32") canonical = canonical.toLowerCase();
  return crypto.createHash("sha256").update(canonical).digest("hex").slice(0, 24);
}
// RFC4180-style parser: quoted commas, escaped quotes and embedded newlines.
function parseCSV(text) {
  const rows = []; let row = [], value = "", quoted = false;
  text = text.replace(/^\uFEFF/, "");
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') { value += '"'; i++; }
      else if (c === '"') quoted = false;
      else value += c;
    } else if (c === '"' && !value) quoted = true;
    else if (c === ",") { row.push(value); value = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(value); if (row.some(x => x !== "")) rows.push(row);
      row = []; value = "";
    } else value += c;
  }
  if (quoted) throw new HttpError(422, "CSV 引号未闭合，文件可能仍在写入");
  row.push(value); if (row.some(x => x !== "")) rows.push(row);
  const headers = rows.shift() || [];
  return rows.map(r => Object.fromEntries(headers.map((h, i) => [h, r[i] ?? ""])));
}
function readCSV(file) { return parseCSV(fs.readFileSync(file, "utf8")); }

function findExecutable(names, env = process.env, platform = process.platform) {
  for (const name of names.filter(Boolean)) {
    const dirs = /[\\/]/.test(name) ? [""] : (env.PATH || env.Path || "").split(path.delimiter);
    const exts = platform === "win32" && !path.extname(name) ? (env.PATHEXT || ".EXE;.CMD;.BAT").split(";") : [""];
    for (const dir of dirs) for (const ext of exts) {
      const file = path.resolve(dir, name + ext);
      try { fs.accessSync(file, platform === "win32" ? fs.constants.F_OK : fs.constants.X_OK); if (fs.statSync(file).isFile()) return file; }
      catch {}
    }
  }
  return null;
}
// For Windows npm .cmd shims, only fixed CLI flags enter cmd.exe. Prompts and
// user paths travel through stdin / direct executable argv, never a batch file.
function toolCommand(executable, args, env = process.env, platform = process.platform) {
  if (platform !== "win32" || !/\.(cmd|bat)$/i.test(executable)) return { command: executable, args, env };
  if (!args.every(a => /^[A-Za-z0-9_-]+$/.test(a))) throw new Error("Windows 批处理入口仅接受固定工具参数，请将 PLC_PYTHON 指向 python.exe");
  return {
    command: env.ComSpec || "cmd.exe",
    args: ["/d", "/v:off", "/s", "/c", `""%PLC_TOOL_EXEC%" ${args.join(" ")}"`],
    env: { ...env, PLC_TOOL_EXEC: executable }, windowsVerbatimArguments: true,
  };
}
function spawnTool(executable, args, options = {}) {
  const spec = toolCommand(executable, args, options.env || process.env);
  return spawn(spec.command, spec.args, { ...options, env: spec.env, windowsVerbatimArguments: spec.windowsVerbatimArguments, windowsHide: true });
}
function probe(executable, args, cwd, env, timeout = 12000) {
  return new Promise(resolve => {
    if (!executable) return resolve({ ok: false, error: "未在 PATH 中找到程序" });
    let output = "", settled = false, child;
    const finish = value => { if (!settled) { settled = true; clearTimeout(timer); resolve(value); } };
    const timer = setTimeout(() => { child?.kill(); finish({ ok: false, error: "检查超时" }); }, timeout);
    try {
      child = spawnTool(executable, args, { cwd, env, stdio: ["pipe", "pipe", "pipe"] });
      child.stdout.setEncoding("utf8"); child.stderr.setEncoding("utf8");
      for (const stream of [child.stdout, child.stderr]) stream.on("data", d => { output = (output + d).slice(-12000); });
      child.on("error", e => finish({ ok: false, error: e.message }));
      child.on("close", code => finish({ ok: code === 0, output: output.trim(), error: code === 0 ? undefined : output.trim() || `退出码 ${code}` }));
      child.stdin.on("error", () => {}); child.stdin.end();
    } catch (e) { finish({ ok: false, error: e.message }); }
  });
}
module.exports = { HttpError, readJSON, writeJSON, within, containedFile, listFiles, runID, parseCSV, readCSV, findExecutable, toolCommand, spawnTool, probe };
