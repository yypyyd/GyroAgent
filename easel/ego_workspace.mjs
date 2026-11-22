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
// ── 自动发布（Ego 全自动）────────────────────────────────────────
// 仅在用户已在前端完成人工审核（reviewed）后由本地后端调用。
// 每个平台一个 driver：打开官方发布页 → 填标题/正文/标签/封面 → 点击发布 → 回执。
// 任何一步失败都不补点重试，草稿保留在平台草稿箱，由用户在 Ego 中处理。

const sleep = ms => new Promise(r => setTimeout(r, ms));

// 点击页面上 innerText 精确等于 text 的最内层可见元素（button/a/[role=button]/li/span/div）。
// evaluate 内直接 el.click()：派发真实 click 事件，React 合成事件可正常触发。
export function clickExactText({ text, attr = 'data-ego-click' }) {
  const clean = s => String(s || '').replace(/\s+/g, ' ').trim();
  const want = clean(text);
  if (!want) return 0;
  const candidates = Array.from(document.querySelectorAll('button, a, [role="button"], li, span, div, label'))
    .filter(el => el.getClientRects().length && !el.closest('[aria-hidden="true"], [inert]'));
  // 最内层：没有子元素也精确匹配，或自身 innerText 精确匹配且不可再分
  const leaf = candidates.filter(el => clean(el.innerText) === want)
    .sort((a, b) => (a.innerText || '').length - (b.innerText || '').length);
  const el = leaf.find(e => !el_has_clickable_child(e)) || leaf[0];
  function el_has_clickable_child(node) {
    return Array.from(node.children).some(c => clean(c.innerText) === want);
  }
  if (!el) return 0;
  el.setAttribute(attr, '1');
  el.click();
  return 1;
}

// 关闭遮挡表单的弹窗：点已知的确认/关闭类按钮，最多 round 轮。
async function dismissModals(page, rounds = 3) {
  const DISMISS = /知道了|我知道了|已知悉|关闭|取消|暂不|以后再|不再提示|跳过|确定|我知道了|got it|close/i;
  for (let i = 0; i < rounds; i++) {
    const hit = await page.evaluate(({ re }) => {
      const rx = new RegExp(re, 'i');
      const els = Array.from(document.querySelectorAll('[class*=modal] button, [class*=Modal] button, [class*=dialog] button, [role=dialog] button, [class*=modal] [class*=close], [class*=Modal] [class*=close]'))
        .filter(e => e.getClientRects().length);
      const el = els.find(e => rx.test((e.innerText || e.getAttribute('aria-label') || '').trim()) || /close|关闭/i.test(e.className || ''));
      if (!el) return 0;
      el.click();
      return 1;
    }, { re: DISMISS.source });
    if (!hit) return;
    await page.waitForTimeout(700);
  }
}

async function juejinPublish(page, payload) {
  const titleSel = 'loc=css:input[placeholder*="输入文章标题"]';
  await page.fill(titleSel, payload.title.slice(0, 100));
  // 正文是 CodeMirror：先清空（复用草稿页时避免追加），聚焦后用系统粘贴写入 Markdown
  await page.evaluate(() => {
    const cm = document.querySelector('.CodeMirror');
    if (cm && cm.CodeMirror) cm.CodeMirror.setValue('');
  }).catch(() => {});
  await page.click('loc=css:.CodeMirror-code, .cm-content').catch(() => {});
  await page.keyboard.paste(payload.body);
  await page.waitForTimeout(2000);
  await dismissModals(page);
  // 点顶栏「发布」打开发布设置弹窗
  const opened = await page.evaluate(clickExactText, { text: '发布' });
  if (!opened) return { ok: false, message: '未找到发布按钮' };
  const hasDialog = await page.waitForSelector('text="确定并发布"', { timeout: 8000 }).then(() => true).catch(() => false);
  if (!hasDialog) return { ok: false, message: '发布设置弹窗未出现，可能有必填项未完成', draft: true };
  // 分类（必填）：payload.category 优先，否则点第一个选项
  const category = payload.category || '前端';
  const catOk = await page.evaluate(clickExactText, { text: category });
  if (!catOk) {
    // 回退：点分类区第一个选项文本
    await page.evaluate(() => {
      const texts = ['后端', '前端', 'Android', 'iOS', '人工智能', '开发工具', '代码人生', '阅读'];
      for (const t of texts) {
        const el = Array.from(document.querySelectorAll('div,span,li')).find(e => e.getClientRects().length && (e.innerText || '').trim() === t);
        if (el) { el.click(); return t; }
      }
      return null;
    });
  }
  await page.waitForTimeout(400);
  // 标签（必填，最多 3 个）：byte-select 下拉，优先点预设选项，否则搜索
  const tags = (payload.tags || []).slice(0, 3);
  if (!tags.length) return { ok: false, message: '缺少标签（掘金发布必填）', draft: true };
  for (const tag of tags) {
    try {
      // 标记该标签行的 select 与 input
      await page.evaluate(() => {
        document.querySelectorAll("[data-ego-tag]").forEach(e => e.removeAttribute("data-ego-tag"));
        const pop = document.querySelector(".publish-popup.active") || document.querySelector(".publish-popup");
        const lab = [...pop.querySelectorAll(".label")].find(e => (e.innerText || "").includes("添加标签"));
        const item = lab && lab.closest(".form-item");
        const sel = item && item.querySelector(".byte-select");
        const inp = item && item.querySelector(".byte-select__input");
        if (sel) sel.setAttribute("data-ego-tag", "sel");
        if (inp) inp.setAttribute("data-ego-tag", "inp");
      });
      await page.click('loc=css:[data-ego-tag="sel"]').catch(() => {});
      await page.waitForTimeout(700);
      // 优先点与标签同名的预设选项（li.byte-select-option）
      let picked = await page.evaluate(({ t }) => {
        const opt = [...document.querySelectorAll("li.byte-select-option, .byte-select-option")]
          .find(e => e.getClientRects().length && (e.innerText || "").trim() === t);
        if (!opt) return 0;
        opt.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
        opt.click();
        return 1;
      }, { t: tag });
      if (!picked) {
        // 无同名预设：输入搜索词后点第一个建议
        await page.fill('loc=css:[data-ego-tag="inp"]', tag).catch(() => {});
        await page.waitForTimeout(1000);
        await page.evaluate(() => {
          const opt = [...document.querySelectorAll("li.byte-select-option, .byte-select-option, .byte-select-dropdown li")]
            .find(e => e.getClientRects().length && (e.innerText || "").trim().length > 0 && (e.innerText || "").trim().length < 30);
          if (opt) { opt.dispatchEvent(new MouseEvent("mousedown", { bubbles: true })); opt.click(); }
        });
      }
      await page.waitForTimeout(400);
    } catch { /* 单个标签失败不阻塞 */ }
  }
  // 校验标签确实已加入（仍显示占位符说明没选上）
  const tagAdded = await page.evaluate(() => {
    const pop = document.querySelector(".publish-popup.active") || document.querySelector(".publish-popup");
    const lab = [...pop.querySelectorAll(".label")].find(e => (e.innerText || "").includes("添加标签"));
    const item = lab && lab.closest(".form-item");
    return !!(item && !/请搜索添加标签/.test(item.innerText || ""));
  }).catch(() => false);
  if (!tagAdded) return { ok: false, message: '标签未能添加成功（掘金必填）', draft: true };
  // 摘要（必填，掘金要求 50-100 字）：优先 payload.abstract，否则从正文提取
  const plain = String(payload.body || "").replace(/[#>*`\-\[\]()!_|]/g, " ").replace(/\s+/g, " ").trim();
  let abstract = String(payload.abstract || "").trim() || (payload.title + " " + plain).replace(/\s+/g, " ").trim();
  abstract = abstract.slice(0, 100);
  if (abstract.length < 50) {
    // 用正文补足到 50 字
    abstract = (abstract + " " + plain).replace(/\s+/g, " ").trim().slice(0, 100);
  }
  if (abstract.length < 50) return { ok: false, message: '摘要不足 50 字（掘金必填）', draft: true };
  try {
    await page.evaluate(() => {
      const pop = document.querySelector(".publish-popup.active") || document.querySelector(".publish-popup");
      const lab = [...pop.querySelectorAll(".label")].find(e => (e.innerText || "").includes("编辑摘要"));
      const ta = lab && lab.closest(".form-item") && lab.closest(".form-item").querySelector("textarea");
      if (ta) ta.setAttribute("data-ego-tag", "abs");
    });
    await page.fill('loc=css:[data-ego-tag="abs"]', abstract).catch(() => {});
    await page.evaluate(() => {
      const ta = document.querySelector('[data-ego-tag="abs"]');
      if (ta) { ta.dispatchEvent(new Event('input', { bubbles: true })); ta.dispatchEvent(new Event('change', { bubbles: true })); ta.blur(); }
    }).catch(() => {});
    await page.waitForTimeout(500);
  } catch { /* 摘要失败仍尝试发布 */ }
  // 封面（可选）：取第一张图片
  if (payload.media?.length) {
    try {
      const chooserPromise = page.waitForFileChooser({ timeout: 5000 }).catch(() => null);
      await page.evaluate(clickExactText, { text: '上传封面' });
      const chooser = await chooserPromise;
      if (chooser) await chooser.setFiles(payload.media[0]);
      else await page.setInputFiles('loc=css:input[type=file]', [payload.media[0]]).catch(() => {});
      await page.waitForTimeout(1500);
      // 可能的裁剪/确认弹窗
      await page.evaluate(clickExactText, { text: '确定' }).catch(() => {});
      await page.evaluate(clickExactText, { text: '完成' }).catch(() => {});
      await page.waitForTimeout(800);
    } catch { /* 封面失败仍可发布 */ }
  }
  // 确定并发布：轮询 URL/弹窗状态/平台报错；5 秒无进展时补点一次（防点击落空）
  let fired = await page.evaluate(clickExactText, { text: '确定并发布' });
  if (!fired) return { ok: false, message: '未找到「确定并发布」按钮', draft: true };
  for (let i = 0; i < 20; i++) {
    await page.waitForTimeout(1000);
    if (i === 5) {
      fired = await page.evaluate(clickExactText, { text: '确定并发布' }).catch(() => 0);
    }
    const st = await page.evaluate(() => ({
      url: location.href,
      popOpen: !!document.querySelector(".publish-popup.active"),
      err: (document.body.innerText.match(/摘要不满足|请填写|请选择|发布失败|操作频繁|审核中|发布成功|已发布|至少.{0,10}字/g) || [])[0] || "",
    })).catch(() => null);
    if (!st) continue;
    if (/\/post\/\d+|\/article\/\d+|\/published|发布成功/.test(st.url + " " + st.err)) {
      return { ok: true, url: st.url, message: '已发布' };
    }
    if (/失败|频繁|不满足|请填写|请选择|至少/.test(st.err)) return { ok: false, message: `平台提示：${st.err}`, draft: true };
    if (!st.popOpen) {
      const done = await page.evaluate(() => /发布成功|审核中|已提交|已发布/.test(document.body?.innerText || "")).catch(() => false);
      return done
        ? { ok: true, url: st.url, message: '已提交发布' }
        : { ok: false, message: '弹窗已关闭但未确认发布结果，请在 Ego 中核对', draft: true };
    }
  }
  return { ok: false, message: '已点击发布，未确认跳转结果，请在 Ego 中核对', draft: true };
}

async function toutiaoPublish(page, payload) {
  await dismissModals(page, 4);
  await page.fill('loc=css:textarea[placeholder*="请输入文章标题"]', payload.title.slice(0, 30));
  // 复用发布页时先清空正文，避免追加
  await page.evaluate(() => {
    const el = document.querySelector('.ProseMirror[contenteditable="true"]');
    if (el) { el.innerHTML = ''; el.dispatchEvent(new InputEvent('input', { bubbles: true })); }
  }).catch(() => {});
  await page.click('loc=css:.ProseMirror').catch(() => {});
  await page.keyboard.paste(payload.body);
  await page.waitForTimeout(1500);
  await dismissModals(page);
  // 封面：选「单图」后上传第一张图（无图则「无封面」）
  if (payload.media?.length) {
    try {
      await page.evaluate(clickExactText, { text: '单图' });
      await page.waitForTimeout(1200);
      const chooserPromise = page.waitForFileChooser({ timeout: 5000 }).catch(() => null);
      // 上传区通常是图片占位按钮/文字
      await page.evaluate(() => {
        const els = Array.from(document.querySelectorAll('div,span,button,label'))
          .filter(e => e.getClientRects().length && /上传|添加|选择/.test(e.innerText || '') && (e.innerText || '').length < 20);
        els.sort((a, b) => (a.innerText || '').length - (b.innerText || '').length)[0]?.click();
      });
      const chooser = await chooserPromise;
      if (chooser) await chooser.setFiles(payload.media[0]);
      else await page.setInputFiles('loc=css:input[type=file]', [payload.media[0]]).catch(() => {});
      await page.waitForTimeout(1500);
    } catch { /* 封面失败仍可发布 */ }
  } else {
    await page.evaluate(clickExactText, { text: '无封面' }).catch(() => {});
  }
  await dismissModals(page);
  // 「预览并发布」→ 预览弹窗里再点发布
  const prev = await page.evaluate(clickExactText, { text: '预览并发布' });
  if (!prev) return { ok: false, message: '未找到「预览并发布」按钮', draft: true };
  await page.waitForTimeout(2500);
  await dismissModals(page, 2);
  const fired = await page.evaluate(clickExactText, { text: '发布' })
    || await page.evaluate(clickExactText, { text: '确定发布' })
    || await page.evaluate(clickExactText, { text: '确认发布' });
  if (!fired) return { ok: false, message: '预览弹窗中未找到发布按钮', draft: true };
  await page.waitForTimeout(5000);
  const url = await page.url().catch(() => '');
  const ok = /group\/\d+|article|publish.*success|graphic/.test(url) || await page.evaluate(() => /发布成功|审核中|已提交/.test(document.body?.innerText || '')).catch(() => false);
  return { ok, url: ok ? url : undefined, message: ok ? '已提交发布' : '已点击发布，未确认结果，请在 Ego 中核对', draft: !ok };
}

async function zhihuPublish(page, payload) {
  // 知乎专栏编辑器：textarea 标题 + Draft.js 正文，点「发布」直达 /p/ 页面
  await page.fill('loc=css:textarea[placeholder*="请输入标题"]', payload.title.slice(0, 100));
  await page.evaluate(() => {
    const el = document.querySelector('.public-DraftEditor-content');
    if (el) { el.innerHTML = ''; el.dispatchEvent(new InputEvent('input', { bubbles: true })); }
  }).catch(() => {});
  await page.click('loc=css:.public-DraftEditor-content').catch(() => {});
  await page.keyboard.paste(payload.body);
  await page.waitForTimeout(2000);
  await dismissModals(page);
  const fired = await page.evaluate(clickExactText, { text: '发布' });
  if (!fired) return { ok: false, message: '未找到「发布」按钮', draft: true };
  for (let i = 0; i < 15; i++) {
    await page.waitForTimeout(1000);
    const st = await page.evaluate(() => ({
      url: location.href,
      err: (document.body.innerText.match(/发布失败|审核中|操作频繁|请填写|至少.{0,10}字/g) || [])[0] || "",
    })).catch(() => null);
    if (!st) continue;
    if (/\/p\/\d+/.test(st.url)) return { ok: true, url: st.url, message: '已发布' };
    if (/失败|频繁|请填写|至少/.test(st.err)) return { ok: false, message: `平台提示：${st.err}`, draft: true };
  }
  return { ok: false, message: '已点击发布，未确认跳转结果，请在 Ego 中核对', draft: true };
}

async function xhsPublish(page, payload) {
  // 小红书图文笔记：必须至少 1 张图片；标题 ≤20 字，正文 ≤1000 字
  const media = (payload.media || []).filter(m => /\.(png|jpe?g|webp)$/i.test(m));
  if (!media.length) return { ok: false, message: '小红书发布需要至少一张图片', draft: false };
  // 切到「上传图文」tab（真实点击，合成事件不触发 tab 切换）
  const tabMarked = await page.evaluate(() => {
    const t = [...document.querySelectorAll('.creator-tab')].find(e => (e.innerText || '').trim() === '上传图文');
    if (t) { t.setAttribute('data-ego-xhs', 'tab'); return 1; }
    return 0;
  });
  if (tabMarked) {
    await page.click('loc=css:[data-ego-xhs="tab"]').catch(() => {});
    await page.waitForTimeout(2000);
  }
  // 图片 file input（accept 含图片格式的那个）
  await page.evaluate(() => {
    const inp = [...document.querySelectorAll('input[type=file]')].find(e => /jpe?g|png|webp/i.test(e.accept || ''));
    if (inp) inp.setAttribute('data-ego-xhs', 'img');
  });
  try {
    await page.setInputFiles('loc=css:[data-ego-xhs="img"]', media.slice(0, 9));
  } catch (e) {
    return { ok: false, message: `图片上传失败：${String(e && e.message || e).slice(0, 120)}`, draft: false };
  }
  const edOk = await page.waitForSelector('loc=css:.tiptap.ProseMirror', { timeout: 25000 }).then(() => true).catch(() => false);
  if (!edOk) return { ok: false, message: '图片上传后编辑器未出现', draft: true };
  await page.fill('loc=css:input.d-text[placeholder*="填写标题"]', payload.title.slice(0, 20));
  const tags = (payload.tags || []).slice(0, 5).map(t => '#' + String(t).replace(/^#+/, '')).join(' ');
  const body = (payload.body + (tags ? ' ' + tags : '')).slice(0, 1000);
  await page.click('loc=css:.tiptap.ProseMirror').catch(() => {});
  await page.keyboard.paste(body);
  await page.waitForTimeout(2000);
  await dismissModals(page);
  // 提交按钮是原生 <button>「发布」（侧栏「发布笔记」是 div，不能点）
  const fired = await page.evaluate(() => {
    const b = [...document.querySelectorAll('button')].find(e => e.getClientRects().length && (e.innerText || '').trim() === '发布');
    if (b) { b.setAttribute('data-ego-xhs', 'pub'); return 1; }
    return 0;
  });
  if (!fired) return { ok: false, message: '未找到「发布」按钮', draft: true };
  await page.click('loc=css:[data-ego-xhs="pub"]').catch(() => {});
  for (let i = 0; i < 15; i++) {
    await page.waitForTimeout(1000);
    const st = await page.evaluate(() => ({
      url: location.href,
      hit: (document.body.innerText.match(/发布成功|审核中|发布失败|内容违规|敏感|确定/g) || [])[0] || '',
    })).catch(() => null);
    if (!st) continue;
    if (/\/publish\/success|发布成功/.test(st.url + ' ' + st.hit)) return { ok: true, url: st.url, message: '已发布' };
    if (/失败|违规|敏感/.test(st.hit)) return { ok: false, message: `平台提示：${st.hit}`, draft: true };
  }
  return { ok: false, message: '已点击发布，未确认结果，请在 Ego 中核对', draft: true };
}

async function wechatOaPublish(page, payload) {
  // 公众号图文：编辑器 URL 带动态 token，从后台首页链接里提取后直接打开
  const cur = await page.url().catch(() => '');
  if (!/appmsg/.test(cur)) {
    await page.goto('https://mp.weixin.qq.com/', { timeout: 30000 });
    await page.waitForTimeout(4000);
    const token = await page.evaluate(() => {
      const a = [...document.querySelectorAll('a[href*="token="]')].map(e => e.href).find(h => /token=\d+/.test(h));
      const m = a && a.match(/token=(\d+)/);
      return m ? m[1] : '';
    });
    if (!token) return { ok: false, message: '未获取到公众号 token，请先在 Ego 中登录并打开后台首页', draft: false };
    await page.goto(`https://mp.weixin.qq.com/cgi-bin/appmsg?t=media/appmsg_edit_v2&action=edit&isNew=1&type=77&createType=0&token=${token}&lang=zh_CN`, { timeout: 30000 });
    await page.waitForTimeout(5000);
  }
  // 1. 标题：定位 y 150-400 的矮 ProseMirror，focus 后粘贴（会同步 js_title）
  const titleOk = await page.evaluate(() => {
    const pm = [...document.querySelectorAll('.ProseMirror')].find(e => {
      const b = e.getBoundingClientRect();
      return b.height > 0 && b.height < 80 && b.y > 100 && b.y < 500;
    });
    if (!pm) return 0;
    pm.setAttribute('data-ego-wx', 'title');
    return 1;
  });
  if (!titleOk) return { ok: false, message: '未找到公众号标题编辑器', draft: true };
  await page.click('loc=css:[data-ego-wx="title"]').catch(() => {});
  await page.evaluate(() => {
    const pm = document.querySelector('[data-ego-wx="title"]');
    if (pm) pm.focus();
  }).catch(() => {});
  await page.keyboard.paste(payload.title.slice(0, 64));
  await page.waitForTimeout(800);
  // 2. 正文：含「从这里开始写正文」或最高的 ProseMirror
  const bodySel = await page.evaluate(() => {
    const pms = [...document.querySelectorAll('.ProseMirror')].filter(e => e.getClientRects().length);
    const body = pms.find(e => (e.innerText || '').includes('从这里开始写正文')) ||
      pms.sort((a, b) => b.getBoundingClientRect().height - a.getBoundingClientRect().height)[0];
    if (body) body.setAttribute('data-ego-wx', 'body');
    return body ? 1 : 0;
  });
  if (!bodySel) return { ok: false, message: '未找到公众号正文编辑器', draft: true };
  await page.click('loc=css:[data-ego-wx="body"]').catch(() => {});
  await page.keyboard.paste(payload.body.slice(0, 20000));
  await page.waitForTimeout(1500);
  // 3. 封面（群发必填）：图片 file input
  if (payload.media?.length) {
    try {
      await page.evaluate(() => {
        const inp = [...document.querySelectorAll('input[type=file]')].find(e => /image/i.test(e.accept || ''));
        if (inp) inp.setAttribute('data-ego-wx', 'cover');
      });
      await page.setInputFiles('loc=css:[data-ego-wx="cover"]', payload.media.slice(0, 1)).catch(() => {});
      await page.waitForTimeout(3000);
    } catch { /* 封面失败仍尝试 */ }
  }
  // 4. 正文插入一张图（公众号要求正文含图）：光标移到正文末尾 → 图片按钮 → file input
  if (payload.media?.length) {
    try {
      await page.evaluate(() => {
        const body = document.querySelector('[data-ego-wx="body"]');
        if (body) { body.focus(); const s = getSelection(); s.selectAllChildren(body); s.collapseToEnd(); }
      });
      await page.evaluate(() => {
        const b = [...document.querySelectorAll('button,div,span,a')].find(e => e.getClientRects().length && (e.innerText || '').trim() === '图片' && e.children.length < 4);
        if (b) b.setAttribute('data-ego-wx', 'imgbtn');
      });
      await page.click('loc=css:[data-ego-wx="imgbtn"]').catch(() => {});
      await page.waitForTimeout(1200);
      await page.setInputFiles('loc=css:input[type=file]', payload.media.slice(1, 2).length ? payload.media.slice(1, 2) : payload.media.slice(0, 1)).catch(() => {});
      await page.waitForTimeout(4000);
    } catch { /* 内嵌图失败仍尝试 */ }
  }
  await dismissModals(page);
  // 5. 点「发表」→ 可能的确认弹窗 → 群发
  const fired = await page.evaluate(() => {
    const b = [...document.querySelectorAll('button')].find(e => e.getClientRects().length && (e.innerText || '').trim() === '发表');
    if (b) { b.setAttribute('data-ego-wx', 'pub'); return 1; }
    return 0;
  });
  if (!fired) return { ok: false, message: '未找到「发表」按钮', draft: true };
  await page.click('loc=css:[data-ego-wx="pub"]').catch(() => {});
  await page.waitForTimeout(2500);
  // 发表后可能有确认弹窗（群发/预览），再点确定/群发
  for (let i = 0; i < 12; i++) {
    await page.waitForTimeout(1000);
    const st = await page.evaluate(() => ({
      url: location.href,
      hit: (document.body.innerText.match(/群发成功|发表成功|发送成功|审核中|发表失败|群发|确定|确认/g) || [])[0] || '',
      dlg: [...document.querySelectorAll('[class*=dialog],[class*=modal],[class*=Dialog],[class*=layer]')]
        .filter(e => e.getClientRects().length && (e.innerText || '').trim() && (e.innerText || '').trim().length < 300)
        .map(e => (e.innerText || '').replace(/\n/g, '|').slice(0, 80))[0] || '',
    })).catch(() => null);
    if (!st) continue;
    if (/群发成功|发表成功|发送成功|appmsgpublish|publish.*success/.test(st.url + ' ' + st.hit)) {
      return { ok: true, url: st.url, message: '已群发' };
    }
    if (/失败|错误/.test(st.hit)) return { ok: false, message: `平台提示：${st.hit}`, draft: true };
    // 有确认弹窗时点「确定/群发」
    if (st.dlg && /群发|确定|确认|发表/.test(st.dlg)) {
      const conf = await page.evaluate(() => {
        const btns = [...document.querySelectorAll('button,[class*=btn],div,span')]
          .filter(e => e.getClientRects().length && /^(群发|确定|确认|发表)$/.test((e.innerText || '').trim()));
        const b = btns[btns.length - 1];
        if (b) { b.setAttribute('data-ego-wx', 'cfm'); return 1; }
        return 0;
      });
      if (conf) await page.click('loc=css:[data-ego-wx="cfm"]').catch(() => {});
    }
  }
  return { ok: false, message: '已点击发表，未确认群发结果，请在 Ego 中核对', draft: true };
}

const PUBLISH_DRIVERS = { juejin: juejinPublish, toutiao: toutiaoPublish, zhihu: zhihuPublish, xiaohongshu: xhsPublish, 'wechat-oa': wechatOaPublish };
// 已在「可发布的编辑页」的 URL 判定；不匹配则重新打开目标编辑器
const READY_PATH = { juejin: /\/editor\//, toutiao: /\/publish|graphic\/publish/, zhihu: /\/write/, xiaohongshu: /\/publish\/publish/, 'wechat-oa': /appmsg/ };

export async function publish({ platform, urls, spaceId, spaceName, payload }) {
  const hosts = hostsOf(urls);
  const onHost = url => onHosts(url, hosts);
  const target = urls[1] || urls[0];
  if (!PUBLISH_DRIVERS[platform]) return failure(`平台 ${platform} 暂不支持 Ego 自动发布`);
  if (!payload?.title?.trim() && !payload?.body?.trim()) return failure('标题和正文不能为空');
  const spaces = await listTaskSpaces();
  const entry = ownSpace(spaces, spaceId, spaceName);
  const task = await acquire(entry, spaceName);
  let result, needsUser = true;
  try {
    const pages = await task.pages();
    const tabs = await task.tabs();
    let tab = tabs.find(item => item.active);
    if (!(tab?.label && onHost(tab.url))) tab = tabs.find(item => item.label && onHost(item.url)) ?? tab;
    let page = tab ? pages.find(p => p.label === tab.label) : pages[0];
    if (!page && !entry) page = task.page('p1');
    if (!page) page = await task.newPage();
    const current = await page.url();
    const readyRe = READY_PATH[platform];
    const ready = onHost(current) && (!readyRe || readyRe.test(current));
    if (!ready) {
      if (!onHost(current) && !BLANK.test(current)) page = await task.newPage();
      await page.goto(target, {timeout: 30000});
    }
    const {state} = await judge(page, hosts, platform);
    if (state === 'login_required' || state === 'restricted') {
      await page.cdp('Page.bringToFront', {}, {timeout:3000}).catch(() => {});
      return {...failure(state === 'restricted' ? '平台要求验证，请在 Ego 中人工处理' : '请先在 Ego 中登录该平台'), spaceId: task.spaceId};
    }
    result = await PUBLISH_DRIVERS[platform](page, payload);
    needsUser = !result?.ok;
    if (result?.ok) {
      try { await page.cdp('Page.bringToFront', {}, {timeout:3000}); } catch {}
      result = {...result, spaceId: task.spaceId};
    }
  } catch (e) {
    needsUser = true;
    result = failure(`Ego 自动发布未完成（${String(e && e.message || e).slice(0, 160)}），内容可能已存为草稿。`);
  } finally {
    await release(task, needsUser);
  }
  return {...(result || failure('发布失败')), spaceId: task.spaceId ?? result?.spaceId};
}

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
