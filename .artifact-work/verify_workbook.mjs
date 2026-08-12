import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const root = path.resolve("..");
const file = path.join(root, "outputs/019fc2e9-a3da-7de3-adaf-0e18b712e218/公共用例_宋佳慧_全页签标准化版.xlsx");
const headers = ["用例ID", "功能", "字段/控件", "测试场景", "前置条件", "测试数据", "操作步骤", "预期结果", "优先级", "是否适用", "执行结果", "测试人员", "测试日期", "备注"];
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(file));
const ids = new Set();
const summary = [];
const problems = [];

for (const sheet of workbook.worksheets.items) {
  const values = sheet.getUsedRange()?.values || [];
  const actualHeaders = (values[0] || []).slice(0, 14).map((value) => value == null ? "" : String(value));
  if (JSON.stringify(actualHeaders) !== JSON.stringify(headers)) problems.push(`${sheet.name}: 表头不一致`);
  let valid = 0;
  for (let index = 1; index < values.length; index += 1) {
    const row = values[index] || [];
    if (!row.slice(0, 14).some((value) => value != null && String(value).trim())) continue;
    const id = String(row[0] || "").trim();
    if (!id) problems.push(`${sheet.name}!A${index + 1}: 缺少用例ID`);
    if (ids.has(id)) problems.push(`${sheet.name}!A${index + 1}: 重复用例ID ${id}`);
    ids.add(id);
    for (const col of [1, 2, 3, 4, 5, 6, 7, 8, 9]) {
      if (row[col] == null || !String(row[col]).trim()) problems.push(`${sheet.name}!${String.fromCharCode(65 + col)}${index + 1}: 核心字段为空`);
    }
    if (!["P0", "P1", "P2"].includes(String(row[8] || ""))) problems.push(`${sheet.name}!I${index + 1}: 优先级无效`);
    if (!["是", "否", "待确认"].includes(String(row[9] || ""))) problems.push(`${sheet.name}!J${index + 1}: 是否适用无效`);
    if (!["未执行", "通过", "失败", "阻塞"].includes(String(row[10] || ""))) problems.push(`${sheet.name}!K${index + 1}: 执行结果无效`);
    valid += 1;
  }
  const sample = await workbook.inspect({ kind: "table", sheetId: sheet.name, range: `A1:N${Math.min(6, values.length)}`, include: "values,formulas", tableMaxRows: 6, tableMaxCols: 14, maxChars: 2500 });
  summary.push({ sheet: sheet.name, cases: valid, sample: sample.ndjson });
}

const formulaErrors = await workbook.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A", options: { useRegex: true, maxResults: 300 }, summary: "formula errors" });
console.log(JSON.stringify({ totalCases: ids.size, sheets: summary.map(({sheet, cases}) => ({sheet, cases})), problems, formulaErrors: formulaErrors.ndjson }, null, 2));
if (problems.length) process.exitCode = 2;
