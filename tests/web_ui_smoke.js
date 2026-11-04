"use strict";
// Optional browser acceptance test. Requires playwright in the QA environment.
// The provider is synthetic; this tests UI/transport, not model quality.
const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path");
const { chromium } = require("playwright");
const { fixture } = require("./web_fixture");

(async () => {
  const f = await fixture();
  const browser = await chromium.launch({ headless: true, ...(process.env.PLC_TEST_CHROMIUM ? { executablePath: process.env.PLC_TEST_CHROMIUM } : {}), args: ["--no-sandbox", "--disable-dev-shm-usage", "--no-zygote", "--single-process"] });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 }, acceptDownloads: true });
  const page = await context.newPage();
  const errors = [], cases = [];
  page.on("pageerror", error => errors.push(error.message));
  const state = text => page.waitForFunction(text => document.getElementById("phaseHeroName").textContent === text, text, { timeout: 20000 });
  const artifactDir = path.resolve(process.env.PLC_TEST_ARTIFACTS || "test-results"); fs.mkdirSync(artifactDir, { recursive: true });
  let missing;
  try {
    await page.goto(f.base); await page.getByText("环境检查通过", { exact: true }).waitFor();
    cases.push("Environment check enables task form");
    await page.locator("#btnPredict").click();
    assert.match(await page.locator("#notice").innerText(), /请填写源码路径/);
    cases.push("Missing fields produce visible validation");
    await page.locator('[data-browse="inpSource"]').click();
    await page.waitForFunction(() => document.getElementById("inpSource").value.endsWith("source.xml"));
    cases.push("File-picker result reaches source input");
    const source = path.join(f.root, "源码'一 &.xml"); fs.writeFileSync(source, "<fixture/>");
    const fillTask = async () => {
      await page.locator("#inpSource").fill(source);
      await page.locator("#inpPattern").fill("Cell.Phase[*]");
      await page.locator("#inpOutputRoot").fill(f.root);
    };
    await fillTask();
    await page.locator("#calibCheck").check();
    await page.locator("#inpManual").fill("stale-nonexistent-manual.xls");
    await page.locator("#calibCheck").uncheck();
    await page.locator("#btnPredict").click(); await state("已完成");
    await page.locator("#resultBody tr").first().waitFor();
    assert.equal(await page.locator("#resultBody tr").count(), 2);
    assert.match(await page.locator("#resultBody").innerText(), /夹紧气缸/);
    assert.match(await page.locator("#resultBody").innerText(), /顶升气缸/);
    assert.match(await page.locator("#jobLog").innerText(), /模型进程收到 EOF/);
    cases.push("Task completes with Unicode/quote/space paths and live logs");
    cases.push("Disabled calibration does not send stale manual path");
    cases.push("Duplicate labels retain distinct action descriptions");
    const downloadEvent = page.waitForEvent("download");
    await page.getByRole("link", { name: /测试_步序采集表.xlsx/ }).click();
    const downloaded = await downloadEvent;
    assert.equal(fs.readFileSync(await downloaded.path(), "utf8"), "fixture-workbook-bytes");
    cases.push("Chinese-named workbook downloads from actual delivery list");
    await page.reload(); await state("已完成");
    assert.equal(await page.locator("#resultBody tr").count(), 2);
    cases.push("Reload restores selected run and completed result");
    await page.locator("#searchBox").fill("顶升");
    await page.waitForFunction(() => document.querySelectorAll("#resultBody tr").length === 1 && document.getElementById("resultBody").textContent.includes("顶升"));
    cases.push("Search filters the actual joined descriptions");

    async function startMode(mode) {
      f.setMode(mode); await page.locator("#btnNewTask").click();
      await page.getByText("环境检查通过", { exact: true }).waitFor(); await fillTask();
      await page.locator("#btnPredict").click();
    }
    await startMode("description-missing"); await state("批次未完成");
    assert.equal(await page.locator("[data-continue]").isVisible(), true);
    f.setMode("normal"); await page.locator("[data-continue]").click(); await state("已完成");
    cases.push("Missing description batch stops completion and resumes successfully");
    await startMode("model-fail"); await state("失败");
    assert.match(await page.locator("#jobLog").innerText(), /fixture model connection failed/);
    cases.push("Model failure is visible and stops polling as a failed job");
    await startMode("slow"); await state("分析工艺与动作描述");
    await page.locator("#btnStop").click(); await state("已停止");
    cases.push("Stop button terminates the real subprocess");
    await startMode("workbook-fail"); await state("部分完成");
    assert.equal(await page.getByRole("link", { name: /final_prediction_pairs.csv/ }).isVisible(), true);
    cases.push("Workbook failure preserves CSV download and shows partial completion");
    await page.locator("#sectionResult").scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(artifactDir, "web-repair-partial.png"), fullPage: true });

    missing = await fixture("normal", { env: { PLC_OPENCODE: path.join(f.root, "not-installed") } });
    await page.goto(missing.base);
    await page.getByText("环境未就绪，请处理下列项目", { exact: true }).waitFor();
    assert.equal(await page.locator("#btnPredict").isEnabled(), false);
    assert.match(await page.locator("#healthDetails").innerText(), /OpenCode/);
    cases.push("Missing OpenCode is diagnosed before task submission");
    assert.deepEqual(errors, []);
    const summary = { successful: true, browser: await browser.version(), cases_passed: cases.length, cases, page_errors: errors, provider: "Synthetic fixture only; no real model was invoked" };
    fs.writeFileSync(path.join(artifactDir, "web-ui-summary.json"), JSON.stringify(summary, null, 2));
    console.log(JSON.stringify(summary, null, 2));
  } finally { await browser.close(); if (missing) await missing.close(); await f.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
