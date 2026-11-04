"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path");
const { parseCSV, within, containedFile, toolCommand, writeJSON } = require("../web_runtime");
const { fixture } = require("./web_fixture");

test("CSV retains quoted newlines, commas, escaped quotes and BOM", () => {
  assert.deepEqual(parseCSV('\uFEFFa,b\r\n"x\ny","a,""b"""\r\n'), [{ a: "x\ny", b: 'a,"b"' }]);
  assert.throws(() => parseCSV('a\n"broken'), /引号未闭合/);
});
test("containment rejects sibling-prefix directories", () => {
  assert.equal(within(path.resolve("run"), path.resolve("run-other/file")), false);
  assert.equal(within(path.resolve("run"), path.resolve("run/file")), true);
});
test("Windows npm shim command contains no user prompt or source argument", () => {
  const result = toolCommand("C:\\中文 &% data\\opencode.cmd", ["run", "--agent", "plc-sequence"], {}, "win32");
  assert.equal(result.env.PLC_TOOL_EXEC, "C:\\中文 &% data\\opencode.cmd");
  assert.equal(result.args.at(-1), '""%PLC_TOOL_EXEC%" run --agent plc-sequence"');
  assert.throws(() => toolCommand("x.cmd", ["untrusted&arg"], {}, "win32"));
});

test("actual subprocess chain, Unicode paths, logs, duplicate-label descriptions and downloads", async t => {
  const f = await fixture(); t.after(f.close);
  assert.equal((await f.request("/api/health")).ready, true);
  const started = await f.predict();
  const result = await f.wait(started.job_id);
  assert.equal(result.status, "succeeded");
  assert.match(result.output, /模型进程收到 EOF/);
  const rows = await f.request(`/api/run/${started.run_id}/prediction?limit=-1&offset=-100`);
  assert.equal(rows.limit, 1); assert.equal(rows.offset, 0);
  assert.equal(rows.rows[0]["动作描述"], "夹紧气缸");
  const full = await f.request(`/api/run/${started.run_id}/prediction`);
  assert.equal(full.rows[1]["动作描述"], "顶升气缸");
  assert.equal(full.rows[0]["备注"], "第一行\n含换行,和逗号");
  const workbook = result.run.files.find(x => x.path.endsWith("_步序采集表.xlsx"));
  assert.ok(workbook);
  const download = await fetch(`${f.base}/api/run/${started.run_id}/file/${encodeURIComponent(workbook.path)}`);
  assert.equal(download.status, 200); assert.match(download.headers.get("content-disposition"), /filename\*=UTF-8/);
  assert.equal(await download.text(), "fixture-workbook-bytes");
  const captured = JSON.parse(fs.readFileSync(path.join(f.root, "captured-task.json"), "utf8"));
  assert.deepEqual(captured.args, ["run", "--agent", "plc-sequence"]);
  assert.equal(captured.task.source, f.source);
  assert.equal(JSON.parse(captured.permission).external_directory["*"], "deny");
  const second = await f.predict(); await f.wait(second.job_id);
  assert.notEqual(second.process_path, started.process_path);
  assert.equal((await f.request("/api/runs")).runs.length, 2);
  await f.request(`/api/run/${started.run_id}/file/${encodeURIComponent("../source.xml")}`, undefined, 404);
});

for (const [mode, expected, log] of [
  ["predict-fail", "failed", /fixture parse failure/],
  ["model-fail", "failed", /fixture model connection failed/],
  ["incomplete", "incomplete", /仍有 1 个工艺批次、1 个描述批次/],
  ["description-missing", "incomplete", /仍有 0 个工艺批次、1 个描述批次/],
  ["finish-fail", "failed", /fixture finish failure/],
  ["workbook-fail", "partial", /fixture workbook failure/],
]) test(`failure is visible and terminal: ${mode}`, async t => {
  const f = await fixture(mode); t.after(f.close);
  const start = await f.predict(), result = await f.wait(start.job_id);
  assert.equal(result.status, expected); assert.match(result.output, log);
  if (mode === "description-missing") {
    assert.equal(fs.existsSync(path.join(start.process_path, "workflow_status.json")), false);
    f.setMode("normal");
    const resume = await f.request(`/api/run/${start.run_id}/continue`, {}, 202);
    const finished = await f.wait(resume.job_id);
    assert.equal(finished.status, "succeeded");
    assert.equal(resume.process_path, start.process_path);
  }
  if (mode === "workbook-fail") assert.ok(result.run.files.some(f => f.path === "final_prediction_pairs.csv"));
});

test("missing tools, source, pattern and malformed request fail explicitly", async t => {
  const f = await fixture("normal", { env: { PLC_OPENCODE: path.resolve("definitely-missing-opencode") } }); t.after(f.close);
  assert.equal((await f.request("/api/health")).ready, false);
  await f.request("/api/predict", { source: f.source, pattern: "S*" }, 503);
  await f.request("/api/predict", { source: f.source }, 400);
  await f.request("/api/predict", { source: f.source + "missing", pattern: "S*" }, 400);
  const response = await fetch(f.base + "/api/predict", { method: "POST", body: "not-json" });
  assert.equal(response.status, 400);
  await f.request("/api/predict", [], 400);
});
test("single active task, cancellation and recovery", async t => {
  const f = await fixture("slow"); t.after(f.close);
  const start = await f.predict();
  await f.request("/api/predict", { source: f.source, pattern: "S*" }, 409);
  await f.request(`/api/job/${start.job_id}/stop`, {});
  assert.equal((await f.wait(start.job_id)).status, "cancelled");
  f.setMode("normal");
  const resumed = await f.request(`/api/run/${start.run_id}/continue`, {}, 202).catch(() => null);
  if (resumed) assert.equal((await f.wait(resumed.job_id)).status, "succeeded");
  else assert.equal((await f.wait((await f.predict()).job_id)).status, "succeeded");
});
test("manual path ignored outside calibration, retained only when explicitly enabled", async t => {
  const f = await fixture(); t.after(f.close);
  let start = await f.predict({ manual: "old-stale-manual.xls", calibration: false });
  assert.equal((await f.wait(start.job_id)).status, "succeeded");
  let manifest = JSON.parse(fs.readFileSync(path.join(start.process_path, "workflow_manifest.json")));
  assert.equal(manifest.manual, null);
  const manual = path.join(f.root, "人工表.xlsx"); fs.writeFileSync(manual, "fixture");
  start = await f.predict({ manual, calibration: true });
  const job = await f.wait(start.job_id); assert.equal(job.status, "succeeded");
  assert.ok(job.run.files.some(f => f.label === "覆盖评价"));
  const captured = JSON.parse(fs.readFileSync(path.join(f.root, "captured-task.json")));
  assert.equal(JSON.parse(captured.permission).read[manual.replace(/\\/g, "/")], "deny");
});
test("file picker cancellation and GUI errors are distinct", async t => {
  const f = await fixture("browse-cancel"); t.after(f.close);
  assert.equal((await f.request("/api/browse", { mode: "file" })).path, "");
  f.setMode("browse-error");
  const error = await f.request("/api/browse", {}, 503); assert.match(error.error, /可直接粘贴路径/);
});
test("history uses canonical IDs for same names and rejects symlink escapes", async t => {
  const f = await fixture(); t.after(f.close);
  const dirs = [path.join(f.root, "a/同名"), path.join(f.root, "b/同名")];
  fs.mkdirSync(path.join(f.root, "outputs"), { recursive: true });
  dirs.forEach((dir, i) => { fs.mkdirSync(dir, { recursive: true }); writeJSON(path.join(dir, "workflow_manifest.json"), { batches: [], source: f.source }); fs.writeFileSync(path.join(f.root, `outputs/${i}.runlink`), dir); });
  const runs = (await f.request("/api/runs")).runs;
  assert.equal(runs.length, 2); assert.notEqual(runs[0].id, runs[1].id);
  assert.equal((await f.request(`/api/run/${encodeURIComponent(runs[0].id)}/status`)).name, "同名");
  await f.request(`/api/run/${encodeURIComponent("同名")}/status`, undefined, 409);
  if (process.platform !== "win32") {
    fs.symlinkSync(f.source, path.join(dirs[0], "escape.csv"));
    assert.throws(() => containedFile(dirs[0], "escape.csv"), /超出运行目录/);
  }
});
test("stray description files cannot conceal missing expected batches", async t => {
  const f = await fixture("description-missing"); t.after(f.close);
  const start = await f.predict(); await f.wait(start.job_id);
  writeJSON(path.join(start.process_path, "batches_desc/stray.result.json"), { descs: {} });
  const info = await f.request(`/api/run/${start.run_id}/status`);
  assert.equal(info.bdesc_result, 0); assert.equal(info.pending_description_batches.length, 1);
});
test("cross-origin requests and unknown APIs fail as JSON", async t => {
  const f = await fixture(); t.after(f.close);
  await f.request("/api/predict", {}, 403, { Origin: "https://other.example" });
  await f.request("/api/not-found", undefined, 404);
  const status = await new Promise((resolve, reject) => {
    require("node:http").get(f.base + "/api/health", { headers: { Host: "other.example" } }, response => { response.resume(); resolve(response.statusCode); }).on("error", reject);
  });
  assert.equal(status, 403);
});

test("restart restores history, marks an interrupted job and resumes without predict", async t => {
  const f = await fixture();
  const start = await f.predict(); await f.wait(start.job_id); await f.app.shutdown();
  const jobFile = path.join(f.root, "outputs/.web-state/jobs", `${start.job_id}.json`);
  const saved = JSON.parse(fs.readFileSync(jobFile)); saved.status = "analyzing"; saved.pid = null; writeJSON(jobFile, saved);
  const { createWorkbench } = require("../server");
  const second = createWorkbench({ root: f.root, env: { PLC_OPENCODE: path.join(f.root, process.platform === "win32" ? "opencode.cmd" : "opencode") } });
  await new Promise(resolve => second.server.listen(0, "127.0.0.1", resolve));
  t.after(async () => { await second.shutdown(); await f.close(); });
  const base = `http://127.0.0.1:${second.server.address().port}`;
  assert.equal((await (await fetch(`${base}/api/job/${start.job_id}`)).json()).status, "interrupted");
  const resumeResponse = await fetch(`${base}/api/run/${start.run_id}/continue`, { method: "POST" });
  assert.equal(resumeResponse.status, 202);
  const resumed = await resumeResponse.json();
  for (let i = 0; i < 100; i++) {
    const job = await (await fetch(`${base}/api/job/${resumed.job_id}`)).json();
    if (job.status === "succeeded") { assert.doesNotMatch(job.output, /正在解析源码/); return; }
    await new Promise(resolve => setTimeout(resolve, 30));
  }
  assert.fail("resume did not complete");
});
test("large Unicode logs retain every character across byte cursors", async t => {
  const f = await fixture(); t.after(f.close);
  const start = await f.predict(); await f.wait(start.job_id);
  const text = "汉字🙂".repeat(40000);
  fs.writeFileSync(path.join(f.root, "outputs/.web-state/jobs", `${start.job_id}.log`), text, "utf8");
  let offset = 0, result = "", more = true;
  while (more) {
    const chunk = await f.request(`/api/job/${start.job_id}?offset=${offset}`);
    assert.ok(chunk.next_offset > offset); result += chunk.output; offset = chunk.next_offset; more = chunk.has_more;
  }
  assert.equal(result, text);
});
test("source parent can be selected as output root without overwriting source", async t => {
  const f = await fixture(); t.after(f.close);
  const start = await f.predict({ output_root: f.root });
  assert.equal((await f.wait(start.job_id)).status, "succeeded");
  assert.equal(fs.readFileSync(f.source, "utf8"), "<fixture/>");
});
test("a stale success flag cannot hide absent or malformed description results", async t => {
  const f = await fixture("description-missing"); t.after(f.close);
  const start = await f.predict(); await f.wait(start.job_id);
  writeJSON(path.join(start.process_path, "workflow_status.json"), { success: true });
  fs.writeFileSync(path.join(start.process_path, "batches_desc/batch_001.result.json"), "broken-json");
  const status = await f.request(`/api/run/${start.run_id}/status`);
  assert.notEqual(status.finish_status, "success"); assert.equal(status.bdesc_result, 0);
  assert.ok(status.invalid_batches.length);
});
test("continue asks OpenCode to repair batches rejected by finish validation", async t => {
  const f = await fixture("semantic-fail"); t.after(f.close);
  const start = await f.predict(); assert.equal((await f.wait(start.job_id)).status, "failed");
  f.setMode("normal");
  const resume = await f.request(`/api/run/${start.run_id}/continue`, {}, 202);
  const result = await f.wait(resume.job_id); assert.equal(result.status, "succeeded");
  assert.match(result.output, /模型进程收到 EOF/);
  const task = JSON.parse(fs.readFileSync(path.join(f.root, "captured-task.json"))).task;
  assert.deepEqual(task.process_batches, ["batches/B00001.result.json"]);
  assert.deepEqual(task.validation_issues, ["B00001 invalid evidence"]);
});

test("a surviving subprocess from an interrupted server can be stopped explicitly", async t => {
  const f = await fixture();
  const start = await f.predict(); await f.wait(start.job_id); await f.app.shutdown();
  const child = require("node:child_process").spawn(process.execPath, ["-e", "setInterval(()=>{},1000)"], { detached: process.platform !== "win32", stdio: "ignore" });
  const record = path.join(f.root, "outputs/.web-state/jobs", `${start.job_id}.json`);
  const job = JSON.parse(fs.readFileSync(record)); job.status = "analyzing"; job.pid = child.pid; writeJSON(record, job);
  writeJSON(path.join(f.root, "outputs/.web-state/active.json"), { server_pid: 2000000000, child_pid: child.pid, job_id: start.job_id });
  const second = require("../server").createWorkbench({ root: f.root });
  await new Promise(resolve => second.server.listen(0, "127.0.0.1", resolve));
  t.after(async () => { if (child.exitCode === null) child.kill(); await second.shutdown(); await f.close(); });
  const base = `http://127.0.0.1:${second.server.address().port}`;
  const before = await (await fetch(`${base}/api/job/${start.job_id}`)).json();
  assert.equal(before.status, "interrupted"); assert.equal(before.orphaned, true);
  const response = await fetch(`${base}/api/job/${start.job_id}/stop`, { method: "POST" });
  assert.equal(response.status, 200);
  assert.equal((await response.json()).status, "cancelled");
});
