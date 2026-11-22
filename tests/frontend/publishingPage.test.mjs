import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const platforms = ['xiaohongshu', 'douyin', 'kuaishou', 'weixin-channels', 'zhihu', 'juejin', 'toutiao', 'twitter-x', 'bilibili', 'wechat-oa'];
function compile(file, modules) {
  const source = readFileSync(new URL(`../../web/frontend/src/components/${file}.tsx`, import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
  } });
  const exports = {};
  runInNewContext(outputText, { exports, require(name) {
    assert.ok(name in modules, `不应依赖 ${name}`);
    return modules[name];
  } });
  return exports.default;
}

function nodes(tree) {
  if (Array.isArray(tree)) return tree.flatMap(nodes);
  if (!tree || typeof tree !== 'object') return [];
  return [tree, ...nodes(tree.props?.children)];
}

function setup(openEgoPage, fillEgoField, egoEnabled = true) {
  const state = [];
  let cursor = 0;
  const react = {
    useEffect() {}, useRef(value) { return { current: value }; },
    useState(initial) {
      const index = cursor++;
      if (!(index in state)) state[index] = initial;
      return [state[index], value => { state[index] = typeof value === 'function' ? value(state[index]) : value; }];
    },
  };
  const jsx = (type, props, key) => ({ type, props, key });
  const Page = compile('ManualPublishPage', {
    react, 'react/jsx-runtime': { jsx, jsxs: jsx }, './icons': {},
    '../lib/api': { openEgoPage, fillEgoField }, '../lib/sanitize': { renderMarkdown: text => text },
    '../lib/store': { loadPublishDraft: () => ({ title: '标题', body: '原稿', platforms, tags: '', overrides: {} }) },
  });
  const render = () => { cursor = 0; return nodes(Page({ persona: '', egoEnabled })); };
  const card = key => nodes(render().find(node => node.key === key && node.props.className?.includes('pv-card')));
  const button = key => card(key).find(node => node.type === 'button' && typeof node.props.children === 'string' && node.props.children.includes('Ego'));
  const approve = key => card(key).find(node => node.props.type === 'checkbox').props.onChange({ target: { checked: true } });
  return { render, card, button, approve };
}

test('Ego 只挂载人工页；关闭 Ego 时沿用管理员策略，包括自动页和人工切换', async () => {
  for (const automaticSubmission of [false, true]) {
    const state = []; let cursor = 0; let reads = 0;
    const jsx = (type, props) => ({ type, props });
    const Page = compile('PublishingPage', {
      './ManualPublishPage': { __esModule: true, default: 'manual' }, './PublishPage': { __esModule: true, default: 'automatic' },
      react: { useEffect(cb) { if (reads === 0) cb(); }, useState(initial) {
        const index = cursor++; if (!(index in state)) state[index] = initial;
        return [state[index], value => { state[index] = value; }];
      } }, 'react/jsx-runtime': { jsx, jsxs: jsx },
      '../lib/api': { fetchPublishingPolicy: async () => { reads++; return { mode: 'automated', automaticSubmission }; } },
    });
    const ego = Page({ persona: '', egoEnabled: true });
    assert.equal(ego.type, 'manual'); assert.equal(ego.props.egoEnabled, true); assert.equal(reads, 0);
    const legacy = Page({ persona: '', egoEnabled: false });
    const render = () => { cursor = 0; return nodes(legacy.type(legacy.props)); };
    render(); await new Promise(resolve => setImmediate(resolve));
    const tree = render();
    const automatic = tree.find(node => node.props.children === '自动发布');
    assert.equal(automatic.props.disabled, !automaticSubmission);
    assert.ok(tree.some(node => node.type === (automaticSubmission ? 'automatic' : 'manual')));
    tree.find(node => node.props.children === '人工审核发布').props.onClick();
    assert.equal(render().find(node => node.type === 'manual').props.egoEnabled, false);
  }
});

test('非 Ego 人工页仅在审核后提供官方链接，没有 Ego 填稿或打开按钮', () => {
  const app = setup(undefined, undefined, false);
  for (const key of platforms) {
    assert.equal(app.button(key), undefined);
    assert.ok(!app.card(key).some(node => node.type === 'a'));
    app.approve(key);
    assert.ok(app.card(key).some(node => node.type === 'a' && node.props.href.startsWith('https://')));
  }
});

test('只填当前审核版本的标题或带标签的正文，改稿后不得填入', async () => {
  const calls = [];
  const app = setup(undefined, async (...args) => { calls.push(args); return '已填入'; });
  const field = (label) => app.card('zhihu').find(node => node.type === 'button' && node.props.children === label);
  const change = (name, value) => app.render().find(node => node.props.id === name).props.onChange({ target: { value } });
  change('manual-tags', '测试，排版');
  assert.equal(field('填正文到 Ego').props.disabled, true);
  await field('填正文到 Ego').props.onClick();
  assert.equal(calls.length, 0);
  app.approve('zhihu');
  await field('填标题到 Ego').props.onClick(); await field('填正文到 Ego').props.onClick();
  assert.deepEqual(calls, [['zhihu', '标题'], ['zhihu', '原稿\n\n#测试 #排版']]);
  change('manual-body', '新稿');
  assert.equal(field('填正文到 Ego').props.disabled, true);
  await field('填正文到 Ego').props.onClick(); assert.equal(calls.length, 2);
});

test('十个平台分别审核后只能打开 Ego，不登记发布或调用自动接口', async () => {
  const calls = [];
  const app = setup(async (...args) => { calls.push(args); return { ok: true, message: '已打开' }; });
  for (const key of platforms) {
    assert.equal(app.button(key).props.disabled, true);
    await app.button(key).props.onClick();
    assert.equal(calls.some(([platform]) => platform === key), false);
    app.approve(key);
    assert.equal(app.button(key).props.disabled, false);
    await app.button(key).props.onClick();
    assert.deepEqual(calls.at(-1), [key, 'publish']);
    assert.ok(app.card(key).some(node => node.props.role === 'status' && node.props.children.includes('尚未上传或发布')));
  }
  assert.equal(calls.length, platforms.length);
  app.render().find(node => node.props.id === 'manual-title').props.onChange({ target: { value: '新标题' } });
  for (const key of platforms) assert.equal(app.button(key).props.disabled, true);
});

test('Ego 打开失败显示原因并允许重试，不回退到其他浏览器', async () => {
  let attempts = 0;
  const app = setup(async () => { attempts++; throw new Error('未连接'); });
  app.approve('zhihu');
  await app.button('zhihu').props.onClick();
  assert.ok(app.card('zhihu').some(node => node.props.role === 'alert'));
  assert.ok(!app.card('zhihu').some(node => node.type === 'a'));
  assert.equal(app.button('zhihu').props.disabled, false);
  await app.button('zhihu').props.onClick();
  assert.equal(attempts, 2);
});
