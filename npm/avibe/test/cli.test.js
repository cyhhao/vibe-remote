"use strict";

const assert = require("node:assert/strict");
const childProcess = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const cliPath = path.join(__dirname, "..", "bin", "avibe.js");

function makeTempEnv() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "avibe-cli-test-"));
  const bin = path.join(root, "bin");
  const home = path.join(root, "home");
  fs.mkdirSync(bin, { recursive: true });
  fs.mkdirSync(home, { recursive: true });

  return { root, bin, home };
}

function writeFakeVibe(bin, label) {
  const vibePath = path.join(bin, process.platform === "win32" ? "vibe.cmd" : "vibe");
  if (process.platform === "win32") {
    fs.writeFileSync(vibePath, `@echo off\necho ${label} %*\n`);
  } else {
    fs.writeFileSync(vibePath, `#!/bin/sh\necho ${label} "$@"\n`);
    fs.chmodSync(vibePath, 0o755);
  }
  return vibePath;
}

function runCli(args, envOverrides = {}) {
  const env = {
    ...process.env,
    ...envOverrides,
  };

  return childProcess.spawnSync(process.execPath, [cliPath, ...args], {
    encoding: "utf8",
    env,
  });
}

test("prints wrapper help without installing", () => {
  const temp = makeTempEnv();
  const result = runCli(["--help"], {
    HOME: temp.home,
    USERPROFILE: temp.home,
    PATH: temp.bin,
    AVIBE_INSTALL_COMMAND: "exit 42",
  });

  assert.equal(result.status, 0);
  assert.match(result.stdout, /npx avibe/);
  assert.match(result.stdout, /npm install -g avibe/);
});

test("delegates commands to an existing vibe binary", () => {
  const temp = makeTempEnv();
  writeFakeVibe(temp.bin, "existing-vibe");

  const result = runCli(["status", "--json"], {
    HOME: temp.home,
    USERPROFILE: temp.home,
    PATH: temp.bin,
  });

  assert.equal(result.status, 0);
  assert.match(result.stdout, /existing-vibe status --json/);
});

test("installs vibe when missing, then delegates", () => {
  const temp = makeTempEnv();
  const installCommand =
    process.platform === "win32"
      ? `echo @echo off> "${path.join(temp.bin, "vibe.cmd")}" && echo echo installed-vibe %*>> "${path.join(temp.bin, "vibe.cmd")}"`
      : `/bin/mkdir -p "${temp.bin}" && /usr/bin/printf '#!/bin/sh\\necho installed-vibe "$@"\\n' > "${path.join(
          temp.bin,
          "vibe"
        )}" && /bin/chmod +x "${path.join(temp.bin, "vibe")}"`;

  const result = runCli(["doctor"], {
    HOME: temp.home,
    USERPROFILE: temp.home,
    PATH: temp.bin,
    AVIBE_INSTALL_COMMAND: installCommand,
  });

  assert.equal(result.status, 0);
  assert.match(result.stdout, /Installing Vibe Remote/);
  assert.match(result.stdout, /installed-vibe doctor/);
});

test("maps init and start to the default vibe command", () => {
  const temp = makeTempEnv();
  writeFakeVibe(temp.bin, "default-vibe");

  const init = runCli(["init"], {
    HOME: temp.home,
    USERPROFILE: temp.home,
    PATH: temp.bin,
  });
  const start = runCli(["start"], {
    HOME: temp.home,
    USERPROFILE: temp.home,
    PATH: temp.bin,
  });

  assert.equal(init.status, 0);
  assert.equal(start.status, 0);
  assert.match(init.stdout, /default-vibe\s*$/m);
  assert.match(start.stdout, /default-vibe\s*$/m);
});
