import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const inputPath = "D:/Auto_Testing/UI-Smoke-Testing/tests/Common_Test_Cases/建设项目_个性化用例.xlsx";
const previewPath = "D:/Auto_Testing/UI-Smoke-Testing/work/20260814_js_add_001/target-preview.png";

const input = await FileBlob.load(inputPath);
const workbook = await SpreadsheetFile.importXlsx(input);

const sheets = await workbook.inspect({
  kind: "sheet",
  include: "id,name",
  maxChars: 4000,
});
console.log(sheets.ndjson);

for (const sheetName of ["新增项目", "项目决策", "建设项目"]) {
  const sheet = workbook.worksheets.getItem(sheetName);
  const used = sheet.getUsedRange();
  console.log(`SHEET:${sheetName}`);
  console.log(JSON.stringify(used.values));
}

const preview = await workbook.render({
  sheetName: "项目决策",
  range: "A1:O20",
  scale: 1.5,
  format: "png",
});
await fs.writeFile(previewPath, new Uint8Array(await preview.arrayBuffer()));
