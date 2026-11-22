// node contracts/build.mjs  -> contracts/artifacts/*.json  (needs `npm i solc@0.8.28`)
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
const require = createRequire(process.env.SOLC_PATH ? path.join(process.env.SOLC_PATH, "x.js") : import.meta.url);
const solc = require("solc");
const dir = path.dirname(new URL(import.meta.url).pathname);
const sources = Object.fromEntries(fs.readdirSync(path.join(dir, "src")).filter(f => f.endsWith(".sol"))
  .map(f => [f, { content: fs.readFileSync(path.join(dir, "src", f), "utf8") }]));
const input = { language: "Solidity", sources, settings: { optimizer: { enabled: true, runs: 200 }, evmVersion: "cancun",
  outputSelection: { "*": { "*": ["abi", "evm.bytecode.object"] } } } };
const out = JSON.parse(solc.compile(JSON.stringify(input)));
const errs = (out.errors || []).filter(e => e.severity === "error");
if (errs.length) { console.error(errs.map(e => e.formattedMessage).join("\n")); process.exit(1); }
for (const [file, cs] of Object.entries(out.contracts)) for (const [name, c] of Object.entries(cs)) {
  if (!c.evm.bytecode.object) continue; // interfaces have no bytecode
  fs.writeFileSync(path.join(dir, "artifacts", `${name}.json`),
    JSON.stringify({ contractName: name, compiler: solc.version(), abi: c.abi, bytecode: "0x" + c.evm.bytecode.object }, null, 2));
  console.log("built", name);
}
