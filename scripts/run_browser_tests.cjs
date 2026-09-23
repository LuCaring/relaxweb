#!/usr/bin/env node
const fs = require('node:fs');
const net = require('node:net');
const path = require('node:path');
const { spawn, spawnSync } = require('node:child_process');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const python = process.env.PYTHON || path.join(root, '.venv', 'bin', 'python');
const chrome = process.env.CHROME_PATH || chromium.executablePath();

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function main() {
  if (!process.env.PYTHON && !fs.existsSync(python)) throw new Error('找不到 Python；请先运行 uv sync --locked，或设置 PYTHON。');
  if (!chrome || !fs.existsSync(chrome)) throw new Error('找不到浏览器；请运行 npx playwright install chromium，或设置 CHROME_PATH。');
  await new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.once('error', () => reject(new Error('8000 端口已占用；请先停止现有服务。')));
    probe.listen(8000, '0.0.0.0', () => probe.close(resolve));
  });

  const requested = process.argv.slice(2);
  const tests = requested.length ? requested : fs.readdirSync(path.join(root, 'tests'))
    .filter(name => name.endsWith('.cjs'))
    .filter(name => /require\(['"]playwright(?:-core)?['"]\)/.test(
      fs.readFileSync(path.join(root, 'tests', name), 'utf8')))
    .map(name => path.join('tests', name));
  const env = { ...process.env, CHROME_PATH: chrome, PYTHON: python, LIVE_WEB_PORT: '8000' };
  const server = spawn(python, ['deploy/serve.py'], { cwd: root, env, stdio: 'ignore' });
  try {
    let ready = false;
    for (let attempt = 0; attempt < 50; attempt++) {
      if (server.exitCode !== null) throw new Error('静态网页服务启动失败；请确认 8000 端口可用。');
      try {
        const response = await fetch('http://127.0.0.1:8000/');
        if (response.ok) { ready = true; break; }
      } catch { /* 等待服务监听 */ }
      await sleep(100);
    }
    if (!ready) throw new Error('等待静态网页服务超时。');

    const failed = [];
    for (const test of tests) {
      console.log(`\n== ${test} ==`);
      const result = spawnSync(process.execPath, [test], { cwd: root, env, stdio: 'inherit' });
      if (result.status !== 0) failed.push(test);
    }
    console.log(`\n浏览器测试：${tests.length - failed.length}/${tests.length} 通过`);
    if (failed.length) {
      console.error(`失败：${failed.join(', ')}`);
      process.exitCode = 1;
    }
  } finally {
    server.kill('SIGTERM');
  }
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
