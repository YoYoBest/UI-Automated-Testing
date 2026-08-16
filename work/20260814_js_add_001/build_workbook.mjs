import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const inputPath = "D:/Auto_Testing/UI-Smoke-Testing/tests/Common_Test_Cases/建设项目_个性化用例.xlsx";
const outputDir = "D:/Auto_Testing/UI-Smoke-Testing/outputs/20260814_js_add_001";
const outputPath = `${outputDir}/建设项目_个性化用例.xlsx`;
const previewPath = `${outputDir}/建设项目-用例预览.png`;

await fs.mkdir(outputDir, { recursive: true });

const input = await FileBlob.load(inputPath);
const workbook = await SpreadsheetFile.importXlsx(input);
const sheet = workbook.worksheets.getItem("建设项目");

// Preserve the existing case and its formatting while reserving row 2 for the new Add case.
sheet.getRange("A2:N2").copyTo(sheet.getRange("A3:N3"), "all");
sheet.getRange("A2:N2").values = [[
  "JS-ADD-001",
  "新增",
  "新增按钮",
  "项目未完成决策，不允许新增项目进度",
  "用户已登录且拥有项目进度新增权限；存在一条项目决策状态为“未完成决策”的建设项目，且可进入其项目进度页面",
  "建设项目：项目决策状态为“未完成决策”",
  "1. 进入该建设项目的项目进度页面。2. 尝试点击“新增”按钮。3. 查看页面是否打开新增表单，并核对项目进度列表。",
  "“新增”按钮不可用，无法进入新增表单；项目进度列表不新增任何记录。",
  "P0",
  "是",
  "未执行",
  null,
  null,
  null,
]];

const verification = await workbook.inspect({
  kind: "table",
  sheetId: "建设项目",
  range: "A1:N3",
  include: "values,formulas",
  tableMaxRows: 3,
  tableMaxCols: 14,
  tableMaxCellChars: 180,
  maxChars: 8000,
});
console.log(verification.ndjson);

const preview = await workbook.render({
  sheetName: "建设项目",
  range: "A1:N6",
  scale: 1.5,
  format: "png",
});
await fs.writeFile(previewPath, new Uint8Array(await preview.arrayBuffer()));

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
