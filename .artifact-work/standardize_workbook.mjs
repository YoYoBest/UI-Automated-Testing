import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const projectRoot = path.resolve("..");
const inputPath = path.join(projectRoot, "tests/Common_Test_Cases/公共用例_宋佳慧_新增页签优化版.xlsx");
const outputDir = path.join(projectRoot, "outputs/019fc2e9-a3da-7de3-adaf-0e18b712e218");
const outputPath = path.join(outputDir, "公共用例_宋佳慧_全页签标准化版.xlsx");
const previewDir = path.join(projectRoot, ".artifact-work/previews-after");

const headers = [
  "用例ID", "功能", "字段/控件", "测试场景", "前置条件", "测试数据", "操作步骤",
  "预期结果", "优先级", "是否适用", "执行结果", "测试人员", "测试日期", "备注",
];
const prefixes = {
  "UI整体": "UI", "权限": "AUTH", "查询": "QUERY", "列表": "LIST",
  "新增_原始": "ADDRAW", "编辑": "EDIT", "查看": "VIEW", "删除": "DELETE",
  "流程": "FLOW", "附件": "ATTACH", "翻页": "PAGE", "导出": "EXPORT",
  "导入": "IMPORT", "打印": "PRINT",
};

const text = (value) => value == null ? "" : String(value).replace(/\u00a0/g, " ").trim();
const headerWords = new Set(["序号", "用例ID"]);

function controlFor(checkpoint, feature) {
  const value = `${checkpoint} ${feature}`;
  const rules = [
    [/按钮|入口/, "按钮"], [/权限|账号|密码|登录/, "权限/账号"],
    [/查询|筛选|搜索|清空|重置/, "查询条件"], [/列表|表头|列|排序/, "列表"],
    [/金额|数字|百分比|比例/, "数值字段"], [/文本|字段名|输入框|必填/, "输入字段"],
    [/日期|时间/, "日期控件"], [/下拉|码值|单选|多选/, "选择控件"],
    [/附件|上传|下载/, "附件控件"], [/翻页|分页/, "分页控件"],
    [/导入/, "导入功能"], [/导出/, "导出功能"], [/打印/, "打印功能"],
    [/删除/, "删除功能"], [/编辑/, "编辑表单"], [/查看|详情/, "查看页面"],
    [/流程|审批|提交|撤回|退回/, "流程操作"], [/样式|布局|字体|边框|对齐|换行/, "页面布局"],
  ];
  return rules.find(([pattern]) => pattern.test(value))?.[1] || checkpoint || feature || "页面";
}

function priorityFor(checkpoint, expected, feature) {
  const value = `${checkpoint} ${expected} ${feature}`;
  if (/删除|权限|登录|密码|必填|保存|提交|审批|导入失败|数据正确性/.test(value)) return "P0";
  if (/样式|字体|边框|对齐|换行|颜色|鼠标|美观/.test(value)) return "P2";
  return "P1";
}

function preconditionFor(sheetName, feature, original) {
  if (original) return original;
  if (sheetName === "权限") return "已准备具备不同角色和数据范围的测试账号；用户已登录";
  if (sheetName === "删除") return "用户已登录且拥有删除权限；存在可安全删除的本次测试数据";
  if (sheetName === "流程") return "用户已登录且拥有对应流程权限；存在处于目标流程状态的本次测试数据";
  if (sheetName === "导入") return "用户已登录且拥有导入权限；已准备对应导入文件";
  if (sheetName === "导出" || sheetName === "打印") return `用户已登录且拥有${sheetName}权限；列表存在可验证数据`;
  return `用户已登录且拥有${feature || sheetName}权限；页面加载完成`;
}

function testDataFor(operation, expected, sheetName) {
  const op = text(operation);
  if (/导入|上传/.test(`${op}${expected}`)) return op || `符合当前场景的${sheetName}测试文件`;
  const input = op.match(/(?:输入|填写|选择|设置)([^；;。\n]+)/);
  if (input) return text(input[1]);
  if (/无数据/.test(expected)) return "0 条数据";
  if (/一页|每页|翻页/.test(`${op}${expected}`)) return "0 条、1 页、跨页边界数据";
  return "无";
}

function operationFor(sheetName, feature, checkpoint, original) {
  if (original) return original;
  const target = checkpoint || feature || sheetName;
  return `进入${sheetName}相关页面，执行“${target}”检查或操作`;
}

function normalizeApplicable(value) {
  const current = text(value);
  if (["是", "否", "待确认"].includes(current)) return current;
  if (/通用/.test(current)) return "是";
  return "待确认";
}

function buildCases(sheet) {
  const used = sheet.getUsedRange();
  const values = used?.values || [];
  const rows = [];
  let feature = "";
  let checkpoint = "";
  for (let rowIndex = 0; rowIndex < values.length; rowIndex += 1) {
    const row = values[rowIndex] || [];
    const sequence = text(row[0]);
    const rowFeature = text(row[1]);
    const rowCheckpoint = text(row[2]);
    const precondition = text(row[3]);
    const operation = text(row[4]);
    const expected = text(row[5]);
    const supplement = text(row[6]);
    const applicable = text(row[7]);
    if (headerWords.has(sequence) || rowFeature === "功能" || rowCheckpoint === "检查点") continue;
    if (rowFeature) feature = rowFeature;
    if (rowCheckpoint) checkpoint = rowCheckpoint;
    const onlyTitle = sequence && !rowFeature && !rowCheckpoint && !operation && !expected;
    if (onlyTitle || (!checkpoint && !operation && !expected)) continue;
    if (!expected && !operation) continue;
    const currentFeature = feature || sheet.name;
    const currentCheckpoint = checkpoint || currentFeature;
    const sourceRemark = `来源：原“${sheet.name}”页签第 ${rowIndex + 1} 行`;
    rows.push([
      "", currentFeature, controlFor(currentCheckpoint, currentFeature), currentCheckpoint,
      preconditionFor(sheet.name, currentFeature, precondition),
      testDataFor(operation, expected, sheet.name),
      operationFor(sheet.name, currentFeature, currentCheckpoint, operation),
      expected || `操作完成且页面反馈与“${currentCheckpoint}”要求一致`,
      priorityFor(currentCheckpoint, expected, currentFeature),
      normalizeApplicable(applicable), "未执行", "", "",
      [supplement, sourceRemark].filter(Boolean).join("；"),
    ]);
  }
  const prefix = prefixes[sheet.name];
  rows.forEach((row, index) => { row[0] = `${prefix}-${String(index + 1).padStart(3, "0")}`; });
  return rows;
}

function styleSheet(sheet, rowCount) {
  sheet.showGridLines = false;
  sheet.freezePanes.freezeRows(1);
  const full = sheet.getRange(`A1:N${Math.max(2, rowCount + 1)}`);
  full.format = {
    font: { name: "Microsoft YaHei", size: 10, color: "#1F2937" },
    verticalAlignment: "center",
    wrapText: true,
  };
  const header = sheet.getRange("A1:N1");
  header.format = {
    fill: "#1F4E78",
    font: { name: "Microsoft YaHei", size: 10, bold: true, color: "#FFFFFF" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "all", style: "thin", color: "#B4C7E7" },
    rowHeight: 30,
  };
  if (rowCount) {
    const body = sheet.getRange(`A2:N${rowCount + 1}`);
    body.format.borders = {
      insideHorizontal: { style: "thin", color: "#D9E2F3" },
      insideVertical: { style: "thin", color: "#E5E7EB" },
      bottom: { style: "thin", color: "#B4C7E7" },
    };
    sheet.getRange(`A2:A${rowCount + 1}`).format.horizontalAlignment = "center";
    sheet.getRange(`I2:M${rowCount + 1}`).format.horizontalAlignment = "center";
    sheet.getRange(`A2:N${rowCount + 1}`).format.rowHeight = 42;
    sheet.getRange(`I2:I${rowCount + 1}`).dataValidation = {
      rule: { type: "list", values: ["P0", "P1", "P2"] },
    };
    sheet.getRange(`J2:J${rowCount + 1}`).dataValidation = {
      rule: { type: "list", values: ["是", "否", "待确认"] },
    };
    sheet.getRange(`K2:K${rowCount + 1}`).dataValidation = {
      rule: { type: "list", values: ["未执行", "通过", "失败", "阻塞"] },
    };
  }
  const widths = [14, 15, 17, 24, 34, 22, 42, 46, 10, 11, 11, 12, 13, 30];
  widths.forEach((width, index) => {
    sheet.getRangeByIndexes(0, index, Math.max(2, rowCount + 1), 1).format.columnWidth = width;
  });
}

await fs.mkdir(outputDir, { recursive: true });
await fs.mkdir(previewDir, { recursive: true });
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));
const counts = [];
for (const sheet of workbook.worksheets.items) {
  if (sheet.name === "新增") {
    const existingRows = Math.max(0, (sheet.getUsedRange()?.values || []).length - 1);
    styleSheet(sheet, existingRows);
    counts.push({ sheet: sheet.name, cases: existingRows, reference: true });
    continue;
  }
  const cases = buildCases(sheet);
  const oldRows = (sheet.getUsedRange()?.values || []).length;
  try { sheet.unmergeCells(`A1:AB${Math.max(1, oldRows)}`); } catch {}
  sheet.getRange(`A1:AB${Math.max(oldRows, cases.length + 1, 2)}`).clear({ applyTo: "all" });
  sheet.getRange("A1:N1").values = [headers];
  if (cases.length) sheet.getRange(`A2:N${cases.length + 1}`).values = cases;
  styleSheet(sheet, cases.length);
  counts.push({ sheet: sheet.name, cases: cases.length, reference: false });
}

const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 300 },
  summary: "final formula error scan",
});
await fs.writeFile(path.join(projectRoot, ".artifact-work/formula-errors.ndjson"), errors.ndjson || "", "utf8");

for (const sheet of workbook.worksheets.items) {
  const preview = await workbook.render({ sheetName: sheet.name, range: `A1:N${Math.min(25, (sheet.getUsedRange()?.values || []).length)}`, scale: 1, format: "png" });
  const safe = sheet.name.replace(/[<>:"/\\|?*]/g, "_");
  await fs.writeFile(path.join(previewDir, `${safe}.png`), new Uint8Array(await preview.arrayBuffer()));
}

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
await fs.writeFile(path.join(projectRoot, ".artifact-work/case-counts.json"), JSON.stringify(counts, null, 2), "utf8");
console.log(JSON.stringify({ outputPath, counts, formulaErrors: errors.ndjson || "" }, null, 2));
