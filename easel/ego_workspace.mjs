// Ego Node runtime entry. Called only by a local user action; never submits a form.
// All platforms share one workbench space (one window, one tab each).
// Never claim a random user tab or read cookies.
const failure = message => ({ ok: false, message });
const unknown = message => ({ ok: true, state: 'unknown', message });
// A new Ego space opens on the browser's new-tab page rather than about:blank.
const BLANK = /^(|about:blank|chrome:\/\/(newtab|new-tab-page)\/?)$/;

// Self-contained function executed in the official page. Return only state, never page text.
export function inspectOrFill({ hosts, action, text, platform }) {
  if (location.protocol !== 'https:' || !hosts.includes(location.hostname)) return 'other';
  const visible = document.body?.innerText || '';
  if (/\b300012\b|IP存在风险|访问受限|异常访问|请完成.{0,8}验证|安全验证|Verify you are human|access denied/i.test(visible) || /[?&]error_code=300012\b/.test(location.search)) return 'restricted';
  if (/\/(login|signin|passport)(\/|$)/i.test(location.pathname) || /扫码登录|登录以继续|Log in to X|Sign in to X/i.test(visible)) return 'login_required';
  if (action === 'fill') {
    const routes = {xiaohongshu:/^\/publish\//,douyin:/^\/creator-micro\/content\/(upload|post|publish)/,kuaishou:/^\/article\/publish\//,'weixin-channels':/^\/platform\/post\/create/,zhihu:/^\/write/,juejin:/^\/editor\//,toutiao:/\/graphic\/publish/,'twitter-x':/^\/compose\/post/,bilibili:/^\/platform\/upload\//,'wechat-oa':/^\/cgi-bin\/(appmsg|operate_appmsg)/};
    if (!routes[platform]?.test(location.pathname)) return 'not_editor';
    const el = document.activeElement;
    if (!el || !el.getClientRects().length || el.closest('[aria-hidden="true"], [inert]') || el.matches(':disabled,[readonly],[aria-disabled="true"]')) return 'no_field';
    const isInput = el.matches('textarea, input[type="text"], input:not([type])');
    if (!isInput && !el.isContentEditable) return 'no_field';
    const hint = [el.getAttribute('placeholder'), el.getAttribute('aria-label'), el.getAttribute('name'), el.getAttribute('autocomplete')].join(' ');
    if (/password|passwd|search|email|one-time-code|phone|验证码|密码|手机|搜索|邮箱/i.test(hint)) return 'no_field';
    if ((isInput ? el.value : el.textContent).trim() || el.querySelector('img, video, iframe')) return 'not_empty';
    // Check target, origin, focus and emptiness in the same browser turn as insertion.
    // No clipboard, keystrokes, Enter, synthetic submit or save/publish button.
    if (isInput && el.maxLength >= 0 && text.length > el.maxLength) return 'too_long';
    const normal = s => String(s || '').replace(/\r\n/g, '\n').replace(/\u00a0/g, ' ').trim();
    const done = document.execCommand('insertText', false, text);
    return done && normal(isInput ? el.value : el.innerText || el.textContent) === normal(text) ? 'filled' : 'unconfirmed';
  }
  if (action === 'verify') {
    // Positive evidence must be an actual visible account control on an official page;
    // a dashboard URL, avatar placeholder or the absence of a login prompt is insufficient.
    const controls = Array.from(document.querySelectorAll('button,a,[role="button"],[role="menuitem"]')).filter(el => el.getClientRects().length);
    if (controls.some(el => /^(退出登录|退出登入|登出|切换账号|Log out(?: @\S+)?|Sign out|Switch account)$/i.test((el.innerText || el.getAttribute('aria-label') || '').trim()))) return 'verified';
    return 'unknown';
  }
  return 'opened';
}

// Self-contained function executed in the official page. Asks the platform's own
// current-user endpoint (with the page's existing session) or a logged-in-only
// control. Returns only a login state and a short display name.
export async function probeAccount({ platform }) {
  const clip = value => String(value || '').trim().split('\n')[0].trim().slice(0, 40);
  const visibleText = selector => {
    for (const el of document.querySelectorAll(selector)) {
      const value = el.getClientRects().length ? clip(el.innerText || el.getAttribute('aria-label')) : '';
      if (value) return value;
    }
    return '';
  };
  const api = async (url, init = {}) => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 4000);
    try {
      const response = await fetch(url, {credentials: 'include', signal: controller.signal, ...init});
      let body = null;
      try { body = await response.json(); } catch { /* non-JSON means no evidence */ }
      return {status: response.status, body: body && typeof body === 'object' ? body : null};
    } catch {
      return null;
    } finally {
      clearTimeout(timer);
    }
  };
  const denied = response => response?.status === 401 || response?.status === 403;
  const result = (ok, name, loggedOut) => ok ? {state: 'verified', name: clip(name)} : {state: loggedOut ? 'login_required' : 'unknown'};
  const pick = (data, keys) => keys.map(key => data?.[key]).find(value => typeof value === 'string' && value.trim()) || '';
  if (platform === 'juejin') {
    const r = await api('https://api.juejin.cn/user_api/v1/user/get?aid=2608&uuid=0&spider=0');
    const err = r?.body?.err_no;
    return result(err === 0 && pick(r.body.data, ['user_name']), r?.body?.data?.user_name, denied(r) || (typeof err === 'number' && err !== 0));
  }
  if (platform === 'zhihu') {
    const r = await api('https://www.zhihu.com/api/v4/me');
    return result(r?.status === 200 && !r.body?.error && pick(r.body, ['id']) && pick(r.body, ['name']), r?.body?.name, denied(r));
  }
  if (platform === 'xiaohongshu') {
    const r = await api('https://creator.xiaohongshu.com/api/galaxy/user/info');
    const data = r?.body?.data;
    return result(r?.body?.success === true && pick(data, ['userId']), pick(data, ['userName', 'nickname', 'user_name', 'name']), denied(r));
  }
  if (platform === 'douyin') {
    const r = await api('https://creator.douyin.com/web/api/media/user/info/?aid=1128');
    const user = r?.body?.user;
    return result(r?.body?.status_code === 0 && user && typeof user === 'object', pick(user, ['nickname', 'name']), denied(r));
  }
  if (platform === 'kuaishou') {
    const r = await api('https://cp.kuaishou.com/rest/cp/works/v2/common/pc/current/user', {method: 'POST', headers: {'content-type': 'application/json'}, body: '{}'});
    const data = r?.body?.data;
    return result(r?.body?.result === 1, pick(data, ['nickname', 'user_name', 'userName', 'display_name', 'name']) || visibleText('.user-info-name, .user-line .name'), denied(r));
  }
  if (platform === 'bilibili') {
    const r = await api('https://api.bilibili.com/x/web-interface/nav');
    const data = r?.body?.data;
    return result(data?.isLogin === true, data?.uname, denied(r) || data?.isLogin === false);
  }
  if (platform === 'weixin-channels') {
    // The creator menu only renders for a signed-in assistant account; the login page shows a QR iframe.
    const menu = Array.from(document.querySelectorAll('.finder-ui-desktop-menu')).some(el => el.getClientRects().length);
    return result(menu, visibleText('.finder-nickname, [class*="finder-nickname"]'), false);
  }
  if (platform === 'wechat-oa') {
    return result(/[?&]token=\d+/.test(location.search), visibleText('.weui-desktop-account__nickname, .account_nickname'), false);
  }
  if (platform === 'toutiao') {
    const r = await api('https://mp.toutiao.com/mp/agw/media/get_media_info');
    const data = r?.body?.data;
    const ok = r?.body?.code === 0 && data?.user && typeof data.user === 'object';
    return result(ok, pick(data?.user, ['screen_name', 'name', 'user_name']) || pick(data?.media, ['name', 'media_name', 'display_name']), denied(r));
  }
  if (platform === 'twitter-x') {
    const button = Array.from(document.querySelectorAll("[data-testid='SideNav_AccountSwitcher_Button']")).find(el => el.getClientRects().length);
    const lines = String(button?.innerText || '').split('\n').map(line => line.trim()).filter(Boolean);
    return result(!!button, lines.find(line => line.startsWith('@')) || lines[0], false);
  }
  return {state: 'unknown'};
}

// Spaces the workbench created: the shared "Easel" window, or an older per-platform one.
const ours = s => typeof s?.name === 'string' && /^Easel(-|$)/.test(s.name);
const LEGACY = /^Easel-[a-z-]+-[0-9a-f]{12}$/;
// A platform's own current-user answer outranks the page heuristics: some sites pass
// through a login route before redirecting a signed-in user to the dashboard.
async function judge(page, hosts, platform) {
  const base = await page.evaluate(inspectOrFill, {hosts, action:'verify', platform});
  if (base === 'restricted' || base === 'other') return {state: base};
  let probe = {state: 'unknown'};
  try { probe = await page.evaluate(probeAccount, {platform}); } catch { /* keep the page evidence */ }
  if (probe?.state === 'verified') return {state: 'verified', ...(probe.name ? {name: probe.name} : {})};
  if (base === 'login_required' || probe?.state === 'login_required') return {state: 'login_required'};
  return {state: base === 'verified' ? 'verified' : 'unknown'};
}

function ownSpace(spaces, spaceId, spaceName) {
  // Prefer the recorded id so a renamed or previously per-platform window is reused.
  // Fall back to the shared name when the id is gone (user closed that window).
  return (spaceId != null && spaceId !== '' ? spaces.find(s => s.id === spaceId && ours(s)) : null)
    ?? spaces.find(s => s.name === spaceName) ?? null;
}

async function acquire(entry, spaceName) {
  if (!entry) return taskSpace(spaceName);
  if (entry.ownership === 'agentDelegatedToUser') return takeOverTaskSpace(entry.id);
  if (entry.ownership === 'user') {
    // Finished earlier with its platform tabs kept for the user; re-adopt only those.
    const task = await claimTaskSpace(entry.id);
    for (const tab of await task.tabs()) {
      if (!tab.label && tab.openedBy === 'agent') { try { await task.adopt(tab.page); } catch { /* leave it */ } }
    }
    return task;
  }
  return taskSpace(entry.id);
}

// Hand the window to the user only when they must act (scan, captcha, review a draft).
// Otherwise keep the platform tabs for them without flagging the space as needing attention.
async function release(task, needsUser) {
  if (needsUser) return task.handOff();
  const keep = [];
  for (const page of await task.pages()) {
    try { if (!BLANK.test(await page.url())) keep.push(page.label); } catch { /* skip */ }
  }
  return task.finish({keep});
}

function hostsOf(urls) {
  return [...new Set(urls.map(url => new URL(url).hostname))];
}

function onHosts(url, hosts) {
  try { return hosts.includes(new URL(url).hostname); } catch { return false; }
}

export async function run({ platform, action, urls, text, spaceId, spaceName }) {
  const hosts = hostsOf(urls);
  const onHost = url => onHosts(url, hosts);
  const target = urls[action === 'publish' ? 1 : 0];
  const spaces = await listTaskSpaces();
  const entry = ownSpace(spaces, spaceId, spaceName);
  const inspect = action === 'verify' || action === 'fill';
  if (!entry && inspect) return action === 'verify' ? unknown('请先从本工作台在 Ego 中打开此平台。') : failure('请先打开 Ego 官方发布页，并点选空白编辑框。');
  const task = await acquire(entry, spaceName);
  let result, needsUser = true;
  try {
    const pages = await task.pages();
    const tabs = await task.tabs();
    // Prefer the active managed tab; never adopt a user-created or unknown-origin page.
    // Opening and checking may use another managed tab already on this platform; fill
    // only ever touches the tab the user is working in.
    let tab = tabs.find(item => item.active);
    if (action !== 'fill' && !(tab?.label && onHost(tab.url))) tab = tabs.find(item => item.label && onHost(item.url)) ?? tab;
    let page = tab ? pages.find(p => p.label === tab.label) : pages[0];
    if (!page && !entry) page = task.page('p1');
    if (!page && inspect) result = action === 'verify' ? unknown('当前不是工作台打开的页面，请切换到该平台的工作台标签。') : failure('请切换到工作台打开的官方发布标签，并点选空白编辑框。');
    if (!result && !page) page = await task.newPage();
    const observe = (p, a) => p.evaluate(inspectOrFill, {hosts, action:a, platform, text:a === 'fill' ? text : undefined});
    const assess = p => judge(p, hosts, platform);
    if (!result && !inspect) {
      const current = await page.url();
      let before = 'other';
      if (current.startsWith('https:') && hosts.includes(new URL(current).hostname)) before = await observe(page, 'verify');
      const focus = async () => {
        try { await page.cdp('Page.bringToFront', {}, {timeout:3000}); return true; }
        catch { return false; } // Windows may leave the external window in the taskbar.
      };
      if (before === 'restricted') {
        await focus();
        result = failure('平台提示验证或访问受限，请在原 Ego 窗口按官方要求处理，本次未跳转或重试。');
      }
      else {
        // Never replace an existing login form or unsaved editor. Reuse matching destinations.
        // Another platform's tab stays put: open this platform in a new tab of the same window.
        const continueHere = action === 'login' && before !== 'other';
        if (current !== target && !continueHere) {
          if (!BLANK.test(current)) page = await task.newPage();
          await page.goto(target, {timeout:25000});
        }
        const {state, name} = await assess(page);
        // Only raise the window when the user must act (scan / captcha / work in the editor).
        // Already-logged-in opens stay in the shared Ego window without stealing focus.
        needsUser = state === 'login_required' || state === 'restricted' || action === 'publish';
        const focused = needsUser ? await focus() : false;
        if (state === 'restricted') result = failure('平台提示验证或访问受限，请在 Ego 官方页面人工处理。未自动重试。');
        else result = {ok:true, state:['verified','login_required'].includes(state) ? state : 'unknown', ...(name ? {name} : {}),
          message: state === 'verified' ? '已在同一 Ego 窗口打开该平台（已登录）。'
            : state === 'login_required' ? '已在同一 Ego 窗口打开官方登录页，请在该标签完成登录。' + (focused ? '' : ' 若没看到窗口，请点任务栏里的 Ego。')
            : '已在同一 Ego 窗口打开该平台。' + (focused ? '' : '')};
      }
    } else if (!result) {
      const messages = {verified:'当前官方页面显示已登录。', login_required:'当前官方页面要求登录，请在 Ego 中操作。', restricted:'平台要求验证或限制访问，请在原页面按官方提示处理。', unknown:'未能确认登录状态。可在 Ego 中打开该平台页面后再刷新。', other:'当前页不是该平台已知的官方页面，未操作。', not_editor:'当前不是受支持的官方编辑页，请先进入编辑器或改用复制。', no_field:'请在 Ego 主页面点选空白标题或正文框；内嵌编辑器请手动粘贴。', not_empty:'所选编辑框已有内容，为避免覆盖请手动粘贴。', too_long:'稿件超过此编辑框的字数限制，请先调整并重新审核。', unconfirmed:'未能确认文字写入。请检查官方编辑器，勿重复填入。', filled:'稿件已填入当前编辑框，尚未提交，请在 Ego 核对。'};
      if (action === 'verify') {
        const {state, name} = await assess(page);
        const known = ['verified','restricted','login_required'].includes(state) ? state : 'unknown';
        needsUser = known === 'login_required' || known === 'restricted';
        result = {ok:true, state:known, ...(name ? {name} : {}), message:messages[known]};
      } else {
        const state = await observe(page, action);
        result = {ok:state === 'filled', message:messages[state] || messages.no_field};
      }
    }
  } catch {
    needsUser = true;
    result = failure('Ego 未完成操作，请检查当前官方窗口或授权提示；未启动其他浏览器。');
  } finally {
    await release(task, needsUser || !result?.ok);
  }
  return {...result, spaceId:task.spaceId};
}

async function closeLegacy(spaces, keepId) {
  // Older builds opened one window per platform. The login lives in the browser
  // profile, not the window, so closing them keeps every session.
  for (const space of spaces) {
    if (space.id === keepId || !LEGACY.test(space.name || '')) continue;
    try { await (await acquire(space, space.name)).finish({keep: []}); } catch { /* leave it */ }
  }
}

// Login state of every platform, read in the background with the browser's existing
// sessions. Platforms without a tab are checked in one temporary tab that is closed
// afterwards; nothing is foregrounded and the window is not flagged for attention.
export async function status({ spaceName, spaceId, platforms }) {
  const spaces = await listTaskSpaces();
  const entry = ownSpace(spaces, spaceId, spaceName);
  await closeLegacy(spaces, entry?.id);
  const task = await acquire(entry, spaceName);
  const out = {};
  let probePage = null;
  try {
    const pages = await task.pages();
    const tabs = await task.tabs();
    for (const [platform, options] of Object.entries(platforms)) {
      const hosts = hostsOf(options.urls);
      const tab = tabs.find(item => item.label && onHosts(item.url, hosts));
      let page = tab ? pages.find(p => p.label === tab.label) : null;
      const navigated = !page;
      if (navigated) {
        if (!probePage) {
          const spare = tabs.find(item => item.label && BLANK.test(item.url || ''));
          probePage = (spare && pages.find(p => p.label === spare.label)) || await task.newPage();
        }
        page = probePage;
        try { await page.goto(options.urls[0], {timeout:12000}); } catch { /* inspect whatever loaded */ }
      }
      let state = 'unknown', name;
      try {
        let verdict = await judge(page, hosts, platform);
        // A freshly loaded page may still be redirecting or rendering its signed-in shell.
        for (let i = 0; navigated && i < 2 && verdict.state !== 'verified' && verdict.state !== 'restricted'; i++) {
          await page.waitForTimeout(2000);
          verdict = await judge(page, hosts, platform);
        }
        state = verdict.state === 'other' ? 'unknown' : verdict.state;
        name = verdict.name;
      } catch { /* report unknown */ }
      out[platform] = {exists:!!tab, state, ...(name ? {name} : {})};
    }
  } catch {
    for (const key of Object.keys(platforms)) if (!out[key]) out[key] = {exists:false, state:'unknown'};
  } finally {
    if (probePage) { try { await probePage.close(); } catch { /* ignore */ } }
    await release(task, false);
  }
  return {ok:true, platforms:out, spaceId:task.spaceId};
}
