import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const jsx = require('react/jsx-runtime');
function moduleAt(relative, deps, globals = {}) {
  const source = readFileSync(new URL(`../../web/frontend/src/${relative}`, import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText;
  const exports = {};
  runInNewContext(code, { exports, Error, require: key => deps[key], ...globals });
  return exports;
}
function all(node, predicate) {
  if (Array.isArray(node)) return node.flatMap(v => all(v, predicate));
  if (!node || typeof node !== 'object') return [];
  return [...(predicate(node) ? [node] : []), ...all(node.props.children, predicate)];
}
function setup() {
  let status = { enabled: true, platforms: ['bilibili', 'zhihu'] }, error = '';
  let verified = 'verified', failure = false, cursor = 0, accounts = {};
  const calls = [], states = [], effects = [];
  const api = moduleAt('lib/api.ts', { './personas': {} }, {
    window: { location: { pathname: '/studio/index.html' } },
    fetch: async (url, options) => {
      calls.push([url, options.method ?? 'GET', options.body && JSON.parse(options.body)]);
      return { ok: !failure, status: 503, json: async () => failure ? { detail: 'Ego 未连接' }
        : url.endsWith('/status') ? status : url.endsWith('/accounts') ? { platforms: accounts }
        : url.endsWith('/verify') ? { state: verified, message: '页面核验结果' } : { ok: true, state: verified, name: '小明', message: '已打开页面' } };
    },
  });
  const legacy = () => assert.fail('未获明确授权就渲染了旧模式');
  const deps = {
    '../lib/api': api, 'react/jsx-runtime': jsx, './LegacyAccountsPage': legacy,
    '../lib/useEgoStatus': { useEgoStatus: () => ({ status, error, retry() {} }) },
    react: {
      useState(initial) {
        const i = cursor++;
        if (!(i in states)) states[i] = initial;
        return [states[i], next => { states[i] = typeof next === 'function' ? next(states[i]) : next; }];
      },
      useEffect(effect) { effects.push(effect); },
    },
  };
  const { default: gate, EgoAccounts } = moduleAt('components/AccountsPage.tsx', deps);
  const render = (platforms = status.platforms) => { cursor = 0; effects.length = 0; return EgoAccounts({ platforms }); };
  const load = async () => { effects.at(-1)(); await new Promise(resolve => setTimeout(resolve, 0)); };
  const click = (card, label) => all(card, n => n.type === 'button' && n.props.children === label)[0].props.onClick();
  const cards = root => all(root, n => n.props.className === 'card account-card');
  return { api, calls, gate, render, load, click, cards, legacy, EgoAccounts, setMode(v, e = '') { status = v; error = e; },
    setResult(v) { verified = v; }, setAccounts(v) { accounts = v; }, fail() { failure = true; } };
}

test('浏览器模式仅在后端显式关闭 Ego 时启用旧账号页', async () => {
  const s = setup();
  const status = await s.api.fetchEgoStatus();
  assert.deepEqual(s.calls, [['/studio/api/ego/status', 'GET', undefined]]);
  assert.equal(s.gate().type, s.EgoAccounts);
  assert.deepEqual(s.gate().props.platforms, status.platforms);
  s.setMode({ enabled: false, platforms: [] });
  assert.equal(s.gate().type, s.legacy);
  s.setMode(null, '无法读取浏览器模式');
  assert.notEqual(s.gate().type, s.legacy);
  assert.ok(all(s.gate(), n => n.props.role === 'alert').length);
  s.setMode({ enabled: 'false', platforms: [] });
  await assert.rejects(s.api.fetchEgoStatus(), /无效/);
});

test('十个平台都只需一个入口，每次点击仅打开对应 Ego 页面', async () => {
  const s = setup(), ids = s.api.EGO_PLATFORMS.map(p => p.platform);
  s.setMode({ enabled: true, platforms: ids });
  assert.equal(s.cards(s.render()).length, 10);
  assert.equal(s.calls.length, 0);
  for (const [i, platform] of ids.entries()) {
    const card = s.cards(s.render())[i];
    assert.equal(all(card, n => n.type === 'button').length, 1);
    await s.click(card, '打开 Ego');
    assert.deepEqual(s.calls.at(-1), ['/studio/api/ego/open', 'POST', { platform, action:'login' }]);
    const updated = JSON.stringify(s.cards(s.render())[i]);
    assert.match(updated, /已登录 · 小明/);
    assert.match(updated, /进入 Ego/);
  }
  assert.equal(s.calls.length, 10);
});

test('已登录的平台进入页面就显示登录状态，不需要再点一遍', async () => {
  const s = setup();
  s.setAccounts({ bilibili: { exists: false, state: 'verified', name: '小明' }, zhihu: { exists: false, state: 'login_required' } });
  s.render();
  await s.load();
  assert.deepEqual(s.calls, [['/studio/api/ego/accounts', 'POST', undefined]]);
  const [bilibili, zhihu] = s.cards(s.render()).map(card => JSON.stringify(card));
  assert.match(bilibili, /已登录 · 小明/);
  assert.match(bilibili, /进入 Ego/);
  assert.match(zhihu, /未登录/);
  assert.match(zhihu, /去登录/);
});

test('未登录与读取失败如实显示，打开失败给出原因', async () => {
  const s = setup();
  s.setAccounts({ bilibili: { exists: true, state: 'login_required' }, zhihu: { exists: true, state: 'unknown' } });
  s.render();
  await s.load();
  const [bilibili, zhihu] = s.cards(s.render()).map(card => JSON.stringify(card));
  assert.match(bilibili, /未登录/);
  assert.doesNotMatch(bilibili, /已登录/);
  assert.match(zhihu, /状态未知/);
  assert.match(zhihu, /打开 Ego/);
  s.fail();
  await s.click(s.cards(s.render())[0], '去登录');
  assert.match(JSON.stringify(all(s.render(), n => n.props.role === 'alert')), /无法打开官方页面：Ego 未连接/);
  const fresh = setup();
  fresh.fail();
  fresh.render();
  await fresh.load();
  assert.match(JSON.stringify(all(fresh.render(), n => n.props.role === 'alert')), /无法读取登录状态：Ego 未连接/);
});
