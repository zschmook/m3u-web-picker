"use strict";

const fs = require("node:fs");
const path = require("node:path");
const {spawnSync} = require("node:child_process");

const root = path.resolve(__dirname, "..");
// This separate browser fixture requires Playwright and a local Chrome install.
// The portable suite covers its VPN state handling without those dependencies.
const browserOnly = new Set(["vpn_settings.test.cjs"]);
const files = fs.readdirSync(path.join(root, "tests"))
  .filter(name => name.endsWith(".test.cjs") && !browserOnly.has(name))
  .sort()
  .map(name => path.join("tests", name));
const result = spawnSync(process.execPath, ["--test", ...files], {cwd: root, stdio: "inherit"});
if (result.error) throw result.error;
process.exitCode = result.status ?? 1;
