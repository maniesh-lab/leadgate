// Makes `src/api/schema.d.ts` from the API's OpenAPI document, or checks that it's up to date.
//
//   node scripts/api-types.mjs          (npm run gen:api)   write the file
//   node scripts/api-types.mjs --check  (npm run check:api) fail if the file is out of date
//
// The document comes from `backend/scripts/export_openapi.py`, so no API server is needed.
// Python is $PYTHON, else the repo's `.venv`, else `python` on the PATH (CI).
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontend = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repo = resolve(frontend, "..");
const schemaFile = join(frontend, "src", "api", "schema.d.ts");
const venvPython = join(repo, ".venv", "bin", "python");
const python = process.env.PYTHON ?? (existsSync(venvPython) ? venvPython : "python");
const check = process.argv.includes("--check");

const work = mkdtempSync(join(tmpdir(), "leadgate-api-"));
try {
  const document = execFileSync(python, [join(repo, "backend", "scripts", "export_openapi.py")], {
    cwd: join(repo, "backend"),
    encoding: "utf8",
  });
  const documentFile = join(work, "openapi.json");
  writeFileSync(documentFile, document);
  const output = check ? join(work, "schema.d.ts") : schemaFile;
  // Pinned, and run through npx rather than installed: it wants TypeScript 5 (PROGRESS #143).
  execFileSync("npx", ["--yes", "openapi-typescript@7.13.0", documentFile, "-o", output], {
    stdio: ["ignore", "ignore", "inherit"],
  });
  if (check && readFileSync(output, "utf8") !== readFileSync(schemaFile, "utf8")) {
    console.error(
      "src/api/schema.d.ts doesn't match the API: run `npm run gen:api` and commit it.",
    );
    process.exitCode = 1;
  } else {
    console.log(check ? "src/api/schema.d.ts matches the API." : "wrote src/api/schema.d.ts");
  }
} finally {
  rmSync(work, { recursive: true, force: true });
}
