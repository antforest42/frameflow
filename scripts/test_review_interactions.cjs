"use strict";

const assert = require("node:assert/strict");
const childProcess = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

function parseArgs(argv) {
  const result = {};
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (!value.startsWith("--")) continue;
    const key = value.slice(2);
    if (key === "contract-only") {
      result[key] = true;
    } else {
      result[key] = argv[index + 1];
      index += 1;
    }
  }
  return result;
}

function nearlyEqual(left, right, tolerance = 2) {
  return Math.abs(Number(left) - Number(right)) <= tolerance;
}

function required(value, name) {
  if (!value) throw new Error(`缺少参数 --${name}`);
  return value;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const root = path.resolve(args.root || path.join(__dirname, ".."));
  const python = required(args.python, "python");
  const generator = path.join(root, "scripts", "generate_review.py");
  const fixture = path.join(root, "tests", "fixtures", "review-schema2");
  const temporaryRoot = fs.mkdtempSync(
    path.join(os.tmpdir(), "life-evolution-review-regression-")
  );
  const project = path.join(temporaryRoot, "review-schema2");
  const reviewPath = path.join(project, "review.html");
  const approvalTemplate = path.join(project, "approval-template.json");
  const checks = [];
  let browser = null;

  const pass = (name) => checks.push(name);
  const snapshot = (page) =>
    page.evaluate(() => window.__reviewTestApi.snapshot());
  const titleLeft = (page, key) =>
    page.evaluate(
      (coverKey) =>
        document
          .getElementById(`titleLayer-${coverKey}`)
          .getBoundingClientRect().left,
      key
    );
  const titleCenter = (page, key) =>
    page.evaluate((coverKey) => {
      const rect = document
        .getElementById(`titleLayer-${coverKey}`)
        .getBoundingClientRect();
      return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
    }, key);

  const selectCharacters = async (page, key, start, end) => {
    await page.evaluate(
      ({ coverKey, selectionStart, selectionEnd }) => {
        const editor = document.getElementById(`titleEditor-${coverKey}`);
        const spans = [...editor.querySelectorAll("span")].filter(
          (span) => span.textContent && !/\s/.test(span.textContent)
        );
        if (
          selectionStart < 0 ||
          selectionEnd > spans.length ||
          selectionStart >= selectionEnd
        ) {
          throw new Error("测试字符范围无效");
        }
        const range = document.createRange();
        range.setStart(spans[selectionStart].firstChild, 0);
        range.setEnd(
          spans[selectionEnd - 1].firstChild,
          spans[selectionEnd - 1].textContent.length
        );
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        editor.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
      },
      {
        coverKey: key,
        selectionStart: start,
        selectionEnd: end,
      }
    );
  };

  const drag = async (page, locator, deltaX, deltaY, inspectDuringDrag) => {
    const box = await locator.boundingBox();
    assert.ok(box, "拖动控制点不可见");
    const startX = box.x + box.width / 2;
    const startY = box.y + box.height / 2;
    await page.mouse.move(startX, startY);
    await page.mouse.down();
    await page.mouse.move(startX + deltaX, startY + deltaY, { steps: 4 });
    if (inspectDuringDrag) await inspectDuringDrag();
    await page.mouse.up();
  };

  try {
    fs.cpSync(fixture, project, { recursive: true });
    const configPath = path.join(project, "config.json");
    const planPath = path.join(project, "plan.json");
    const config = JSON.parse(fs.readFileSync(configPath, "utf8"));
    const plan = JSON.parse(fs.readFileSync(planPath, "utf8"));
    const approvedSample = path.join(
      root,
      "assets",
      "approved-samples",
      "00-你身边人为什么总打你的隐私.png"
    );
    config.asset_root = path.join(root, "assets");
    plan.cover.image_path = approvedSample;
    for (const scene of plan.scenes) scene.image_path = approvedSample;
    fs.writeFileSync(configPath, `${JSON.stringify(config, null, 2)}\n`, "utf8");
    fs.writeFileSync(planPath, `${JSON.stringify(plan, null, 2)}\n`, "utf8");
    childProcess.execFileSync(
      python,
      [
        "-B",
        generator,
        planPath,
        reviewPath,
        "--approval-template",
        approvalTemplate,
        "--config",
        configPath,
      ],
      { encoding: "utf8", windowsHide: true }
    );

    const html = fs.readFileSync(reviewPath, "utf8");
    for (const marker of [
      'id="coverGrid"',
      "window.__reviewTestApi",
      "line-spacing-handle",
      "letter-spacing-handle",
      'id="bgmOptions"',
      'id="introModeOptions"',
      "copyApproval",
    ]) {
      assert.ok(html.includes(marker), `固定审核页缺少标记：${marker}`);
    }
    const payloadMatch = html.match(
      /<script id="reviewData" type="application\/json">([\s\S]*?)<\/script>/
    );
    assert.ok(payloadMatch, "固定审核页缺少 reviewData");
    const reviewData = JSON.parse(payloadMatch[1]);
    assert.deepEqual(Object.keys(reviewData.covers).sort(), [
      "landscape",
      "portrait",
    ]);
    const templateData = JSON.parse(
      fs.readFileSync(approvalTemplate, "utf8")
    );
    assert.equal(templateData.schema_version, 2);
    assert.ok(templateData.covers.landscape);
    assert.ok(templateData.covers.portrait);
    assert.equal(templateData.selected_bgm_id, "A");
    assert.equal(templateData.include_intro, true);
    assert.equal("selected_subtitle" in templateData, false);
    assert.equal(html.includes('id="subtitleInput"'), false);
    assert.equal(html.includes("@时代微情绪"), false);
    assert.ok(html.includes("本期主标题"));
    pass("固定 schema 2 项目可生成双尺寸审核页");
    pass("左上角固定使用头像与本期主标题，不再显示旧账号文字");
    pass("BGM A/B 选项、试听片段与默认选择进入审核页");
    pass("片头保留开关默认开启并进入审批模板");

    if (args["contract-only"]) {
      return { passed: true, mode: "contract-only", checks };
    }

    const playwrightPath = required(args["playwright-path"], "playwright-path");
    const { chromium } = require(playwrightPath);
    const attempts = [
      { headless: true },
      { headless: true, channel: "msedge" },
      { headless: true, channel: "chrome" },
    ];
    let lastLaunchError = null;
    for (const options of attempts) {
      try {
        browser = await chromium.launch(options);
        break;
      } catch (error) {
        lastLaunchError = error;
      }
    }
    if (!browser) {
      throw new Error(
        `无法启动 Playwright 浏览器：${lastLaunchError?.message || "未知错误"}`
      );
    }

    const context = await browser.newContext({
      viewport: { width: 1600, height: 1200 },
      deviceScaleFactor: 1,
    });
    await context.addInitScript(() => {
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: {
          writeText: async (text) => {
            window.__copiedApproval = text;
          },
        },
      });
    });
    const page = await context.newPage();
    const consoleProblems = [];
    page.on("console", (message) => {
      if (["error", "warning"].includes(message.type())) {
        consoleProblems.push(`${message.type()}: ${message.text()}`);
      }
    });
    page.on("pageerror", (error) => {
      consoleProblems.push(`pageerror: ${error.message}`);
    });

    await page.goto(pathToFileURL(reviewPath).href, { waitUntil: "load" });
    await page.locator("#coverStage-landscape").waitFor();
    assert.equal(await page.title(), "回归测试标题｜最终确认");
    assert.equal(await page.locator(".cover-card").count(), 2);
    assert.equal(await page.locator(".scene-card").count(), 1);
    assert.ok(await page.locator("body").innerText());
    pass("页面身份、正文、双封面与场景卡加载正常");

    assert.equal(await page.locator(".bgm-choice").count(), 2);
    assert.equal(
      await page.locator('.bgm-choice audio').count(),
      2
    );
    await page.locator('input[name="bgmOption"][value="B"]').check();
    let current = await snapshot(page);
    assert.equal(current.state.selectedBgmId, "B");
    assert.ok(
      await page.locator('.bgm-choice[data-bgm-id="B"]').evaluate(
        (element) => element.classList.contains("selected")
      )
    );
    pass("审核页可试听并将正文背景音乐切换为选项 B");

    assert.equal(await page.locator(".intro-mode-choice").count(), 2);
    await page.locator('input[name="introMode"][value="omit"]').check();
    current = await snapshot(page);
    assert.equal(current.state.includeIntro, false);
    assert.equal(await page.locator("#introInput").isDisabled(), true);
    assert.equal(await page.locator("#introStage").textContent(), "本期不保留片头");
    await page.locator('input[name="introMode"][value="include"]').check();
    current = await snapshot(page);
    assert.equal(current.state.includeIntro, true);
    assert.equal(await page.locator("#introInput").isDisabled(), false);
    pass("审核页可切换保留或移除前 3.933 秒片头，并同步片头编辑状态");

    await page.locator("#introInput").fill("片头文字\n也能修改");
    current = await snapshot(page);
    assert.equal(current.state.introText, "片头文字\n也能修改");
    assert.equal(
      await page.locator("#introStage").textContent(),
      "片头文字\n也能修改"
    );
    pass("片头标题内容与换行可编辑并实时进入预览");

    await page.locator('input[name="introMode"][value="omit"]').check();
    current = await snapshot(page);
    assert.equal(current.state.includeIntro, false);

    await page.evaluate(() => {
      const editor = document.getElementById("titleEditor-landscape");
      editor.focus();
      editor.innerText = "回归\n测试标题";
      editor.dispatchEvent(
        new InputEvent("input", { bubbles: true, inputType: "insertText" })
      );
      editor.blur();
    });
    current = await snapshot(page);
    assert.equal(current.state.covers.landscape.coverText, "回归\n测试标题");
    assert.equal(
      current.state.covers.portrait.coverText,
      "回归测试标题"
    );
    pass("横版文案与换行可编辑，竖版布局状态保持独立");

    await page.evaluate(() => {
      const editor = document.getElementById("titleEditor-portrait");
      editor.focus();
      editor.innerText = "竖版封面\n也能改字";
      editor.dispatchEvent(
        new InputEvent("input", { bubbles: true, inputType: "insertText" })
      );
      editor.blur();
    });
    current = await snapshot(page);
    assert.equal(
      current.state.covers.portrait.coverText,
      "竖版封面\n也能改字"
    );
    assert.equal(current.state.covers.landscape.coverText, "回归\n测试标题");
    pass("竖版封面文案可编辑且不影响横版封面");

    await selectCharacters(page, "landscape", 0, 2);
    await page
      .locator('[data-cover-key="landscape"] [data-action="accent-add"]')
      .click();
    current = await snapshot(page);
    assert.deepEqual(
      current.state.covers.landscape.accentIndices.slice(0, 2),
      [0, 1]
    );
    pass("选区离开文字后仍可设置强调色");

    await selectCharacters(page, "landscape", 0, 2);
    await page
      .locator(
        '[data-cover-key="landscape"] [data-type-action="character-plus"]'
      )
      .click();
    current = await snapshot(page);
    assert.equal(current.state.covers.landscape.characterSizeScales["0"], 1.1);
    assert.equal(current.state.covers.landscape.characterSizeScales["1"], 1.1);
    const characterSizes = await page.evaluate(() => {
      const spans = [
        ...document.querySelectorAll("#titleEditor-landscape span"),
      ].filter((span) => span.textContent && !/\s/.test(span.textContent));
      return spans.slice(0, 3).map((span) => parseFloat(span.style.fontSize));
    });
    assert.ok(characterSizes[0] > characterSizes[2]);
    pass("所选单字大小立即写入状态并实时呈现");

    await page.locator("#titleEditor-landscape").click();
    const beforeMove = await snapshot(page);
    await drag(
      page,
      page.locator(
        '[data-cover-key="landscape"] .title-layer .move-handle'
      ),
      34,
      22
    );
    current = await snapshot(page);
    assert.ok(
      current.state.covers.landscape.textX !==
        beforeMove.state.covers.landscape.textX ||
        current.state.covers.landscape.textY !==
          beforeMove.state.covers.landscape.textY
    );
    pass("文字移动控制点只改变文字位置");

    await page.locator("#titleEditor-landscape").click();
    const beforeLine = await snapshot(page);
    await drag(
      page,
      page.locator(
        '[data-cover-key="landscape"] .line-spacing-handle'
      ),
      0,
      36,
      async () => {
        const during = await snapshot(page);
        assert.ok(
          during.state.covers.landscape.lineSpacing !==
            beforeLine.state.covers.landscape.lineSpacing
        );
        assert.equal(
          during.state.covers.landscape.fontSize,
          beforeLine.state.covers.landscape.fontSize
        );
        assert.equal(
          during.state.covers.landscape.letterSpacing,
          beforeLine.state.covers.landscape.letterSpacing
        );
      }
    );
    pass("底边控制点实时且仅调整行距");

    await page.locator("#titleEditor-landscape").click();
    const beforeLetter = await snapshot(page);
    const leftBeforeLetter = await titleLeft(page, "landscape");
    await drag(
      page,
      page.locator(
        '[data-cover-key="landscape"] .letter-spacing-handle'
      ),
      46,
      0,
      async () => {
        const during = await snapshot(page);
        const leftDuring = await titleLeft(page, "landscape");
        assert.ok(
          during.state.covers.landscape.letterSpacing !==
            beforeLetter.state.covers.landscape.letterSpacing
        );
        assert.equal(
          during.state.covers.landscape.fontSize,
          beforeLetter.state.covers.landscape.fontSize
        );
        assert.equal(
          during.state.covers.landscape.lineSpacing,
          beforeLetter.state.covers.landscape.lineSpacing
        );
        assert.ok(nearlyEqual(leftDuring, leftBeforeLetter, 2));
      }
    );
    pass("右边控制点实时且固定左边调整字距");

    await page.locator("#titleEditor-landscape").click();
    const beforeResize = await snapshot(page);
    const centerBeforeResize = await titleCenter(page, "landscape");
    await drag(
      page,
      page.locator('[data-cover-key="landscape"] .title-layer .resize-handle'),
      44,
      32,
      async () => {
        const during = await snapshot(page);
        const centerDuring = await titleCenter(page, "landscape");
        const before = beforeResize.state.covers.landscape;
        const after = during.state.covers.landscape;
        const factor = after.fontSize / before.fontSize;
        assert.ok(Math.abs(factor - 1) > 0.01);
        assert.ok(nearlyEqual(after.lineSpacing / before.lineSpacing, factor, 0.08));
        assert.ok(
          nearlyEqual(after.letterSpacing / before.letterSpacing, factor, 0.08)
        );
        assert.ok(nearlyEqual(centerDuring.x, centerBeforeResize.x, 2));
        assert.ok(nearlyEqual(centerDuring.y, centerBeforeResize.y, 2));
      }
    );
    pass("右下角只围绕视觉中心等比缩放整体文字");

    const beforeSubject = await snapshot(page);
    await drag(
      page,
      page.locator("#subjectLayer-landscape"),
      28,
      18
    );
    current = await snapshot(page);
    assert.ok(
      current.state.covers.landscape.subjectX !==
        beforeSubject.state.covers.landscape.subjectX ||
        current.state.covers.landscape.subjectY !==
          beforeSubject.state.covers.landscape.subjectY
    );
    pass("主体图可独立拖动");

    await page.locator("#coverStage-landscape").click({
      position: { x: 8, y: 8 },
    });
    current = await snapshot(page);
    assert.equal(current.selectedLayer, null);
    assert.equal(
      Object.values(current.textSelections).filter(Boolean).length,
      0
    );
    assert.equal(await page.locator(".cover-layer.selected").count(), 0);
    pass("点击画布空白处清除选框、控制点与文字选区");

    const copyButton = page.locator("#copyApproval");
    assert.equal(await copyButton.isDisabled(), false);
    await copyButton.click();
    await page.waitForFunction(
      () => document.getElementById("copyApproval").textContent.includes("已复制")
    );
    const copied = await page.evaluate(() =>
      JSON.parse(window.__copiedApproval)
    );
    assert.equal(copied.schema_version, 2);
    assert.equal(copied.approved, true);
    assert.equal(copied.selected_bgm_id, "B");
    assert.equal(copied.include_intro, false);
    assert.equal("selected_subtitle" in copied, false);
    assert.ok(copied.covers.landscape);
    assert.ok(copied.covers.portrait);
    assert.deepEqual(copied.intro_lines, ["片头文字", "也能修改"]);
    assert.deepEqual(copied.covers.landscape.title_lines, [
      "回归",
      "测试标题",
    ]);
    assert.deepEqual(copied.covers.portrait.title_lines, [
      "竖版封面",
      "也能改字",
    ]);
    assert.notDeepEqual(copied.covers.landscape, copied.covers.portrait);
    pass("复制按钮提供成功反馈并输出可编辑片头及独立双封面 schema 2 数据");

    assert.deepEqual(consoleProblems, []);
    pass("交互全过程无控制台错误或警告");
    await context.close();
    return { passed: true, mode: "browser", checks };
  } finally {
    if (browser) await browser.close();
    fs.rmSync(temporaryRoot, { recursive: true, force: true });
  }
}

main()
  .then((result) => {
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
  })
  .catch((error) => {
    process.stderr.write(
      `${JSON.stringify(
        {
          passed: false,
          message: error.message,
          stack: error.stack,
        },
        null,
        2
      )}\n`
    );
    process.exitCode = 1;
  });
