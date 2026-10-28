import { useEffect, useState } from "react";
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock3,
  FileText,
  ShieldAlert,
  Square,
  Terminal,
  Wrench,
  XCircle,
} from "lucide-react";
import type { ExecutionApproval, ExecutionTaskState } from "../services/lianxinApi";

type Decision = "accept" | "acceptForSession" | "decline";

type Props = {
  state: ExecutionTaskState | null;
  onApprove: (requestId: string, decision: Decision) => void;
  onStop: () => void;
  onSteer?: (text: string) => Promise<boolean>;
  deciding?: boolean;
};

const KIND_LABEL: Record<string, string> = {
  fileChange: "文件改动",
  "command+patch": "文件改动",
  command: "执行命令",
  permissions: "额外权限",
  other: "操作确认",
};

/** 每 1 秒自增，用来让运行中的耗时平滑跳动，而不必让父组件反复重渲染。 */
function useTick(active: boolean) {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setTick((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  return tick;
}

function DiffBlock({ approval }: { approval: ExecutionApproval }) {
  const files = approval.files ?? [];
  if (!files.length) return null;
  return (
    <div className="exec-files">
      {files.map((file) => (
        <div className="exec-file" key={`${approval.requestId}:${file.path}`}>
          <div className="exec-file-head">
            <FileText size={13} />
            <span className="exec-file-path">{file.path}</span>
            <span className="exec-file-kind">{file.kind}</span>
            <span className="exec-file-stat">
              <span className="exec-add">+{file.additions}</span>
              <span className="exec-del">-{file.deletions}</span>
            </span>
          </div>
          {file.diff ? (
            <pre className="exec-diff">
              {file.diff.split("\n").map((line, index) => {
                const trimmed = line.trimStart();
                const tone = trimmed.startsWith("+")
                  ? "add"
                  : trimmed.startsWith("-")
                    ? "del"
                    : trimmed.startsWith("@@")
                      ? "hunk"
                      : "context";
                return (
                  <span className={`exec-diff-line ${tone}`} key={`${file.path}:${index}`}>
                    {line || " "}
                    {"\n"}
                  </span>
                );
              })}
              {file.truncated ? (
                <span className="exec-diff-line hunk">
                  … 内容过长，预览已截断（原始 {file.diffBytes ?? 0} 字节）
                  {"\n"}
                </span>
              ) : null}
            </pre>
          ) : (
            <p className="exec-file-note">（本次没有可预览的文本 diff）</p>
          )}
        </div>
      ))}
    </div>
  );
}

function ApprovalCard({
  approval,
  onApprove,
  deciding,
}: {
  approval: ExecutionApproval;
  onApprove: (requestId: string, decision: Decision) => void;
  deciding?: boolean;
}) {
  const [showCommand, setShowCommand] = useState(false);
  const kind = approval.approvalKind ?? "other";
  const files = approval.files ?? [];
  const isFile = kind === "fileChange" || kind === "command+patch";
  return (
    <div className="exec-approval">
      <div className="exec-approval-head">
        <span className="exec-approval-icon">
          {isFile ? <FileText size={14} /> : <ShieldAlert size={14} />}
        </span>
        <div className="exec-approval-copy">
          <strong>{approval.title || "莲心在请求确认"}</strong>
          <span>
            {KIND_LABEL[kind] ?? "操作确认"}
            {isFile && files.length
              ? ` · ${files.length} 个文件 · +${approval.additions ?? 0}/-${approval.deletions ?? 0}`
              : ""}
          </span>
          {approval.reason ? <p>{approval.reason}</p> : null}
          {approval.cwd ? <p className="exec-path">{approval.cwd}</p> : null}
        </div>
      </div>
      <DiffBlock approval={approval} />
      {approval.command ? (
        <div className="exec-command">
          <button type="button" onClick={() => setShowCommand((value) => !value)}>
            {showCommand ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            <Terminal size={13} />
            {showCommand ? "收起命令原文" : "查看命令原文"}
          </button>
          {showCommand ? <pre>{approval.command}</pre> : null}
        </div>
      ) : null}
      <div className="exec-actions">
        <button
          type="button"
          className="exec-btn primary"
          disabled={deciding}
          onClick={() => onApprove(approval.requestId, "accept")}
        >
          <CheckCircle2 size={14} />
          允许这一次
        </button>
        {isFile ? (
          <button
            type="button"
            className="exec-btn"
            disabled={deciding}
            onClick={() => onApprove(approval.requestId, "acceptForSession")}
          >
            本轮都允许
          </button>
        ) : null}
        <button
          type="button"
          className="exec-btn danger"
          disabled={deciding}
          onClick={() => onApprove(approval.requestId, "decline")}
        >
          <XCircle size={14} />
          拒绝
        </button>
      </div>
    </div>
  );
}

export function ExecutionPanel(props: Props) {
  const task = props.state?.task;
  const controlKey = JSON.stringify([task?.sessionId, task?.taskId, task?.threadId, task?.turnId]);
  return <ExecutionPanelContent key={controlKey} {...props} />;
}

function ExecutionPanelContent({ state, onApprove, onStop, onSteer, deciding }: Props) {
  const [supplement, setSupplement] = useState("");
  const [sending, setSending] = useState(false);
  const running = Boolean(state?.running);
  const approvals = state?.pendingApprovals ?? [];
  const task = state?.task ?? null;
  useTick(running);

  if (!state) return null;
  // 结束后不在顶部留"已结束"卡：结论已经作为 execution_task 卡片落进会话流。
  if (!running && !approvals.length) return null;

  const elapsed = task?.startedAt
    ? Math.max(0, Date.now() / 1000 - task.startedAt)
    : task?.elapsedSeconds ?? 0;
  const maxRounds = state.limits?.max_rounds ?? 40;
  const rounds = task?.rounds ?? 0;
  const stopping = task?.state === "stopping";
  const ready = running && Boolean(task?.turnId) && !stopping;

  async function submitSupplement() {
    if (!onSteer || !supplement.trim() || sending || !ready) return;
    setSending(true);
    try {
      if (await onSteer(supplement.trim())) setSupplement("");
    } finally {
      setSending(false);
    }
  }

  return (
    <div className={`exec-panel${approvals.length ? " awaiting" : ""}`}>
      <div className="exec-head">
        <span className="exec-title">
          <Wrench size={14} />
          莲心的执行任务
        </span>
        <span className="exec-head-right">
          <span className={`exec-chip ${running ? "live" : "idle"}`}>
            {stopping ? "正在停止" : approvals.length ? "等待你确认" : ready ? "进行中" : "正在启动"}
          </span>
          {running ? (
            <button type="button" className="exec-stop" onClick={onStop} disabled={stopping || deciding}>
              <Square size={12} />
              停止
            </button>
          ) : null}
        </span>
      </div>

      <div className="exec-body">
        <p className="exec-goal">{task?.goal || "（无目标）"}</p>
        <div className="exec-meta">
          <span>
            <Clock3 size={12} />
            {elapsed.toFixed(1)}s
          </span>
          <span>
            动作 {rounds}/{maxRounds}
          </span>
          {state.sandbox === "read-only" ? <span className="exec-tag">只读沙箱</span> : null}
          {state.networkAccess ? <span className="exec-tag warn">已放行网络</span> : null}
        </div>
        {task?.cwd ? <p className="exec-path">{task.cwd}</p> : null}
        {task?.currentStep ? <p className="exec-current">当前：{task.currentStep}</p> : null}
        {task?.plan?.length ? <ol className="exec-plan" aria-label="任务计划">
          {task.plan.map((item, index) => <li key={`${index}-${item.step}`} data-status={item.status}>
            <span aria-label={item.status === "completed" ? "已完成" : item.status === "in_progress" ? "进行中" : "待处理"}>
              {item.status === "completed" ? "✓" : item.status === "in_progress" ? "◉" : "○"}
            </span>{item.step}
          </li>)}
        </ol> : null}
        {task?.lastApproval?.reason ? <p className="exec-control-status" role="status">{task.lastApproval.reason}</p> : null}
        {task?.stopReason ? <p className="exec-control-status" role="status">停止原因：{task.stopReason}</p> : null}
        {onSteer && running ? <form className="exec-supplement" onSubmit={(event) => {
          event.preventDefault();
          void submitSupplement();
        }}>
          <input aria-label="补充当前任务要求" placeholder="补充要求（仅影响当前执行任务）" value={supplement}
            maxLength={12000} disabled={!ready || sending} onChange={(event) => setSupplement(event.target.value)} />
          <button type="submit" disabled={!ready || sending || !supplement.trim()}>{sending ? "正在提交…" : "补充要求"}</button>
          <small>下方聊天仍是普通对话；停止任务请点右上角「停止」。</small>
        </form> : null}
      </div>

      {approvals.map((approval) => (
        <ApprovalCard
          key={approval.requestId}
          approval={approval}
          onApprove={onApprove}
          deciding={deciding}
        />
      ))}

    </div>
  );
}
