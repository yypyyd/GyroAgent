import { useEffect, useState } from 'react';
import { fetchEgoStatus, type EgoStatus } from './api';

export function useEgoStatus() {
  const [status, setStatus] = useState<EgoStatus | null>(null);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    fetchEgoStatus().then((result) => { if (active) setStatus(result); })
      .catch((err: unknown) => {
        if (active) setError(`无法读取工作台浏览器模式：${err instanceof Error ? err.message : '请检查连接'}`);
      });
    return () => { active = false; };
  }, [revision]);
  const retry = () => { setError(''); setStatus(null); setRevision((n) => n + 1); };
  return { status, error, retry };
}
