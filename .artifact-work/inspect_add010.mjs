import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const path = "tests/Common_Test_Cases/公共用例_全页签标准化版.xlsx";
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(path));
const result = await workbook.inspect({
  kind: "match",
  searchTerm: "ADD-010",
  options: { maxResults: 20 },
  maxChars: 8000,
});
console.log(result.ndjson);
const sheet = workbook.worksheets.getItem("新增");
console.log(JSON.stringify({ headers: sheet.getRange("A1:K1").values, row: sheet.getRange("A11:K11").values }));
