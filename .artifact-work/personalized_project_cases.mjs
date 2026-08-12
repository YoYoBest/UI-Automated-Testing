import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const projectRoot = path.resolve("..");
const inputPath = path.join(projectRoot, "tests/Common_Test_Cases/建设项目_个性化用例.xlsx");
const outputDir = path.join(projectRoot, "outputs/019fc2e9-a3da-7de3-adaf-0e18b712e218");
const outputPath = path.join(outputDir, "建设项目_个性化用例_新增项目四字段版.xlsx");
const workDir = path.join(projectRoot, ".artifact-work/project-cases");
await fs.mkdir(outputDir, { recursive: true });
await fs.mkdir(workDir, { recursive: true });

const headers = [
  "用例ID", "功能", "字段/控件", "测试场景", "前置条件", "测试数据", "操作步骤",
  "预期结果", "优先级", "是否自动化", "执行结果", "测试人员", "测试日期", "备注",
];
const precondition = "用户已登录且拥有新增项目权限；已打开新增项目表单；页面加载完成";
const rows = [
  [
    "BP-ADD-001", "新增项目", "项目类型|projClassify|select", "码值选项完整性",
    precondition, "新建、改造、搬迁、扩产、扩建、固定资产大修、其他",
    "打开“项目类型”下拉框，获取并逐项核对所有可选值",
    "下拉框仅展示：新建、改造、搬迁、扩产、扩建、固定资产大修、其他；选项无缺失、重复或多余，且均可正常选择",
    "P1", "是", "未执行", "", "", "field_code=projClassify;expected_type=options_equal;execution=option_assertion",
  ],
  [
    "BP-ADD-002", "新增项目", "责任板块|belongSection|select", "码值选项完整性",
    precondition, "四川板块、江西板块、海外板块、其他未纳入业务板块管理的子公司",
    "打开“责任板块”下拉框，获取并逐项核对所有可选值",
    "下拉框仅展示：四川板块、江西板块、海外板块、其他未纳入业务板块管理的子公司；选项无缺失、重复或多余，且均可正常选择",
    "P1", "是", "未执行", "", "", "field_code=belongSection;expected_type=options_equal;execution=option_assertion",
  ],
  [
    "BP-ADD-003", "新增项目", "实施主体公司|inveId|company_select", "可选公司数据检查",
    precondition, "系统中有效的实施主体公司",
    "打开“实施主体公司”选择器，查询并选择一条有效公司",
    "选择器展示系统中有效公司；选中后页面显示公司名称，内部值为对应公司ID，名称与ID对应一致",
    "P1", "否", "未执行", "", "", "field_code=inveId;expected_type=valid_company_selected;execution=manual",
  ],
  [
    "BP-ADD-004", "新增项目", "是否需总经办决策|isGmoDecision|radio", "码值选项完整性",
    precondition, "是、否",
    "检查“是否需总经办决策”单选项，并分别选择“是”和“否”",
    "仅展示“是”和“否”两个选项；两个选项均可选择，且同一时间只能选中一个",
    "P1", "是", "未执行", "", "", "field_code=isGmoDecision;expected_type=options_equal;execution=option_assertion",
  ],
];

const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));
const sheet = workbook.worksheets.getItem("新增项目");
try { sheet.unmergeCells("A1:N122"); } catch {}
sheet.getRange("A1:N122").clear({ applyTo: "all" });
sheet.getRange("A1:N1").values = [headers];
sheet.getRange("A2:N5").values = rows;
sheet.showGridLines = false;
sheet.freezePanes.freezeRows(1);
sheet.getRange("A1:N5").format = {
  font: { name: "Microsoft YaHei", size: 10, color: "#1F2937" },
  verticalAlignment: "center", wrapText: true,
};
sheet.getRange("A1:N1").format = {
  fill: "#1F4E78", font: { name: "Microsoft YaHei", size: 10, bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center", verticalAlignment: "center", wrapText: true,
  borders: { preset: "all", style: "thin", color: "#B4C7E7" }, rowHeight: 30,
};
sheet.getRange("A2:N5").format.borders = { preset: "all", style: "thin", color: "#D9E2F3" };
sheet.getRange("A2:N2").format.fill = "#EAF1FB";
sheet.getRange("A4:N4").format.fill = "#EAF1FB";
sheet.getRange("A2:A5").format.horizontalAlignment = "center";
sheet.getRange("I2:M5").format.horizontalAlignment = "center";
sheet.getRange("A2:N5").format.rowHeight = 58;
const widths = [15, 15, 30, 22, 38, 38, 44, 50, 10, 12, 11, 12, 13, 44];
widths.forEach((width, col) => sheet.getRangeByIndexes(0, col, 5, 1).format.columnWidth = width);
sheet.getRange("I2:I5").dataValidation = { rule: { type: "list", values: ["P0", "P1", "P2"] } };
sheet.getRange("J2:J5").dataValidation = { rule: { type: "list", values: ["是", "否"] } };
sheet.getRange("K2:K5").dataValidation = { rule: { type: "list", values: ["未执行", "通过", "失败", "阻塞"] } };

const preview = await workbook.render({ sheetName: "新增项目", range: "A1:N5", scale: 1.5, format: "png" });
await fs.writeFile(path.join(workDir, "新增项目-four-fields.png"), new Uint8Array(await preview.arrayBuffer()));
const sample = await workbook.inspect({
  kind: "table", sheetId: "新增项目", range: "A1:N5", include: "values,formulas",
  tableMaxRows: 5, tableMaxCols: 14, maxChars: 7000,
});
const errors = await workbook.inspect({
  kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 300 }, summary: "formula errors",
});
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);

const exported = await SpreadsheetFile.importXlsx(await FileBlob.load(outputPath));
const values = exported.worksheets.getItem("新增项目").getRange("A1:N5").values;
const nonEmptyCases = values.slice(1).filter((row) => row.some((value) => value != null && String(value).trim()));
const problems = [];
if (JSON.stringify(values[0]) !== JSON.stringify(headers)) problems.push("14列表头不一致");
if (nonEmptyCases.length !== 4) problems.push(`有效用例不是4行：${nonEmptyCases.length}`);
if (new Set(nonEmptyCases.map((row) => row[0])).size !== 4) problems.push("用例ID不唯一");
for (const [rowIndex, row] of nonEmptyCases.entries()) {
  for (const col of [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 13]) {
    if (row[col] == null || !String(row[col]).trim()) problems.push(`${String.fromCharCode(65 + col)}${rowIndex + 2}为空`);
  }
}
await fs.writeFile(path.join(workDir, "four-fields-verification.json"), JSON.stringify({
  outputPath, caseCount: nonEmptyCases.length, problems,
  formulaErrors: errors.ndjson || "", sample: sample.ndjson || "",
}, null, 2), "utf8");
console.log(JSON.stringify({ outputPath, caseCount: nonEmptyCases.length, problems, formulaErrors: errors.ndjson || "" }, null, 2));
if (problems.length) process.exitCode = 2;
