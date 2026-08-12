import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const projectRoot = path.resolve("..");
const inputPath = path.join(projectRoot, "tests/Common_Test_Cases/公共用例_宋佳慧_新增页签优化版.xlsx");
const previewDir = path.join(projectRoot, ".artifact-work/previews-before");
await fs.mkdir(previewDir, { recursive: true });

const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));
const sheets = workbook.worksheets.items;
const summary = [];
for (const sheet of sheets) {
  const used = sheet.getUsedRange();
  const address = used?.address || "";
  const values = used?.values || [];
  summary.push({
    name: sheet.name,
    address,
    rowCount: values.length,
    colCount: Math.max(0, ...values.map((row) => row.length)),
    firstRows: values.slice(0, 8),
  });
  const preview = await workbook.render({
    sheetName: sheet.name,
    autoCrop: "all",
    scale: 1,
    format: "png",
  });
  const safe = sheet.name.replace(/[<>:"/\\|?*]/g, "_");
  await fs.writeFile(
    path.join(previewDir, `${safe}.png`),
    new Uint8Array(await preview.arrayBuffer()),
  );
}

const inspection = await workbook.inspect({
  kind: "workbook,sheet,table",
  maxChars: 12000,
  tableMaxRows: 8,
  tableMaxCols: 16,
  tableMaxCellChars: 120,
});
await fs.writeFile(
  path.join(projectRoot, ".artifact-work/workbook-summary.json"),
  JSON.stringify(summary, null, 2),
  "utf8",
);
await fs.writeFile(
  path.join(projectRoot, ".artifact-work/workbook-inspect.ndjson"),
  inspection.ndjson,
  "utf8",
);
console.log(JSON.stringify(summary, null, 2));
