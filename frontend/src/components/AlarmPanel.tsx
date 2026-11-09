import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlarmClock, Check, Clock3, Play, RefreshCw, Save, Trash2, X } from "lucide-react";
import { lianxinApi, type AlarmPanelState } from "../services/lianxinApi";
import { confirmConfigAction } from "../utils/nativeConfig";
import "../styles/alarm-panel.css";

type Tab = "alarms" | "countdowns" | "reminders" | "todos" | "automation";
const tabs: Array<[Tab, string]> = [
  ["alarms", "定时闹钟"], ["countdowns", "倒计时"], ["reminders", "提醒"], ["todos", "待办"], ["automation", "自动化"],
];
const weekdays = [[0, "一"], [1, "二"], [2, "三"], [3, "四"], [4, "五"], [5, "六"], [6, "日"]] as const;
const repeatLabels: Record<string, string> = { once: "仅一次", daily: "每天", weekdays: "工作日", weekends: "周末" };
const ruleLabels: Record<string, string> = { once: "一次", daily: "每天", weekly: "每周", monthly: "每月" };
const scheduleLabels: Record<string, string> = { once: "一次", daily: "每天", weekly: "每周", monthly: "每月", interval: "间隔" };

export function AlarmPanel({ onClose }: { onClose: () => void }) {
  const mounted = useRef(true);
  const pending = useRef(false);
  const actionPending = useRef(false);
  const revision = useRef(0);
  const [editingTodo, setEditingTodo] = useState<string | null>(null);
  const [logTask, setLogTask] = useState("");
  const [tab, setTab] = useState<Tab>("alarms");
  const [state, setState] = useState<AlarmPanelState | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [todoSearch, setTodoSearch] = useState("");
  const [editingTask, setEditingTask] = useState<string | null>(null);
  const [taskDraft, setTaskDraft] = useState({ name: "", description: "", schedule_type: "daily", schedule_time: "08:00", interval_minutes: 60, missed_action: "ask", weekdays: [] as number[], day_of_month: 1 });
  const [alarmDraft, setAlarmDraft] = useState({ name: "", time_str: "08:00", repeat: "once" });
  const [countdownDraft, setCountdownDraft] = useState({ name: "", hours: 0, minutes: 5, seconds: 0 });
  const [reminderDraft, setReminderDraft] = useState({ name: "", time: "08:00", rule: "once", weekdays: [] as number[], day_of_month: 1, advance_minutes: 0, interval: 1 });
  const [todoDraft, setTodoDraft] = useState({ title: "", due_time: "", priority: "medium", description: "" });

  const refresh = useCallback(async () => {
    if (pending.current || actionPending.current) return;
    pending.current = true;
    const requestedRevision = revision.current;
    try {
      const result = await lianxinApi.alarmPanelState();
      if (mounted.current && requestedRevision === revision.current) setState(result);
    } catch (cause) {
      if (mounted.current && requestedRevision === revision.current) setError(cause instanceof Error ? cause.message : "读取状态失败");
    } finally { pending.current = false; }
  }, []);

  useEffect(() => {
    mounted.current = true;
    void refresh();
    const timer = window.setInterval(() => void refresh(), 1000);
    return () => { mounted.current = false; window.clearInterval(timer); };
  }, [refresh]);

  const act = useCallback(async (action: string, payload: Record<string, unknown> = {}) => {
    if (actionPending.current) return false;
    actionPending.current = true;
    revision.current += 1;
    setBusy(true);
    setError("");
    try {
      if (action.endsWith(".delete") || action === "auto_task.cleanup" || action === "auto_task.run" || action === "runtime.start") {
        const message = action === "runtime.start" ? "启动调度后，已到期的提醒和自动化任务可能立即触发并调用真实工具。确认接管？"
          : action === "auto_task.run" ? "立即执行此任务？它可能调用工具产生实际操作。" : "确认执行此操作？删除后不可恢复。";
        if (!await confirmConfigAction(message, "莲心 · 闹钟与提醒")) return false;
      }
      const result = await lianxinApi.alarmPanelAction(action, payload);
      if (mounted.current) { setState(result); setNotice("已完成"); }
      return true;
    } catch (cause) {
      if (mounted.current) setError(cause instanceof Error ? cause.message : "操作失败");
      return false;
    } finally {
      actionPending.current = false;
      if (mounted.current) setBusy(false);
    }
  }, []);

  const filteredTodos = useMemo(() => {
    const query = todoSearch.trim().toLocaleLowerCase();
    return (state?.todos ?? []).filter(item => !query || `${item.title} ${item.description ?? ""}`.toLocaleLowerCase().includes(query));
  }, [state?.todos, todoSearch]);

  const toggleDay = (selected: number[], day: number) => selected.includes(day) ? selected.filter(value => value !== day) : [...selected, day].sort();
  const field = (label: string, control: React.ReactNode) => <label className="alarm-field"><span>{label}</span>{control}</label>;
  const rowActions = (children: React.ReactNode) => <div className="alarm-actions">{children}</div>;
  const empty = (label: string) => <div className="alarm-empty">{label}</div>;
  const switcher = (checked: boolean, onChange: () => void, label: string) => <button className={`alarm-switch ${checked ? "is-on" : ""}`} aria-label={label} aria-pressed={checked} onClick={onChange}><i /></button>;
  const timeFields = (selected: number[], set: (next: number[]) => void) => <div className="alarm-weekdays">{weekdays.map(([day, label]) => <button type="button" key={day} className={selected.includes(day) ? "selected" : ""} onClick={() => set(toggleDay(selected, day))}>{label}</button>)}</div>;
  const submit = (event: React.FormEvent, action: string, payload: Record<string, unknown>, reset: () => void) => {
    event.preventDefault();
    void act(action, payload).then(success => { if (success) reset(); });
  };

  return <div className="alarm-overlay">
    <section className="alarm-shell" aria-label="闹钟与提醒">
      <header className="alarm-header">
        <div><span className="alarm-eyebrow">莲心 · 管理中心</span><h2><AlarmClock size={21} />闹钟与提醒</h2><p>统一管理闹钟、待办与自动化任务</p></div>
        <div className="alarm-header-actions"><span className={`alarm-runtime ${state?.scheduler.status === "running" ? "online" : "offline"}`}><i />{state?.scheduler.status === "running" ? "调度运行中" : state?.scheduler.message || "调度未连接"}</span><button className="alarm-icon-button" title="刷新" onClick={() => void refresh()}><RefreshCw size={16} /></button><button className="alarm-icon-button alarm-close" data-settings-close aria-label="关闭" onClick={onClose}><X size={18} /></button></div>
      </header>
      <nav className="alarm-tabs" aria-label="闹钟与提醒分类">{tabs.map(([id, label]) => <button key={id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}>{label}</button>)}</nav>
      <main className="alarm-content">
        {state?.scheduler.status !== "running" && <section className="alarm-card" role="status"><p>新版预览尚未接管调度。请先完全退出旧版 Qt 莲心，再点击启动；关闭面板不会停止已启动的调度；已有到期任务可能立即执行。</p><button className="alarm-primary" disabled={busy || !state} onClick={() => void act("runtime.start")}>启动新版调度</button></section>}
        <fieldset className="alarm-controls" disabled={busy || state?.scheduler.status !== "running"}>
        {tab === "alarms" && <>
          <section className="alarm-card"><h3>添加定时闹钟</h3><form className="alarm-form" onSubmit={event => submit(event, "alarm.add", alarmDraft, () => setAlarmDraft(current => ({ ...current, name: "" })))}>{field("名称", <input required maxLength={80} placeholder="例如：早会、吃药" value={alarmDraft.name} onChange={event => setAlarmDraft({ ...alarmDraft, name: event.target.value })} />)}{field("时间", <input required type="time" value={alarmDraft.time_str} onChange={event => setAlarmDraft({ ...alarmDraft, time_str: event.target.value })} />)}{field("重复", <select value={alarmDraft.repeat} onChange={event => setAlarmDraft({ ...alarmDraft, repeat: event.target.value })}>{Object.entries(repeatLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>)}<button className="alarm-primary" disabled={busy}><Save size={14} />添加闹钟</button></form></section>
          <section className="alarm-card"><h3>闹钟列表 <small>{state?.alarms.length ?? 0}</small></h3>{state?.alarms.length ? <div className="alarm-list">{state.alarms.map(item => <article className="alarm-row" key={item.id}><div className="alarm-time">{item.time_str}</div><div className="alarm-row-info"><strong>{item.name}</strong><span>{repeatLabels[item.repeat] ?? item.repeat}{item.last_fired_date ? ` · 上次触发 ${item.last_fired_date}` : ""}</span></div>{switcher(item.enabled, () => void act("alarm.toggle", { id: item.id }), `切换${item.name}`)}<button className="alarm-icon-button" title="贪睡 5 分钟" onClick={() => void act("alarm.snooze", { id: item.id })}><Clock3 size={15} /></button><button className="alarm-icon-button danger" title="删除" onClick={() => void act("alarm.delete", { id: item.id })}><Trash2 size={15} /></button></article>)}</div> : empty("还没有闹钟，添加一个开始吧。")}</section>
        </>}
        {tab === "countdowns" && <>
          <section className="alarm-card countdown-current"><h3>运行中的倒计时</h3>{state?.countdowns.length ? state.countdowns.map(item => <article className="countdown-row" key={item.id}><div><strong>{item.name}</strong><b>{formatDuration(item.remaining_seconds)}</b></div><button className="alarm-icon-button danger" title="取消倒计时" onClick={() => void act("countdown.cancel", { id: item.id })}><X size={16} /></button></article>) : empty("当前没有运行中的倒计时。")}</section>
          <section className="alarm-card"><h3>开始倒计时</h3><form className="alarm-form" onSubmit={event => { const total_seconds = Number(countdownDraft.hours) * 3600 + Number(countdownDraft.minutes) * 60 + Number(countdownDraft.seconds); submit(event, "countdown.start", { name: countdownDraft.name, total_seconds }, () => setCountdownDraft(current => ({ ...current, name: "" }))); }}>
            {field("名称", <input required maxLength={80} placeholder="例如：煮面、休息" value={countdownDraft.name} onChange={event => setCountdownDraft({ ...countdownDraft, name: event.target.value })} />)}
            {field("小时", <input type="number" min={0} max={99} value={countdownDraft.hours} onChange={event => setCountdownDraft({ ...countdownDraft, hours: Number(event.target.value) })} />)}
            {field("分钟", <input type="number" min={0} max={59} value={countdownDraft.minutes} onChange={event => setCountdownDraft({ ...countdownDraft, minutes: Number(event.target.value) })} />)}
            {field("秒", <input type="number" min={0} max={59} value={countdownDraft.seconds} onChange={event => setCountdownDraft({ ...countdownDraft, seconds: Number(event.target.value) })} />)}
            <button className="alarm-primary" disabled={busy}><Play size={14} />开始</button>
          </form></section>
        </>}
        {tab === "reminders" && <>
          <section className="alarm-card"><h3>提醒设置</h3><div className="alarm-setting-row"><div><strong>智能提醒文案</strong><span>使用莲心生成更自然的提醒表达</span></div>{switcher(Boolean(state?.smartReminder), () => void act("settings.update", { smartReminder: !state?.smartReminder }), "智能提醒文案")}</div>
            <form className="alarm-form" onSubmit={event => submit(event, "reminder.add", reminderDraft, () => setReminderDraft(current => ({ ...current, name: "" })))}>{field("提醒事项", <input required maxLength={120} placeholder="要提醒你的事情" value={reminderDraft.name} onChange={event => setReminderDraft({ ...reminderDraft, name: event.target.value })} />)}{field("时间", <input type="time" required value={reminderDraft.time} onChange={event => setReminderDraft({ ...reminderDraft, time: event.target.value })} />)}{field("重复", <select value={reminderDraft.rule} onChange={event => setReminderDraft({ ...reminderDraft, rule: event.target.value })}>{Object.entries(ruleLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>)}{reminderDraft.rule !== "once" && field("重复间隔（周期）", <input type="number" min={1} max={365} value={reminderDraft.interval} onChange={event => setReminderDraft({ ...reminderDraft, interval: Number(event.target.value) })} />)}{reminderDraft.rule === "weekly" && field("每周", timeFields(reminderDraft.weekdays, next => setReminderDraft({ ...reminderDraft, weekdays: next })))}{reminderDraft.rule === "monthly" && field("每月日期", <input type="number" min={1} max={31} value={reminderDraft.day_of_month} onChange={event => setReminderDraft({ ...reminderDraft, day_of_month: Number(event.target.value) })} />)}{field("提前提醒", <select value={reminderDraft.advance_minutes} onChange={event => setReminderDraft({ ...reminderDraft, advance_minutes: Number(event.target.value) })}>{[0, 5, 10, 15, 30, 60].map(value => <option key={value} value={value}>{value ? `提前 ${value} 分钟` : "准时提醒"}</option>)}</select>)}<button className="alarm-primary" disabled={busy}><Save size={14} />添加提醒</button></form>
          </section>
          <section className="alarm-card"><h3>提醒列表 <small>{state?.reminders.length ?? 0}</small></h3>{state?.reminders.length ? <div className="alarm-list">{state.reminders.map(item => <article className="alarm-row" key={item.id} onDoubleClick={event => { if (!(event.target as HTMLElement).closest("button")) void act("reminder.toggle", { id: item.id }); }}><div className="alarm-time">{item.time}</div><div className="alarm-row-info"><strong>{item.name}</strong><span>每 {item.interval ?? 1} 个周期 · {ruleLabels[item.rule] ?? item.rule}{item.rule === "weekly" && item.weekdays?.length ? ` · 周${item.weekdays.map(day => "一二三四五六日"[day]).join("、")}` : ""}{item.advance_minutes ? ` · 提前${item.advance_minutes}分钟` : ""}</span></div>{switcher(item.enabled, () => void act("reminder.toggle", { id: item.id }), `切换${item.name}`)}<button className="alarm-icon-button danger" title="删除" onClick={() => void act("reminder.delete", { id: item.id })}><Trash2 size={15} /></button></article>)}</div> : empty("还没有提醒事项。")}</section>
        </>}
        {tab === "todos" && <>
          <section className="alarm-card"><h3>待办偏好</h3><div className="alarm-setting-row"><div><strong>添加待办时询问我</strong><span>莲心自动创建待办前先向你确认</span></div>{switcher(Boolean(state?.todoAutoConfirm), () => void act("settings.update", { todoAutoConfirm: !state?.todoAutoConfirm }), "添加待办时询问我")}</div><div className="alarm-setting-row"><div><strong>允许自动提议待办/提醒</strong><span>根据聊天内容提出待办或提醒建议</span></div>{switcher(Boolean(state?.todoAutoSuggest), () => void act("settings.update", { todoAutoSuggest: !state?.todoAutoSuggest }), "允许自动提议待办和提醒")}</div></section>
          <section className="alarm-card"><h3>{editingTodo ? "编辑待办" : "添加待办"}</h3><form className="alarm-form" onSubmit={event => submit(event, editingTodo ? "todo.update" : "todo.add", { ...todoDraft, id: editingTodo, due_time: todoDraft.due_time || null }, () => { setEditingTodo(null); setTodoDraft({ title: "", due_time: "", priority: "medium", description: "" }); })}>{field("标题", <input required maxLength={200} placeholder="待办内容" value={todoDraft.title} onChange={event => setTodoDraft({ ...todoDraft, title: event.target.value })} />)}{field("截止时间", <input type="datetime-local" value={todoDraft.due_time} onChange={event => setTodoDraft({ ...todoDraft, due_time: event.target.value })} />)}{field("优先级", <select value={todoDraft.priority} onChange={event => setTodoDraft({ ...todoDraft, priority: event.target.value })}><option value="low">低</option><option value="medium">中</option><option value="high">高</option></select>)}<button className="alarm-primary" disabled={busy}><Save size={14} />{editingTodo ? "保存修改" : "添加"}</button>{field("备注", <input value={todoDraft.description} onChange={event => setTodoDraft({ ...todoDraft, description: event.target.value })} />)}{editingTodo && <button type="button" className="alarm-text-button" onClick={() => { setEditingTodo(null); setTodoDraft({ title: "", due_time: "", priority: "medium", description: "" }); }}>取消编辑</button>}</form></section>
          <section className="alarm-card"><div className="alarm-card-heading"><h3>待办清单 <small>{filteredTodos.length}</small></h3><input className="alarm-search" aria-label="搜索待办" placeholder="搜索待办" value={todoSearch} onChange={event => setTodoSearch(event.target.value)} /></div>{filteredTodos.length ? <div className="alarm-list">{filteredTodos.map(item => <article className={`alarm-row todo-row ${item.completed ? "completed" : ""}`} key={item.id}><button className="alarm-check" aria-label="完成/重开" onClick={() => void act("todo.toggle", { id: item.id })}>{item.completed ? <Check size={14} /> : null}</button><div className="alarm-row-info"><strong>{item.title}</strong><span>{item.due_time ? new Date(item.due_time).toLocaleString() : "无截止时间"} · {({ low: "低", medium: "中", high: "高" } as Record<string, string>)[item.priority] ?? item.priority}</span></div><button className="alarm-icon-button" title="编辑" onClick={() => { setEditingTodo(item.id); setTodoDraft({ title: item.title, due_time: item.due_time?.slice(0, 16) ?? "", priority: item.priority, description: item.description ?? "" }); }}><Save size={14} /></button><button className="alarm-icon-button danger" title="删除" onClick={() => void act("todo.delete", { id: item.id })}><Trash2 size={15} /></button></article>)}</div> : empty(todoSearch ? "没有匹配的待办。" : "目前没有待办事项。")}</section>
        </>}
        {tab === "automation" && <>
          <section className="alarm-card"><h3>{editingTask ? "编辑自动化任务" : "添加自动化任务"}</h3><form className="alarm-form automation-form" onSubmit={event => submit(event, editingTask ? "auto_task.update" : "auto_task.add", { ...taskDraft, id: editingTask ?? undefined }, () => { setEditingTask(null); setTaskDraft({ name: "", description: "", schedule_type: "daily", schedule_time: "08:00", interval_minutes: 60, missed_action: "ask", weekdays: [], day_of_month: 1 }); })}>{field("名称", <input required maxLength={100} value={taskDraft.name} onChange={event => setTaskDraft({ ...taskDraft, name: event.target.value })} />)}{field("说明", <input value={taskDraft.description} onChange={event => setTaskDraft({ ...taskDraft, description: event.target.value })} />)}{field("计划类型", <select value={taskDraft.schedule_type} onChange={event => setTaskDraft({ ...taskDraft, schedule_type: event.target.value })}>{Object.entries(scheduleLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>)}{taskDraft.schedule_type === "interval" ? field("间隔（分钟）", <input type="number" min={1} max={1440} value={taskDraft.interval_minutes} onChange={event => setTaskDraft({ ...taskDraft, interval_minutes: Number(event.target.value) })} />) : field("执行时间", <input type="time" value={taskDraft.schedule_time} onChange={event => setTaskDraft({ ...taskDraft, schedule_time: event.target.value })} />)}{taskDraft.schedule_type === "weekly" && field("每周", timeFields(taskDraft.weekdays, next => setTaskDraft({ ...taskDraft, weekdays: next })))}{taskDraft.schedule_type === "monthly" && field("每月日期", <input type="number" min={1} max={31} value={taskDraft.day_of_month} onChange={event => setTaskDraft({ ...taskDraft, day_of_month: Number(event.target.value) })} />)}{field("错过时", <select value={taskDraft.missed_action} onChange={event => setTaskDraft({ ...taskDraft, missed_action: event.target.value })}><option value="ask">询问我</option><option value="skip">跳过</option><option value="auto_execute">自动补做</option></select>)}{rowActions(<><button className="alarm-primary" disabled={busy}><Save size={14} />{editingTask ? "保存修改" : "添加任务"}</button>{editingTask && <button type="button" onClick={() => setEditingTask(null)}>取消编辑</button>}</>)}</form></section>
          <section className="alarm-card"><div className="alarm-card-heading"><h3>自动化任务 <small>{state?.autoTasks.length ?? 0}</small></h3><button className="alarm-text-button" onClick={() => void act("auto_task.cleanup")}>清理已完成</button></div>{state?.autoTasks.length ? <div className="alarm-list">{state.autoTasks.map(task => { const running = state.runningAutoTaskIds.includes(task.task_id); const paused = !task.enabled || task.status !== "active"; return <article className="alarm-task" key={task.task_id}><div className="alarm-task-main"><div className="alarm-task-title"><strong>{task.name}</strong><span className={`alarm-status ${running ? "running" : paused ? "paused" : "enabled"}`}>{running ? "执行中" : task.status || (task.enabled ? "启用" : "暂停")}</span></div><p>{task.description || "无描述"}</p><span>{scheduleLabels[task.schedule_type] ?? task.schedule_type}{task.schedule_type !== "interval" ? ` · ${task.schedule_time}` : ` · 每 ${task.interval_minutes} 分钟`}{task.next_run ? ` · 下次 ${task.next_run}` : ""}</span>{task.last_result && <small>上次结果：{task.last_result}</small>}</div>{rowActions(<><button title="编辑" onClick={() => { setEditingTask(task.task_id); setTaskDraft({ name: task.name, description: task.description, schedule_type: task.schedule_type, schedule_time: task.schedule_time || "08:00", interval_minutes: task.interval_minutes || 60, missed_action: task.missed_action, weekdays: task.weekdays ?? [], day_of_month: task.day_of_month ?? 1 }); }}>编辑</button><button title="暂停/恢复" onClick={() => void act(paused ? "auto_task.resume" : "auto_task.pause", { id: task.task_id })}>{paused ? "恢复" : "暂停"}</button><button title="立即执行" onClick={() => void act("auto_task.run", { id: task.task_id })}><Play size={13} />执行</button>{running && <button title="取消执行" onClick={() => void act("auto_task.cancel", { id: task.task_id })}>取消</button>}<button title="标记完成" onClick={() => void act("auto_task.complete", { id: task.task_id })}>完成</button><button className="danger" title="删除" onClick={() => void act("auto_task.delete", { id: task.task_id })}><Trash2 size={14} /></button></>)}</article>; })}</div> : empty("还没有自动化任务。")}</section>
          <section className="alarm-card"><h3>执行记录与关联工作流</h3><label className="alarm-field"><span>筛选任务</span><select value={logTask} onChange={event => setLogTask(event.target.value)}><option value="">全部任务</option>{state?.autoTasks.map(task => <option key={task.task_id} value={task.task_id}>{task.name}</option>)}</select></label>{state?.autoTaskLogs?.length ? <div className="alarm-logs">{state.autoTaskLogs.filter(log => !logTask || log.task_id === logTask).map((log, index) => <pre key={String(log.id ?? index)}>{JSON.stringify(log, null, 2)}</pre>)}</div> : empty("暂无执行记录。")}{logTask && <details><summary>关联工作流</summary><div className="alarm-logs"><pre>{JSON.stringify(state?.autoTaskWorkflows?.[logTask] ?? [], null, 2)}</pre></div></details>}</section>
        </>}
        </fieldset>
      </main>
      <footer className="alarm-footer"><p role={error ? "alert" : "status"}>{busy ? "正在处理…" : error || notice || state?.scheduler.message || "数据由莲心现有管理器与执行器提供"}</p><button onClick={() => void refresh()}><RefreshCw size={14} />刷新</button><button className="alarm-primary" onClick={onClose}>完成</button></footer>
    </section>
  </div>;
}

function formatDuration(value: number) {
  const seconds = Math.max(0, Math.floor(value));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  return [hours, minutes, remainder].map(part => String(part).padStart(2, "0")).join(":");
}
