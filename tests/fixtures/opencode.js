"use strict";
// Synthetic subprocess: verifies stdin EOF and writes only isolated test data.
const fs = require("node:fs"), path = require("node:path");
if (process.argv.includes("--version")) { console.log("fixture-opencode 1.0"); process.exit(0); }
let text = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", d => { text += d; });
process.stdin.on("end", () => {
  const mode = fs.readFileSync(path.join(__dirname, "fixture-mode.txt"), "utf8").trim();
  console.log("fixture: 模型进程收到 EOF");
  if (mode === "model-fail") { console.error("fixture model connection failed"); process.exitCode = 7; return; }
  if (mode === "slow") { setInterval(() => console.log("fixture waiting"), 100); return; }
  const start = text.indexOf("\n{"), end = text.indexOf("\n不要重新");
  const task = JSON.parse(text.slice(start + 1, end));
  fs.writeFileSync(path.join(__dirname, "captured-task.json"), JSON.stringify({ task, permission: process.env.OPENCODE_PERMISSION, args: process.argv.slice(2) }));
  if (mode === "incomplete") return;
  for (const output of task.process_batches) {
    const input = JSON.parse(fs.readFileSync(path.join(task.run_dir, output.replace(/\.result\.json$/, ".input.json")), "utf8"));
    fs.writeFileSync(path.join(task.run_dir, output), JSON.stringify({ batch_id: input.batch_id, steps: [], unresolved_events: (input.events || []).map(e => ({ event_id: e["事件ID"], reason: "Synthetic transport test; no model review performed" })) }));
  }
  if (mode === "description-missing") return;
  for (const output of task.description_batches) {
    const input = JSON.parse(fs.readFileSync(path.join(task.run_dir, output.replace(/\.result\.json$/, ".input.json")), "utf8"));
    const descs = Object.fromEntries(input.rows.map(r => [String(r.id), { ok: false, reason: "Synthetic fixture only" }]));
    fs.writeFileSync(path.join(task.run_dir, output), JSON.stringify({ descs }));
  }
  console.log("fixture: 所有批次处理完成");
});
