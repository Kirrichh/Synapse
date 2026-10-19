import { readFile, mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
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
const command = JSON.parse(await readFile(new URL("../commands/binding.schema.json", root), "utf8"));
const reconciliation = JSON.parse(await readFile(new URL("../commands/reconciliation.schema.json", root), "utf8"));
const domain = JSON.parse(await readFile(new URL("../machine/schemas/domain.schema.json", root), "utf8"));
const application = JSON.parse(await readFile(new URL("contracts/application-delivery.schema.json", root), "utf8"));
const output = new URL("src/contracts/generated/", root);
await mkdir(output, { recursive: true });
await writeFile(new URL("application-errors.ts", output), `// Generated from the product application delivery schema.\nexport const applicationErrors = ${JSON.stringify(application.$defs.errorCode.enum)} as const;\n`);
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
// Only UUID is required. The owner schemas bound it to exactly 36 characters.
addFormats(ajv, ["uuid"]);
ajv.addSchema(envelope, "envelope");
ajv.addSchema(bootstrap, "bootstrap");
ajv.addSchema(command, "command");
ajv.addSchema(reconciliation, "reconciliation");
ajv.addSchema(domain, "domain");
ajv.addSchema(application, "application");
await build({
  stdin: {
    contents: standalone(ajv, {
      validateEnvelope: "envelope",
      validateBootstrap: "bootstrap",
      validateCommand: "command",
      validateReconciliation: "reconciliation",
      validateApplicationDelivery: "application",
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
  ["CommandMutation", command],
  ["CommandReconciliation", reconciliation],
  ["ApplicationDelivery", application],
]) {
  await writeFile(
    new URL(`${name}.d.ts`, output),
    await compile({ ...schema, title: name }, name, {
      bannerComment:
        "/* Generated from the product JSON Schema. Do not edit. */",
      $refOptions: { resolve: { file: false, http: false, product: {
        order: 1,
        canRead: (file) => [domain, command, reconciliation].some((item) => item.$id === file.url),
        read: (file) => {
          const schema = [domain, command, reconciliation].find((item) => item.$id === file.url);
          if (!schema) throw new Error("Unknown product schema reference");
          return schema;
        },
      } } },
    }),
  );
}
console.log(`Generated product contracts in ${fileURLToPath(output)}`);
