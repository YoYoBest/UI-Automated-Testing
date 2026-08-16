import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const inputPath = "D:/Auto_Testing/UI-Smoke-Testing/tests/Common_Test_Cases/建设项目_个性化用例.xlsx";
const blob = await FileBlob.load(inputPath);
const workbook = await SpreadsheetFile.importXlsx(blob);

const summary = await workbook.inspect({
  kind: "workbook,sheet,table",
  maxChars: 6000,
  tableMaxRows: 12,
  tableMaxCols: 12,
  tableMaxCellChars: 120,
});
console.log(summary.ndjson);

for (const sheetName of ["编辑", "项目进度"] ) {
  try {
    const preview = await workbook.render({ sheetName, range: "A1:H12", scale: 2, format: "png" });
    await fs.writeFile(`D:/Auto_Testing/UI-Smoke-Testing/.codex-tmp/${sheetName}.png`, new Uint8Array(await preview.arrayBuffer()));
    console.log(`RENDERED=${sheetName}`);
  } catch (error) {
    console.log(`NO_RENDER=${sheetName}: ${error.message}`);
  }
}
