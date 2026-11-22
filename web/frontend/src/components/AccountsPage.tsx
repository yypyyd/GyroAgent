import { useEffect, useState } from 'react';
import { EGO_PLATFORMS, fetchEgoAccounts, openEgoPage, type EgoAccount } from '../lib/api';
import { useEgoStatus } from '../lib/useEgoStatus';
import LegacyAccountsPage from './LegacyAccountsPage';

export default function AccountsPage() {
  const { status, error, retry } = useEgoStatus();
  if (!status) return <div className="accounts-page"><h1 className="page-title">账号管理</h1>
    {error ? <><p role="alert">{error}</p><button className="btn" onClick={retry}>重试</button></> : <p role="status">正在读取平台列表…</p>}
  </div>;
  return status.enabled ? <EgoAccounts platforms={status.platforms} /> : <LegacyAccountsPage />;
}

function accountBadge(account: EgoAccount | undefined, checking: boolean): { text: string; tone: string } | null {
  if (!account) return checking ? { text: '检查中…', tone: 'unknown' } : null;
  if (account.state === 'verified') return { text: account.name ? `已登录 · ${account.name}` : '已登录', tone: 'verified' };
  if (account.state === 'login_required') return { text: '未登录', tone: 'login' };
  if (account.state === 'restricted') return { text: '需验证', tone: 'restricted' };
  return { text: '状态未知', tone: 'unknown' };
}

function actionLabel(account: EgoAccount | undefined): string {
  if (account?.state === 'verified') return '进入 Ego';
  if (account?.state === 'login_required') return '去登录';
  if (account?.state === 'restricted') return '去验证';
  return '打开 Ego';
}

export function EgoAccounts({ platforms }: { platforms: string[] }) {
  const [busy, setBusy] = useState('');
  const [notice, setNotice] = useState<{ platform: string; message: string; error: boolean } | null>(null);
  const [accounts, setAccounts] = useState<Record<string, EgoAccount>>({});
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState('');
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    let active = true;
    setChecking(true);
    setCheckError('');
    fetchEgoAccounts()
      .then((result) => { if (active) setAccounts(result); })
      .catch((err: unknown) => { if (active) setCheckError(`无法读取登录状态：${err instanceof Error ? err.message : '请检查 Ego 浏览器'}`); })
      .finally(() => { if (active) setChecking(false); });
    return () => { active = false; };
  }, [revision]);

  const run = async (platform: string) => {
    if (busy || checking) return;
    setBusy(platform);
    setNotice(null);
    try {
      const result = await openEgoPage(platform, 'login');
      setAccounts(prev => ({ ...prev, [platform]: { exists: true, state: result.state ?? 'unknown', ...(result.name ? { name: result.name } : {}) } }));
      setNotice({ platform, message: result.message, error: false });
    } catch (err) {
      setNotice({ platform, message: `无法打开官方页面：${err instanceof Error ? err.message : '请检查 Ego 浏览器后重试'}`, error: true });
    } finally {
      setBusy('');
    }
  };

  return (
    <div className="accounts-page ego-accounts">
      <div className="page-head"><div>
        <h1 className="page-title">账号管理</h1>
        <p className="page-subtitle">进入本页会在后台读取 Ego 里已有的登录状态，已登录的平台无需再点。需要扫码或验证时，点对应按钮在 Ego 中完成。</p>
      </div></div>
      <div className="ego-section-head">
        <h2>平台账号</h2><span>{platforms.length} 个平台</span>
        <button type="button" className="btn btn-sm ego-refresh" disabled={!!busy || checking} onClick={() => setRevision(n => n + 1)}>
          {checking ? '检查中…' : '刷新状态'}
        </button>
      </div>
      <div className={`ego-feedback${notice?.error || (!notice && checkError) ? ' is-error' : ''}`} role={notice?.error || (!notice && checkError) ? 'alert' : 'status'} aria-live="polite" aria-atomic="true">
        {notice ? <><strong>{EGO_PLATFORMS.find((p) => p.platform === notice.platform)?.name ?? notice.platform}</strong><span>{notice.message}</span></>
          : <span>{checkError || '需要扫码或验证时，到同一个 Ego 窗口里对应的标签完成即可。'}</span>}
      </div>
      {platforms.length === 0 && <p>此工作台未配置平台入口。</p>}
      <div className="accounts-grid">
        {platforms.map((platform) => {
          const account = accounts[platform];
          const badge = accountBadge(account, checking);
          return (
            <div className="card account-card" key={platform}>
              <div className="account-card-head">
                <span className="account-card-name">{EGO_PLATFORMS.find((p) => p.platform === platform)?.name ?? platform}</span>
                {badge && <span className={`badge ego-state state-${badge.tone}`} title={badge.text}>{badge.text}</span>}
              </div>
              <div className="ego-card-actions">
                <button type="button" className="btn btn-sm btn-primary" disabled={!!busy || checking} onClick={() => run(platform)}>
                  {busy === platform ? '正在打开 Ego…' : actionLabel(account)}
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
