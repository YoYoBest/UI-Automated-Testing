import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const outputDir = "D:/Auto_Testing/UI-Smoke-Testing/outputs/20260814_js_add_001";
const outputPath = `${outputDir}/建设项目_个性化用例.xlsx`;

const input = await FileBlob.load(outputPath);
const workbook = await SpreadsheetFile.importXlsx(input);

const target = await workbook.inspect({
  kind: "table",
  sheetId: "建设项目",
  range: "A1:N3",
  include: "values,formulas",
  tableMaxRows: 3,
  tableMaxCols: 14,
  tableMaxCellChars: 180,
  maxChars: 8000,
});
console.log(target.ndjson);

const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  maxChars: 3000,
});
console.log(errors.ndjson);

for (const sheetName of ["新增项目", "项目决策", "建设项目"]) {
  const preview = await workbook.render({
    sheetName,
    autoCrop: "all",
    scale: 1,
    format: "png",
  });
  await fs.writeFile(
    `${outputDir}/${sheetName}-final-check.png`,
    new Uint8Array(await preview.arrayBuffer()),
  );
}
