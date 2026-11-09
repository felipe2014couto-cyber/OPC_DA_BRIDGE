/* Real browser laboratory test. Own temporary backend/data only; no OPC agents. */
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const { spawn } = require('node:child_process');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'opc-web-browser-'));
const data = path.join(directory, 'data');
let processServer, browser, base;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function api(route, options) {
  const response = await fetch(base + '/api/v1/' + route, options);
  const result = await response.json();
  if (!response.ok) throw new Error(JSON.stringify(result));
  return result;
}
function flatten(state) {
  const result = { connections: [], machines: [], modules: [], folders: [], variables: [] };
  for (const c of state.connections) { result.connections.push(c);
    for (const m of c.machines) { result.machines.push(m);
      for (const s of m.modules) { result.modules.push(s);
        for (const f of s.folders) { result.folders.push(f); result.variables.push(...f.variables); }
      }
    }
  }
  return result;
}
async function start(port) {
  processServer = spawn(process.env.PYTHON || 'python3', ['-m', 'opc_da_bridge.management', '--data-dir', data, '--port', String(port)], {
    cwd: root, env: { ...process.env, PYTHONPATH: path.join(root, 'src'), PYTHONDONTWRITEBYTECODE: '1' }, stdio: ['ignore', 'pipe', 'pipe'],
  });
  processServer.stdout.on('data', bytes => fs.appendFileSync(path.join(directory, 'backend.log'), bytes));
  processServer.stderr.on('data', bytes => fs.appendFileSync(path.join(directory, 'backend.log'), bytes));
  for (let i = 0; i < 80; i++) {
    if (processServer.exitCode !== null) throw new Error('Backend exited: ' + fs.readFileSync(path.join(directory, 'backend.log'), 'utf8'));
    try { await api('health'); return; } catch { await sleep(100); }
  }
  throw new Error('Backend did not start.');
}
async function stop() {
  if (!processServer || processServer.exitCode !== null) return;
  const own = processServer;
  const exited = new Promise(resolve => own.once('exit', resolve));
  own.kill('SIGINT');
  await Promise.race([exited, sleep(6000)]);
  if (own.exitCode === null && own.signalCode === null) { own.kill('SIGKILL'); await exited; }
  processServer = null;
}
async function main() {
  const port = await new Promise((resolve, reject) => {
    const probe = net.createServer(); probe.once('error', reject);
    probe.listen(0, '127.0.0.1', () => { const port = probe.address().port; probe.close(() => resolve(port)); });
  });
  base = 'http://127.0.0.1:' + port;
  await start(port);
  assert.equal((await api('state')).connections.length, 0);
  browser = await chromium.launch({ headless: true, executablePath: process.env.OPC_BROWSER_EXECUTABLE || undefined });
  console.log('BROWSER_VERSION=' + browser.version());
  const page = await browser.newPage({ viewport: { width: 1550, height: 950 }, locale: 'pt-BR', timezoneId: 'America/Sao_Paulo' });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => route.request().url().startsWith(base + '/') ? route.continue() : route.abort());
  await page.goto(base, { waitUntil: 'networkidle' });
  const test = id => page.getByTestId(id);
  async function field(name, value) {
    const element = test('field-' + name);
    if (await element.evaluate(e => e.tagName) === 'SELECT') await element.selectOption(String(value));
    else await element.fill(String(value));
  }
  async function save() { await test('dialog-save').click(); await page.getByRole('dialog').waitFor({ state: 'hidden' }); }
  async function create(kind, fields) {
    await test('new-' + kind).click();
    for (const [name, value] of Object.entries(fields)) await field(name, value);
    await save();
  }
  await create('connection', { name: 'Laboratório simulado', description: 'Configuração local; nenhum agente Windows conectado.' });
  await create('machine', { name: 'Destino de laboratório', host: 'destino-lab.invalid' });
  await create('module', { name: 'Módulo 1', source_kind: 'simulated' });
  await create('folder', { name: 'Processo' });
  await create('variable', { name: 'Pressao', data_type: 'Double', initial_value: '6,25', source_id: 'Simulado.Pressao', description: 'Pressão simulada do processo (bar)' });
  await create('variable', { name: 'StatusBomba', data_type: 'Boolean', initial_value: 'true', source_id: 'Simulado.StatusBomba', description: 'Estado simulado da bomba' });
  let state = await api('state'), nodes = flatten(state);
  assert.equal(nodes.variables.length, 2);
  assert.equal(nodes.variables.find(v => v.name === 'Pressao').initial_value, 6.25);
  assert.equal(nodes.variables.find(v => v.name === 'StatusBomba').initial_value, true);
  assert.equal(state.applied_to_windows, false);
  console.log('BROWSER_CREATE_FULL_HIERARCHY_PASS revision=' + state.revision);

  const numeric = nodes.variables.find(v => v.name === 'Pressao');
  const boolean = nodes.variables.find(v => v.name === 'StatusBomba');
  const row = id => test('variable-row-' + id);
  await row(numeric.id).click({ button: 'right' }); await test('context-read').click();
  await page.waitForFunction(id => /6[,.]25/.test(document.querySelector('[data-testid="variable-row-' + id + '"]').textContent), numeric.id);
  let samplePolls = 0;
  page.on('response', response => { if (response.url().includes('/api/v1/samples?')) samplePolls++; });
  await row(boolean.id).click({ button: 'right' }); await test('context-monitor').click();
  await page.waitForFunction(id => /Boa/.test(document.querySelector('[data-testid="variable-row-' + id + '"]').textContent), boolean.id);
  await page.waitForTimeout(2200); assert(samplePolls >= 2, 'monitor must actually poll the backend');
  await api('simulator/availability', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ available: false }) });
  await page.waitForFunction(id => /Ruim/.test(document.querySelector('[data-testid="variable-row-' + id + '"]').textContent), boolean.id);
  await api('simulator/availability', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ available: true }) });
  await page.waitForFunction(id => /Boa/.test(document.querySelector('[data-testid="variable-row-' + id + '"]').textContent), boolean.id);
  console.log('BROWSER_READ_MONITOR_GOOD_BAD_GOOD_PASS polls=' + samplePolls);
  await test('disconnect').click();

  await row(numeric.id).click({ button: 'right' }); await test('context-edit').click();
  const dialog = page.getByRole('dialog'), before = await dialog.boundingBox(), header = await test('dialog-header').boundingBox();
  await page.mouse.move(header.x + 70, header.y + 16); await page.mouse.down();
  await page.mouse.move(header.x + 150, header.y + 56, { steps: 8 }); await page.mouse.up();
  const after = await dialog.boundingBox(); assert(Math.abs(after.x - before.x) > 20, 'form must drag by title bar');
  await field('description', 'Descrição editada pelo teste visual'); await save();
  const revision = (await api('state')).revision;
  await test('new-variable').click(); await field('name', 'Pressao'); await field('data_type', 'Double'); await field('initial_value', '1');
  await test('dialog-save').click(); await test('dialog-error').waitFor({ state: 'visible' });
  assert.equal((await api('state')).revision, revision, 'duplicate must not change persisted config');
  await test('dialog-cancel').click();
  console.log('BROWSER_DRAG_EDIT_DUPLICATE_VALIDATION_PASS');

  state = await api('state'); const persisted = JSON.stringify(state.connections);
  await stop(); await start(port); await page.reload({ waitUntil: 'networkidle' });
  state = await api('state'); assert.equal(JSON.stringify(state.connections), persisted);
  nodes = flatten(state); await test('tree-node-' + nodes.folders[0].id).click();
  await row(numeric.id).waitFor({ state: 'visible' }); await row(boolean.id).waitFor({ state: 'visible' });
  console.log('BROWSER_BACKEND_RESTART_PERSISTENCE_PASS');

  await test('connect').click();
  await row(numeric.id).click({ button: 'right' }); await test('context-read').click();
  await page.waitForFunction(id => /6[,.]25/.test(document.querySelector('[data-testid="variable-row-' + id + '"]').textContent), numeric.id);
  await page.screenshot({ path: path.join(directory, 'configurator.png'), fullPage: true });
  console.log('SCREENSHOT=' + path.join(directory, 'configurator.png'));
  console.log('VISIBLE_NUMERIC_ROW=' + await row(numeric.id).innerText());

  // Every tree level is editable, and deletion requires the explicit dialog.
  for (const kind of ['folders', 'modules', 'machines', 'connections']) {
    nodes = flatten(await api('state')); const node = nodes[kind][0];
    await test('tree-node-' + node.id).click({ button: 'right' }); await test('context-edit').click();
    await field('name', node.name + ' editado'); await save();
    const newNodes = flatten(await api('state'));
    assert.equal(newNodes[kind][0].name, node.name + ' editado');
    assert.equal(newNodes.variables.find(v => v.id === numeric.id).item_id, numeric.item_id, 'planned ItemID stays stable when ancestors rename');
  }
  nodes = flatten(await api('state')); await test('tree-node-' + nodes.folders[0].id).click();
  for (const variable of nodes.variables) {
    await row(variable.id).click({ button: 'right' }); await test('context-delete').click();
    await test('dialog-save').click(); await page.getByRole('dialog').waitFor({ state: 'hidden' });
  }
  for (const kind of ['folders', 'modules', 'machines', 'connections']) {
    const node = flatten(await api('state'))[kind][0];
    await test('tree-node-' + node.id).click({ button: 'right' }); await test('context-delete').click();
    await test('dialog-save').click(); await page.getByRole('dialog').waitFor({ state: 'hidden' });
  }
  assert.equal((await api('state')).connections.length, 0);
  assert.deepEqual(errors, []);
  console.log('BROWSER_EDIT_DELETE_ALL_LEVELS_PASS');
  console.log('MANAGEMENT_BROWSER_PASS data_dir=' + data);
}
main().catch(error => { console.error(error); process.exitCode = 1; }).finally(async () => { if (browser) await browser.close(); await stop(); });
