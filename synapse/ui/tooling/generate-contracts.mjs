import { readFile, mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import Ajv2020 from "ajv/dist/2020.js";
import standalone from "ajv/dist/standalone/index.js";
import { compile } from "json-schema-to-typescript";
import { build } from "esbuild";

const root = new URL("../", import.meta.url);
const envelope = JSON.parse(
  await readFile(
    new URL("../machine/schemas/envelope.schema.json", root),
    "utf8",
  ),
);
const bootstrap = JSON.parse(
  await readFile(new URL("contracts/bootstrap.schema.json", root), "utf8"),
);
const output = new URL("src/contracts/generated/", root);
await mkdir(output, { recursive: true });
await writeFile(new URL("diagnostics.ts", output), `// Generated from the product bootstrap schema.\nexport const diagnosticCodes = ${JSON.stringify(bootstrap.$defs.diagnosticCode.enum)} as const;\n`);
const profile = await readFile(new URL("../machine/release_profile.json", root), "utf8");
await writeFile(new URL("profile.ts", output), `// Generated from the product release profile.\nexport const profile = ${profile.trim()} as const;\n`);
const ajv = new Ajv2020({
  strict: true,
  allErrors: false,
  validateFormats: true,
  coerceTypes: false,
  removeAdditional: false,
  useDefaults: false,
  code: { source: true, esm: true },
});
ajv.addSchema(envelope, "envelope");
ajv.addSchema(bootstrap, "bootstrap");
await build({
  stdin: {
    contents: standalone(ajv, {
      validateEnvelope: "envelope",
      validateBootstrap: "bootstrap",
    }),
    resolveDir: fileURLToPath(root),
  },
  bundle: true,
  format: "esm",
  platform: "browser",
  outfile: fileURLToPath(new URL("validators.js", output)),
});
for (const [name, schema] of [
  ["MachineEnvelope", envelope],
  ["BootstrapSnapshot", bootstrap],
]) {
  await writeFile(
    new URL(`${name}.d.ts`, output),
    await compile(schema, name, {
      bannerComment:
        "/* Generated from the product JSON Schema. Do not edit. */",
    }),
  );
}
console.log(`Generated product contracts in ${fileURLToPath(output)}`);
