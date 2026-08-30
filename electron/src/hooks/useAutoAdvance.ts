/**
 * useAutoAdvance — 时光回放自动推进 hook（R5）
 *
 * 串行调 stepSession（不能并发，服务端有 409 防连点），4 档速度：
 * - 慢 2000ms
 * - 中 1000ms
 * - 快 300ms
 * - 极速 0（仅受 setTimeout 下限影响）
 *
 * 状态机：idle | running | paused | error | done
 * 错误：非 2xx 或 result.error 非空 → 立即停并标 error
 * 卸载：useRef 标记 abort，循环真正停止
 */

import { useState, useRef, useCallback, useEffect } from 'react';
import type { ReplaySession, StepResult } from '../services/replayService';
import { stepSession, getSession } from '../services/replayService';

export type AutoAdvanceSpeed = 'slow' | 'medium' | 'fast' | 'instant';
export type AutoAdvanceState = 'idle' | 'running' | 'paused' | 'error' | 'done';

const SPEED_MS: Record<AutoAdvanceSpeed, number> = {
    slow: 2000,
    medium: 1000,
    fast: 300,
    instant: 0,
};

export interface DailyRecord {
    trade_date: string;
    fill_count: number;
    /** 卖出成交笔数（轮换掉的持仓） */
    sell_count: number;
    /** 买入成交笔数（新换入的持仓） */
    buy_count: number;
    day_pnl: number;
    cum_pnl: number;
    rejected: number;
    error?: string;
    /** 当日收盘估值快照（供资产卡实时展示） */
    snapshot?: StepResult['snapshot'];
}

export interface UseAutoAdvanceOptions {
    /** 初始速度 */
    speed?: AutoAdvanceSpeed;
    /** 逐日结果回调 */
    onDay?: (record: DailyRecord) => void;
    /** 全部完成回调 */
    onDone?: (records: DailyRecord[]) => void;
    /** 错误回调（用于 UI 提示） */
    onError?: (err: Error) => void;
}

export interface UseAutoAdvanceResult {
    state: AutoAdvanceState;
    speed: AutoAdvanceSpeed;
    setSpeed: (s: AutoAdvanceSpeed) => void;
    records: DailyRecord[];
    progress: { done: number; total: number };
    errorMessage: string | null;
    start: (session: ReplaySession) => void;
    pause: () => void;
    resume: () => void;
    stop: () => void;
}

export function useAutoAdvance(opts: UseAutoAdvanceOptions = {}): UseAutoAdvanceResult {
    const { speed: initialSpeed = 'medium' } = opts;

    const [state, setState] = useState<AutoAdvanceState>('idle');
    const [speed, setSpeed] = useState<AutoAdvanceSpeed>(initialSpeed);
    const [records, setRecords] = useState<DailyRecord[]>([]);
    const [progress, setProgress] = useState({ done: 0, total: 0 });
    const [errorMessage, setErrorMessage] = useState<string | null>(null);

    // Refs for values that must be read inside the loop without causing re-subscription
    const sessionIdRef = useRef<string | null>(null);
    const abortRef = useRef(false);
    const pausedRef = useRef(false);
    const runningRef = useRef(false); // gate: only one tick chain at a time
    const recordsRef = useRef<DailyRecord[]>([]);
    const progressRef = useRef({ done: 0, total: 0 });
    const speedRef = useRef<AutoAdvanceSpeed>(initialSpeed);
    const onDayRef = useRef(opts.onDay);
    const onDoneRef = useRef(opts.onDone);
    const onErrorRef = useRef(opts.onError);

    // Keep callback refs in sync (stable identity, no re-subscription)
    onDayRef.current = opts.onDay;
    onDoneRef.current = opts.onDone;
    onErrorRef.current = opts.onError;
    speedRef.current = speed;

    const stopInternal = useCallback((finalState: AutoAdvanceState) => {
        abortRef.current = true;
        runningRef.current = false;
        setState(finalState);
    }, []);

    const start = useCallback((session: ReplaySession) => {
        if (runningRef.current) return;
        sessionIdRef.current = session.session_id;
        abortRef.current = false;
        pausedRef.current = false;
        runningRef.current = true;
        const initialProgress = {
            done: session.sessions_done,
            total: session.sessions_total,
        };
        recordsRef.current = [];
        progressRef.current = initialProgress;
        setRecords([]);
        setProgress(initialProgress);
        setErrorMessage(null);
        setState('running');
    }, []);

    const pause = useCallback(() => {
        if (!runningRef.current) return;
        pausedRef.current = true;
        setState('paused');
    }, []);

    const resume = useCallback(() => {
        if (state !== 'paused') return;
        pausedRef.current = false;
        setState('running');
    }, [state]);

    const stop = useCallback(() => {
        stopInternal('idle');
    }, [stopInternal]);

    // Auto-advance loop: runs as long as runningRef is true.
    // Only re-subscribes when state changes to 'running' — not on callback/speed changes.
    useEffect(() => {
        if (state !== 'running') return;
        if (!runningRef.current) return;

        const sessionId = sessionIdRef.current;
        if (!sessionId) return;

        let cancelled = false;

        const tick = async () => {
            if (cancelled || abortRef.current || !runningRef.current) return;
            if (pausedRef.current) return;

            try {
                const result: StepResult = await stepSession(sessionId);
                if (cancelled || abortRef.current) return;

                if (result.error) {
                    setErrorMessage(result.error);
                    runningRef.current = false;
                    setState('error');
                    onErrorRef.current?.(new Error(result.error));
                    return;
                }

                const record: DailyRecord = {
                    trade_date: result.trade_date,
                    fill_count: result.filled.length,
                    sell_count: result.filled.filter(f => f.side.toUpperCase() === 'SELL').length,
                    buy_count: result.filled.filter(f => f.side.toUpperCase() === 'BUY').length,
                    day_pnl: result.snapshot.day_pnl,
                    cum_pnl: result.snapshot.cum_pnl,
                    rejected: result.rejected.length,
                    snapshot: result.snapshot,
                };
                const newRecords = [...recordsRef.current, record];
                recordsRef.current = newRecords;
                const newProgress = {
                    done: progressRef.current.done + 1,
                    total: progressRef.current.total,
                };
                progressRef.current = newProgress;
                setRecords(newRecords);
                setProgress(newProgress);
                onDayRef.current?.(record);

                // Check if done via session refresh
                try {
                    const updated = await getSession(sessionId);
                    if (cancelled || abortRef.current) return;
                    if (updated.next_date === null) {
                        runningRef.current = false;
                        setState('done');
                        onDoneRef.current?.(newRecords);
                        return;
                    }
                } catch (err: unknown) {
                    if (cancelled || abortRef.current) return;
                    const msg = err instanceof Error ? err.message : '刷新会话失败';
                    setErrorMessage(msg);
                    runningRef.current = false;
                    setState('error');
                    onErrorRef.current?.(err instanceof Error ? err : new Error(msg));
                    return;
                }

                if (cancelled || abortRef.current || !runningRef.current) return;

                // Schedule next tick using current speed from ref (not stale closure)
                const delay = SPEED_MS[speedRef.current];
                if (delay <= 0) {
                    // Instant: use microtask to avoid stack overflow and allow abort checks
                    Promise.resolve().then(tick);
                } else {
                    setTimeout(tick, delay);
                }
            } catch (err: unknown) {
                if (cancelled || abortRef.current) return;
                const msg = err instanceof Error ? err.message : '推演失败';
                setErrorMessage(msg);
                runningRef.current = false;
                setState('error');
                onErrorRef.current?.(err instanceof Error ? err : new Error(msg));
            }
        };

        tick();

        return () => {
            cancelled = true;
        };
    }, [state]); // ONLY re-subscribe on state change — callbacks/speed use refs

    // Cleanup on unmount
    useEffect(() => {
        return () => {
            abortRef.current = true;
            runningRef.current = false;
        };
    }, []);

    return {
        state,
        speed,
        setSpeed,
        records,
        progress,
        errorMessage,
        start,
        pause,
        resume,
        stop,
    };
}
