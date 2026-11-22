import test from 'node:test';
import assert from 'node:assert/strict';
import { inspectOrFill, probeAccount, run, status } from '../easel/ego_workspace.mjs';

function fixture({href='https://x.com/compose/post', visible='', field='', controls=[], max=-1}={}) {
  globalThis.location = new URL(href);
  const box = {value:field,maxLength:max,getClientRects:()=>[1],closest:()=>null,matches:s=>s.startsWith('textarea'),getAttribute:()=>'',querySelector:()=>null};
  globalThis.document = {body:{innerText:visible},activeElement:box,querySelectorAll:()=>controls.map(innerText=>({innerText,getClientRects:()=>[1]})),execCommand(command,_,s){assert.equal(command,'insertText');box.value=s;return true;}};
  return box;
}
const opts = {hosts:['x.com'],platform:'twitter-x',action:'fill',text:'hello'};
const xUrls = ['https://x.com/home','https://x.com/compose/post'];

function space({ id = 9, pages, tabs, log, extra = {} }) {
  return { spaceId: id, pages: async () => pages, tabs: async () => tabs,
    handOff: async () => log.push(['handoff']), finish: async ({ keep }) => log.push(['finish', keep]), ...extra };
}

test('already logged in reuses the tab, keeps it for the user and does not steal focus',async()=>{
  const log=[];const p={label:'p1',url:async()=>'https://x.com/official',evaluate:async()=>'verified',cdp:async(method)=>{log.push([method]);},goto(){assert.fail('do not reload the login form or an existing session');}};
  globalThis.listTaskSpaces=async()=>[{id:9,name:'Easel',ownership:'agentDelegatedToUser'}];
  globalThis.takeOverTaskSpace=async()=>space({pages:[p],tabs:[{active:true,label:'p1'}],log});
  const r=await run({spaceName:'Easel',spaceId:9,platform:'twitter-x',action:'login',urls:xUrls});
  assert.equal(r.ok,true);assert.equal(r.state,'verified');assert.deepEqual(log,[['finish',['p1']]]);
});

test('login or captcha pages are brought forward and handed to the user',async()=>{
  for(const state of ['login_required','restricted']){
    const log=[];const p={label:'p1',url:async()=>'https://x.com/official',evaluate:async()=>state,cdp:async(method)=>{log.push([method]);},goto(){assert.fail('do not reload');}};
    globalThis.listTaskSpaces=async()=>[{id:9,name:'Easel',ownership:'agentDelegatedToUser'}];
    globalThis.takeOverTaskSpace=async()=>space({pages:[p],tabs:[{active:true,label:'p1'}],log});
    const r=await run({spaceName:'Easel',spaceId:9,platform:'twitter-x',action:state==='restricted'?'manage':'login',urls:xUrls});
    if(state==='restricted') assert.equal(r.ok,false); else assert.equal(r.state,'login_required');
    assert.deepEqual(log,[['Page.bringToFront'],['handoff']]);
  }
});

test('only inserts in an empty editor, without submitting',()=>{const box=fixture();assert.equal(inspectOrFill(opts),'filled');assert.equal(box.value,'hello');assert.equal(inspectOrFill(opts),'not_empty');});
test('rejects nonofficial URLs, login, risk, non-editor and size limit',()=>{
  for (const [args,status] of [[{href:'https://example.org/compose/post'},'other'],[{href:'https://x.com/i/flow/login'},'login_required'],[{visible:'300012'},'restricted'],[{href:'https://x.com/home'},'not_editor'],[{max:4},'too_long']]) {const box=fixture(args);assert.equal(inspectOrFill(opts),status);assert.equal(box.value,'');}
});
test('requires visible account control for positive verification',()=>{fixture();assert.equal(inspectOrFill({...opts,action:'verify'}),'unknown');fixture({controls:['Sign out']});assert.equal(inspectOrFill({...opts,action:'verify'}),'verified');});
test('no new space or navigation for verify before open',async()=>{globalThis.listTaskSpaces=async()=>[];assert.equal((await run({platform:'juejin',action:'verify',urls:['https://juejin.cn/','https://juejin.cn/editor/'],spaceName:'Easel'})).state,'unknown');});
test('no navigation or account switch after platform restriction; return control',async()=>{const log=[];const p={label:'p1',url:async()=>'https://x.com/home',evaluate:async()=>'restricted',cdp:async()=>{},goto(){assert.fail('must not navigate');}};globalThis.listTaskSpaces=async()=>[{id:5,name:'Easel',ownership:'agentDelegatedToUser'}];globalThis.takeOverTaskSpace=async()=>space({id:5,pages:[p],tabs:[{active:true,label:'p1'}],log});const r=await run({spaceId:5,spaceName:'Easel',platform:'twitter-x',action:'manage',urls:xUrls});assert.equal(r.ok,false);assert.deepEqual(log.at(-1),['handoff']);});

const juejinUrls = ['https://juejin.cn/creator', 'https://juejin.cn/editor/drafts/new?v=2'];
const zhihuUrls = ['https://www.zhihu.com/creator/analytics', 'https://zhuanlan.zhihu.com/write'];
function managedPage({ label = 'p1', url = 'https://juejin.cn/creator', verdicts = ['unknown', { state: 'unknown' }], log = [] } = {}) {
  let current = url;
  const queue = [...verdicts];
  return {
    label,
    url: async () => current,
    evaluate: async () => queue.shift(),
    cdp: async () => {},
    goto: async (target) => { log.push(['goto', label, target]); current = target; },
    waitForTimeout: async () => {},
    close: async () => log.push(['close', label]),
  };
}

test('a new space reuses its new-tab page instead of leaving a stray blank tab', async () => {
  const log = [];
  const p1 = managedPage({ url: 'chrome://newtab/', verdicts: ['unknown', { state: 'verified', name: '默笙呀' }], log });
  globalThis.listTaskSpaces = async () => [];
  globalThis.taskSpace = async (name) => {
    assert.equal(name, 'Easel');
    return space({ id: 13, pages: [p1], tabs: [{ label: 'p1', url: 'chrome://newtab/', active: true }], log,
      extra: { page: () => p1, newPage: async () => assert.fail('must reuse the new-tab page') } });
  };
  const r = await run({ platform: 'juejin', action: 'login', urls: juejinUrls, spaceName: 'Easel', spaceId: null });
  assert.deepEqual(log, [['goto', 'p1', juejinUrls[0]], ['finish', ['p1']]]);
  assert.equal(r.state, 'verified');
  assert.equal(r.name, '默笙呀');
  assert.equal(r.spaceId, 13);
});

test('a previously per-platform window is reused by id even if the name differs', async () => {
  const log = [];
  const p2 = managedPage({ label: 'p2', log });
  globalThis.listTaskSpaces = async () => [{ id: 21, name: 'Easel-juejin-abc', ownership: 'agentDelegatedToUser' }];
  globalThis.taskSpace = async () => assert.fail('must not create a duplicate space');
  globalThis.takeOverTaskSpace = async (id) => {
    log.push(['takeover', id]);
    return space({ id, pages: [p2], tabs: [{ label: 'p2', url: 'https://juejin.cn/creator', active: true }], log });
  };
  const r = await run({ platform: 'juejin', action: 'login', urls: juejinUrls, spaceName: 'Easel', spaceId: 21 });
  assert.equal(r.ok, true);
  assert.equal(r.spaceId, 21);
  assert.deepEqual(log[0], ['takeover', 21]);
});

test('a window kept for the user is claimed back and its own tabs re-adopted', async () => {
  const log = [];
  const official = managedPage({ label: 'p3', verdicts: ['unknown', { state: 'verified', name: 'demo' }], log });
  globalThis.listTaskSpaces = async () => [{ id: 4, name: 'Easel', ownership: 'user' }];
  globalThis.claimTaskSpace = async (id) => {
    log.push(['claim', id]);
    const pages = [];
    const kept = { url: 'https://juejin.cn/creator', active: true, openedBy: 'agent', page: 'kept' };
    const mine = { url: 'https://example.org/', active: false, openedBy: 'unknown', page: 'mine' };
    const task = space({ id, pages, log, tabs: [kept, mine],
      extra: { adopt: async (page) => { log.push(['adopt', page]); kept.label = 'p3'; pages.push(official); return official; } } });
    task.tabs = async () => [kept, mine];
    return task;
  };
  const r = await run({ platform: 'juejin', action: 'verify', urls: juejinUrls, spaceName: 'Easel', spaceId: 4 });
  assert.deepEqual(log.slice(0, 2), [['claim', 4], ['adopt', 'kept']]);
  assert.deepEqual([r.state, r.name], ['verified', 'demo']);
});

test('checking prefers the managed tab already on the platform', async () => {
  const log = [];
  const blank = managedPage({ label: 'p1', url: 'chrome://newtab/', verdicts: [] });
  const official = managedPage({ label: 'p2', verdicts: ['unknown', { state: 'verified', name: 'demo' }] });
  globalThis.listTaskSpaces = async () => [{ id: 4, name: 'Easel', ownership: 'agentDelegatedToUser' }];
  globalThis.takeOverTaskSpace = async () => space({ id: 4, pages: [blank, official], log,
    tabs: [{ label: 'p1', url: 'chrome://newtab/', active: true }, { label: 'p2', url: 'https://juejin.cn/creator', active: false }] });
  const r = await run({ platform: 'juejin', action: 'verify', urls: juejinUrls, spaceName: 'Easel', spaceId: 4 });
  assert.deepEqual([r.state, r.name], ['verified', 'demo']);
  assert.deepEqual(log, [['finish', ['p2']]]);
});

function probeFixture({ href = 'https://juejin.cn/creator', response, elements = {} } = {}) {
  globalThis.location = new URL(href);
  globalThis.document = { querySelectorAll: (selector) => elements[selector] ?? [] };
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push([url, init.credentials, init.method ?? 'GET']);
    if (response instanceof Error) throw response;
    return { status: response.status ?? 200, json: async () => response.body };
  };
  return calls;
}

test('platform current-user endpoints decide the login state with the page session', async () => {
  let calls = probeFixture({ response: { body: { err_no: 0, data: { user_name: ' 默笙呀 \n多余', user_id: '1' } } } });
  assert.deepEqual(await probeAccount({ platform: 'juejin' }), { state: 'verified', name: '默笙呀' });
  assert.deepEqual(calls[0].slice(1), ['include', 'GET']);
  probeFixture({ response: { body: { err_no: 403, data: null } } });
  assert.deepEqual(await probeAccount({ platform: 'juejin' }), { state: 'login_required' });
  probeFixture({ response: new Error('offline') });
  assert.deepEqual(await probeAccount({ platform: 'juejin' }), { state: 'unknown' });
  probeFixture({ href: 'https://member.bilibili.com/', response: { body: { code: -101, data: { isLogin: false } } } });
  assert.deepEqual(await probeAccount({ platform: 'bilibili' }), { state: 'login_required' });
  probeFixture({ href: 'https://www.zhihu.com/creator/analytics', response: { status: 401, body: { error: { code: 100 } } } });
  assert.deepEqual(await probeAccount({ platform: 'zhihu' }), { state: 'login_required' });
  calls = probeFixture({ href: 'https://cp.kuaishou.com/profile', response: { body: { result: 1, data: { userName: '快手号' } } } });
  assert.deepEqual(await probeAccount({ platform: 'kuaishou' }), { state: 'verified', name: '快手号' });
  assert.equal(calls[0][2], 'POST');
  probeFixture({ href: 'https://creator.douyin.com/creator-micro/home', response: { body: { status_code: 8 } } });
  assert.deepEqual(await probeAccount({ platform: 'douyin' }), { state: 'unknown' });
});

test('platforms without an endpoint need a visible logged-in-only control', async () => {
  const visible = (innerText) => ({ innerText, getClientRects: () => [1], getAttribute: () => '' });
  probeFixture({ href: 'https://x.com/home', elements: { "[data-testid='SideNav_AccountSwitcher_Button']": [visible('Easel\n@easel_ai')] } });
  assert.deepEqual(await probeAccount({ platform: 'twitter-x' }), { state: 'verified', name: '@easel_ai' });
  probeFixture({ href: 'https://x.com/home' });
  assert.deepEqual(await probeAccount({ platform: 'twitter-x' }), { state: 'unknown' });
  probeFixture({ href: 'https://mp.weixin.qq.com/cgi-bin/home?t=home/index&token=123' });
  assert.equal((await probeAccount({ platform: 'wechat-oa' })).state, 'verified');
  probeFixture({ href: 'https://channels.weixin.qq.com/platform', elements: { '.finder-ui-desktop-menu': [visible('首页')] } });
  assert.equal((await probeAccount({ platform: 'weixin-channels' })).state, 'verified');
  probeFixture({ href: 'https://channels.weixin.qq.com/platform' });
  assert.equal((await probeAccount({ platform: 'weixin-channels' })).state, 'unknown');
});

test('toutiao reads the creator media endpoint', async () => {
  probeFixture({ href: 'https://mp.toutiao.com/profile_v4/index', response: { body: { code: 0, data: { user: { screen_name: '头条号' }, media: { name: 'x' } } } } });
  assert.deepEqual(await probeAccount({ platform: 'toutiao' }), { state: 'verified', name: '头条号' });
  probeFixture({ href: 'https://mp.toutiao.com/profile_v4/index', response: { status: 401, body: { code: 10001 } } });
  assert.deepEqual(await probeAccount({ platform: 'toutiao' }), { state: 'login_required' });
});

test('an account answer from the platform outranks a passing login route', async () => {
  const log = [];
  const probe = managedPage({ label: 'p2', url: 'about:blank', verdicts: ['login_required', { state: 'verified', name: '小红薯' }], log });
  globalThis.listTaskSpaces = async () => [{ id: 4, name: 'Easel', ownership: 'agentDelegatedToUser' }];
  globalThis.takeOverTaskSpace = async () => space({ id: 4, pages: [], tabs: [], log, extra: { newPage: async () => probe } });
  const r = await status({ spaceName: 'Easel', spaceId: 4, platforms: { xiaohongshu: { urls: ['https://creator.xiaohongshu.com/'] } } });
  assert.deepEqual(r.platforms.xiaohongshu, { exists: false, state: 'verified', name: '小红薯' });
});

test('status checks platforms without a tab in one temporary tab and closes it', async () => {
  const log = [];
  const official = managedPage({ label: 'p1', verdicts: ['unknown', { state: 'verified', name: 'demo' }], log });
  const probe = managedPage({ label: 'p2', url: 'about:blank', verdicts: ['unknown', { state: 'verified', name: '知乎号' }], log });
  globalThis.listTaskSpaces = async () => [{ id: 4, name: 'Easel', ownership: 'agentDelegatedToUser' }];
  globalThis.taskSpace = async () => assert.fail('status must reuse the shared window');
  globalThis.takeOverTaskSpace = async () => space({ id: 4, pages: [official], log,
    tabs: [{ label: 'p1', url: 'https://juejin.cn/creator', active: true }],
    extra: { newPage: async () => probe } });
  const r = await status({ spaceName: 'Easel', spaceId: 4, platforms: { juejin: { urls: juejinUrls }, zhihu: { urls: zhihuUrls } } });
  assert.deepEqual(r.platforms, {
    juejin: { exists: true, state: 'verified', name: 'demo' },
    zhihu: { exists: false, state: 'verified', name: '知乎号' },
  });
  assert.deepEqual(log, [['goto', 'p2', zhihuUrls[0]], ['close', 'p2'], ['finish', ['p1']]]);
});

test('status closes old per-platform windows; logins stay in the browser profile', async () => {
  const log = [];
  const stillLoggedOut = ['login_required', { state: 'login_required' }];
  const probe = managedPage({ label: 'p1', url: 'chrome://newtab/', verdicts: [...stillLoggedOut, ...stillLoggedOut, ...stillLoggedOut], log });
  globalThis.listTaskSpaces = async () => [
    { id: 0, name: '', ownership: 'user' },
    { id: 6, name: 'Easel-bilibili-c3fbf6a8958a', ownership: 'agentDelegatedToUser' },
  ];
  globalThis.takeOverTaskSpace = async (id) => space({ id, pages: [], tabs: [], log: { push: (e) => log.push([...e, id]) } });
  globalThis.claimTaskSpace = async () => assert.fail('never touch the user default space');
  globalThis.taskSpace = async (name) => {
    assert.equal(name, 'Easel');
    return space({ id: 30, pages: [probe], tabs: [{ label: 'p1', url: 'chrome://newtab/', active: true }], log });
  };
  const r = await status({ spaceName: 'Easel', spaceId: null, platforms: { bilibili: { urls: ['https://member.bilibili.com/platform/upload-manager/article'] } } });
  assert.deepEqual(log[0], ['finish', [], 6]);
  assert.deepEqual(r.platforms.bilibili, { exists: false, state: 'login_required' });
  assert.equal(r.spaceId, 30);
});

test('a second platform opens as a new tab in the same window', async () => {
  const log = [];
  const xhs = managedPage({ label: 'p1', url: 'https://creator.xiaohongshu.com/', verdicts: ['other'], log });
  const next = managedPage({ label: 'p2', url: 'about:blank', verdicts: ['unknown', { state: 'verified', name: 'demo' }], log });
  globalThis.listTaskSpaces = async () => [{ id: 4, name: 'Easel', ownership: 'agentDelegatedToUser' }];
  globalThis.takeOverTaskSpace = async () => space({ id: 4, pages: [xhs, next], log,
    tabs: [{ label: 'p1', url: 'https://creator.xiaohongshu.com/', active: true }],
    extra: { newPage: async () => next } });
  const r = await run({ platform: 'juejin', action: 'login', urls: juejinUrls, spaceName: 'Easel', spaceId: 4 });
  assert.equal(r.spaceId, 4);
  assert.equal(r.state, 'verified');
  assert.deepEqual(log, [['goto', 'p2', juejinUrls[0]], ['finish', ['p1', 'p2']]]);
});
