import { useState, useEffect } from 'react';
import { fetchTrends, fetchSchedule, fetchOutputs, EGO_PLATFORMS, fetchIdeas, openEgoPage } from '../lib/api';
import type { TrendGroup, ScheduleItem, OutputNode, Idea } from '../lib/api';
import type { Page } from './Sidebar';
import ContentPanel from './ContentPanel';
import LegacyDashboardPage from './LegacyDashboardPage';
import { useEgoStatus } from '../lib/useEgoStatus';
import {
  IconFire, IconCalendar, IconOutputs, IconChat, IconSkills, IconAccounts,
  IconIdea, IconPublish,
} from './icons';

interface DashboardProps {
  persona: string;
  gatewayStatus: string;
  apiDirect: boolean;
  agentRuntime: string;
  onNavigate: (page: Page) => void;
  onUseTopic: (title: string) => void;
}

const STATUS_LABEL: Record<string, string> = { idea: '选题', draft: '草稿', scheduled: '待发', published: '已发' };

export default function DashboardPage(props: DashboardProps) {
  const { status, error, retry } = useEgoStatus();
  if (status?.enabled === false) return <LegacyDashboardPage {...props} />;
  if (!status && !error) return <div className="loading" role="status">正在读取工作台配置…</div>;
  return <EgoDashboard {...props} platforms={status?.platforms ?? []} modeError={error} retry={retry} />;
}

function EgoDashboard({ persona, onNavigate, onUseTopic, platforms, modeError, retry }: DashboardProps & {
  platforms: string[]; modeError: string; retry: () => void;
}) {
  const [trends, setTrends] = useState<TrendGroup[]>([]);
  const [schedule, setSchedule] = useState<ScheduleItem[]>([]);
  const [outputs, setOutputs] = useState<OutputNode[]>([]);
  const [opening, setOpening] = useState('');
  const [notice, setNotice] = useState<{ message: string; error: boolean } | null>(null);
  const [ideas, setIdeas] = useState<Idea[]>([]);

  useEffect(() => {
    fetchTrends(undefined, 6).then((d) => setTrends(d.trends)).catch(() => {});
    fetchSchedule().then(setSchedule).catch(() => {});
    fetchOutputs().then(setOutputs).catch(() => {});
    fetchIdeas().then(setIdeas).catch(() => {});
  }, []);

  const openAnalytics = async (platform: string) => {
    setOpening(platform);
    setNotice(null);
    try {
      const result = await openEgoPage(platform, 'manage');
      setNotice({ message: result.message, error: false });
    } catch (err) {
      setNotice({ message: `无法打开官方后台：${err instanceof Error ? err.message : '请检查 Ego 浏览器后重试'}`, error: true });
    } finally {
      setOpening('');
    }
  };

  const hour = new Date().getHours();
  const greet = hour < 6 ? '夜深了' : hour < 12 ? '上午好' : hour < 14 ? '中午好' : hour < 18 ? '下午好' : '晚上好';
  // 本地日期（与日历页 ymd 一致）：toISOString 是 UTC，会把「今天」错判成昨天/明天
  const today = new Date();
  const todayStr = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;
  const upcoming = [...schedule]
    .filter((s) => s.date >= todayStr && s.status !== 'published')
    .sort((a, b) => (a.date + a.time).localeCompare(b.date + b.time)).slice(0, 5);
  const recent = outputs.slice(0, 5);
  const pendingIdeas = ideas.filter((i) => i.status === 'pending');

  const quick: { label: string; page: Page; Icon: typeof IconChat }[] = [
    { label: '开始对话', page: 'chat', Icon: IconChat },
    { label: '看热点', page: 'trends', Icon: IconFire },
    { label: '拆爆款', page: 'breakdown', Icon: IconSkills },
    { label: '记选题', page: 'ideas', Icon: IconIdea },
    { label: '排日历', page: 'calendar', Icon: IconCalendar },
    { label: '去发布', page: 'publish', Icon: IconPublish },
  ];

  const stats: { label: string; value: string; page: Page; Icon: typeof IconChat }[] = [
    { label: '待做选题', value: String(pendingIdeas.length), page: 'ideas', Icon: IconIdea },
    { label: '待发排期', value: String(upcoming.length), page: 'calendar', Icon: IconCalendar },
    { label: '内容项目', value: String(outputs.length), page: 'outputs', Icon: IconOutputs },
    { label: '账号管理平台', value: modeError ? '—' : String(platforms.length), page: 'accounts', Icon: IconAccounts },
  ];

  return (
    <div className="page-scroll dash-page">
      <div className="dash-hero">
        <h1 className="page-title">{greet} 👋</h1>
        <p className="page-subtitle">
          {persona ? `围绕「${persona}」，开始今天的创作。` : '把灵感变成作品，开始今天的创作。'}
          从热点到发布，一站式搞定今天的内容。
        </p>
        <div className="dash-quick">
          {quick.map((q) => (
            <button key={q.page} className="dash-quick-btn" onClick={() => onNavigate(q.page)}>
              <q.Icon size={16} /><span>{q.label}</span>
            </button>
          ))}
        </div>
      </div>

      {/* 概览数字 */}
      <div className="dash-stats">
        {stats.map((s) => (
          <button key={s.label} className="card card-hover dash-stat" onClick={() => onNavigate(s.page)}>
            <span className="dash-stat-ic"><s.Icon size={18} /></span>
            <span className="dash-stat-val">{s.value}</span>
            <span className="dash-stat-label">{s.label}</span>
          </button>
        ))}
      </div>

      <div className="dash-grid">
        {/* 今日热点 */}
        <ContentPanel className="dash-card" label="今日热点列表" header={<div className="dash-card-head">
            <span><IconFire size={16} /> 今日热点</span>
            <button className="dash-more" onClick={() => onNavigate('trends')}>热点雷达 →</button>
          </div>}>
          {trends.length === 0 && <div className="dash-empty">暂无热点，请到热点雷达查看数据源状态。</div>}
          {trends.map((g) => (
            <div key={g.platform} className="dash-trend-group">
              <div className="dash-trend-plat">{g.label}{g.stale ? ' · 过期缓存' : ''}</div>
              {g.error && <div className="dash-empty">{g.error.message}</div>}
              {g.ok && g.items.length === 0 && <div className="dash-empty">采集成功，暂无热点</div>}
              {g.items.slice(0, 3).map((it, i) => (
                <div key={i} className="dash-trend-item" title={`${it.title}（点击做成内容）`}>
                  <span className="dash-trend-title" onClick={() => onUseTopic(it.title)}>{it.title}</span>
                </div>
              ))}
            </div>
          ))}
        </ContentPanel>

        {/* 选题库 */}
        <ContentPanel className="dash-card" label="待做选题列表" header={<div className="dash-card-head">
            <span><IconIdea size={16} /> 选题库 · 待做</span>
            <button className="dash-more" onClick={() => onNavigate('ideas')}>全部 →</button>
          </div>}>
          {pendingIdeas.length === 0 && <div className="dash-empty">还没攒选题，去热点雷达收藏几个吧</div>}
          {pendingIdeas.slice(0, 5).map((it) => (
            <div key={it.id} className="dash-idea" onClick={() => onUseTopic(it.title)} title="点击做成内容">
              <span className="dash-idea-title">{it.title}</span>
              {it.source && <span className="badge">{it.source}</span>}
            </div>
          ))}
        </ContentPanel>

        {/* 近期排期 */}
        <ContentPanel className="dash-card" label="近期排期列表" header={<div className="dash-card-head">
            <span><IconCalendar size={16} /> 近期排期</span>
            <button className="dash-more" onClick={() => onNavigate('calendar')}>日历 →</button>
          </div>}>
          {upcoming.length === 0 && <div className="dash-empty">暂无排期，去日历安排一条吧</div>}
          {upcoming.map((s) => (
            <div key={s.id} className="dash-sched" onClick={() => onNavigate('calendar')}>
              <span className="dash-sched-date">{s.date.slice(5)}</span>
              <span className="dash-sched-title">{s.platform ? `[${s.platform}] ` : ''}{s.title}</span>
              <span className="badge">{STATUS_LABEL[s.status] || s.status}</span>
            </div>
          ))}
        </ContentPanel>

        {/* 最近产物 */}
        <ContentPanel className="dash-card dash-card-top" label="最近产物列表" header={<div className="dash-card-head">
            <span><IconOutputs size={16} /> 最近产物</span>
            <button className="dash-more" onClick={() => onNavigate('outputs')}>内容库 →</button>
          </div>}>
          {recent.length === 0 && <div className="dash-empty">还没有产物，去对话生成第一条吧</div>}
          {recent.map((g) => (
            <div key={g.name} className="dash-output" onClick={() => onNavigate('outputs')}>
              <span className="dash-output-name">{g.meta?.title || g.name}</span>
              <span className="badge">{g.meta?.platform || (g.type === 'dir' ? `${g.fileCount ?? 0} 文件` : '单文件')}</span>
            </div>
          ))}
        </ContentPanel>

        <ContentPanel className="dash-card dash-card-wide" label="创作数据" header={<div className="dash-card-head">
            <span><IconAccounts size={16} /> 创作数据</span>
            <button className="dash-more" onClick={() => onNavigate('accounts')}>账号管理 →</button>
          </div>}>
          <p>选择平台，在 Ego 浏览器的官方后台查看数据。工作台不自动同步粉丝或作品指标。</p>
          {modeError && <div role="alert">{modeError} <button className="btn btn-sm" onClick={retry}>重试</button></div>}
          {!modeError && platforms.length === 0 && <p>暂无平台入口。</p>}
          <div className="ana-plats">
            {platforms.map((platform) => (
              <button className="chip" key={platform} disabled={!!opening} onClick={() => openAnalytics(platform)}>
                {opening === platform ? '正在打开…' : EGO_PLATFORMS.find((p) => p.platform === platform)?.name ?? platform}
              </button>
            ))}
          </div>
          {notice && <p role={notice.error ? 'alert' : 'status'}>{notice.message}</p>}
        </ContentPanel>
      </div>
    </div>
  );
}
