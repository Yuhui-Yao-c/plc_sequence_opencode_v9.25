"use strict";
// Real repository XML + real Python predict/finish. Only model responses are
// synthetic and every source event stays explicitly unresolved in this test.
const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path");
const { fixture } = require("./web_fixture");
const { readCSV, probe, findExecutable } = require("../web_runtime");

(async () => {
  const f = await fixture();
  try {
    const project = path.resolve(__dirname, "..");
    for (const name of fs.readdirSync(project)) if (name.endsWith(".py")) fs.copyFileSync(path.join(project, name), path.join(f.root, name));
    for (const name of ["examples", "评价器"]) fs.cpSync(path.join(project, name), path.join(f.root, name), { recursive: true });
    fs.copyFileSync(path.join(project, "action_wordbook.json"), path.join(f.root, "action_wordbook.json"));
    const source = path.join(f.root, "examples/omron_native_minimal.xml"), before = fs.readFileSync(source);
    const start = await f.predict({ source });
    const job = await f.wait(start.job_id);
    assert.ok(["succeeded", "partial"].includes(job.status), `${job.status}: ${job.output}`);
    assert.deepEqual(fs.readFileSync(source), before);
    const rows = readCSV(path.join(start.process_path, "final_prediction_pairs.csv"));
    assert.ok(rows.length > 0);
    const workbook = job.run.files.find(f => f.path.endsWith("_步序采集表.xlsx")); assert.ok(workbook);
    const checked = await probe(findExecutable([process.env.PLC_PYTHON, "python", "python3", "py"]), ["-c", "import openpyxl,sys; w=openpyxl.load_workbook(sys.argv[1]); print(len(w.sheetnames))", path.join(start.process_path, workbook.path)], f.root, process.env);
    assert.equal(checked.ok, true, checked.error);
    const summary = { successful: true, source_example: "examples/omron_native_minimal.xml", status: job.status, source_unchanged: true, candidate_count: job.run.candidate_count, final_pair_rows: rows.length, workbook_verified_by_openpyxl: true, provider: "Synthetic replies; all process events explicitly unresolved; no real model quality claim", warnings: job.run.warnings };
    const target = process.env.PLC_TEST_ARTIFACTS;
    if (target) { fs.mkdirSync(target, { recursive: true }); fs.writeFileSync(path.join(target, "real-pipeline-summary.json"), JSON.stringify(summary, null, 2)); }
    console.log(JSON.stringify(summary, null, 2));
  } finally { await f.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
