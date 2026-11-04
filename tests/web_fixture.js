"use strict";
const fs = require("node:fs"), os = require("node:os"), path = require("node:path");
const { createWorkbench } = require("../server");

async function fixture(mode = "normal", overrides = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "plc 中文 &% ' "));
  fs.cpSync(path.join(__dirname, "../web"), path.join(root, "web"), { recursive: true });
  fs.mkdirSync(path.join(root, ".opencode/agents"), { recursive: true });
  fs.writeFileSync(path.join(root, ".opencode/agents/plc-sequence.md"), "fixture");
  fs.copyFileSync(path.join(__dirname, "fixtures/plc_cli.py"), path.join(root, "plc_cli.py"));
  const js = fs.readFileSync(path.join(__dirname, "fixtures/opencode.js"), "utf8");
  let executable;
  if (process.platform === "win32") {
    executable = path.join(root, "opencode.cmd");
    fs.writeFileSync(path.join(root, "fixture-opencode.js"), js);
    fs.writeFileSync(executable, `@echo off\r\n"${process.execPath}" "%~dp0fixture-opencode.js" %*\r\n`);
  } else {
    executable = path.join(root, "opencode");
    fs.writeFileSync(executable, `#!${process.execPath}\n${js}`, { mode: 0o755 });
  }
  fs.writeFileSync(path.join(root, "fixture-mode.txt"), mode);
  fs.writeFileSync(path.join(root, "web_browse.py"), `from pathlib import Path\nimport sys\np=Path(__file__).parent\nm=(p/'fixture-mode.txt').read_text()\nif m=='browse-error':\n print('fixture tkinter unavailable',file=sys.stderr);sys.exit(2)\nprint('' if m=='browse-cancel' else str(p/"source.xml"))\n`);
  const source = path.join(root, "source.xml"); fs.writeFileSync(source, "<fixture/>");
  const app = createWorkbench({ root, ...overrides, env: { PLC_OPENCODE: executable, ...overrides.env } });
  await new Promise(resolve => app.server.listen(0, "127.0.0.1", resolve));
  const base = `http://127.0.0.1:${app.server.address().port}`;
  const request = async (url, value, expected = 200, headers = {}) => {
    const r = await fetch(base + url, value === undefined ? { headers } : { method: "POST", headers: { "Content-Type": "application/json", ...headers }, body: JSON.stringify(value) });
    const data = await r.json();
    if (r.status !== expected) throw new Error(`HTTP ${r.status} (expected ${expected}): ${JSON.stringify(data)}`);
    return data;
  };
  const predict = (extra = {}) => request("/api/predict", { source, pattern: "Cell.Phase[*]", output_root: path.join(root, "delivery"), ...extra }, 202);
  const wait = async jobID => {
    const deadline = Date.now() + 20000;
    while (Date.now() < deadline) {
      const job = await request(`/api/job/${jobID}`);
      if (!["queued", "predicting", "analyzing", "finishing", "cancelling"].includes(job.status)) return job;
      await new Promise(resolve => setTimeout(resolve, 30));
    }
    throw new Error("fixture job did not finish");
  };
  return { root, source, app, base, request, predict, wait,
    setMode: value => fs.writeFileSync(path.join(root, "fixture-mode.txt"), value),
    close: async () => { await app.shutdown(); fs.rmSync(root, { recursive: true, force: true }); },
  };
}
module.exports = { fixture };
