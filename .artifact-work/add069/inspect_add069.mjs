import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const path = "D:/Auto_Testing/UI-Smoke-Testing/tests/Common_Test_Cases/公共用例_UI自动化.xlsx";
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(path));
const sheets = await workbook.inspect({ kind: "sheet", include: "id,name", maxChars: 4000 });
console.log(sheets.ndjson);
const matches = await workbook.inspect({
  kind: "match",
  searchTerm: "ADD-069",
  options: { useRegex: false, maxResults: 20 },
  maxChars: 8000,
});
console.log(matches.ndjson);
const row = await workbook.inspect({
  kind: "table",
  sheetId: "新增",
  range: "A69:N71",
  include: "values,formulas",
  tableMaxRows: 5,
  tableMaxCols: 14,
  maxChars: 8000,
});
console.log(row.ndjson);
