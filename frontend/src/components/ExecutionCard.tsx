import { useState } from "react";
import { CheckCircle2, Clock3, Play, Wrench, XCircle } from "lucide-react";
import { lianxinApi } from "../services/lianxinApi";
import type { ExecutionCardData } from "../types/ui";
import { continuationMode } from "../utils/executionControls";
import { showExecutionNotice } from "../utils/executionNotices";

const STATUS_LABEL: Record<string, string> = {
  completed: "已完成",
  failed: "没做成",
  interrupted: "被中断",
  stopped: "已停止",
};

type Tone = "ok" | "bad" | "warn";

function toneOf(status: string): Tone {
  if (status === "completed") return "ok";
  if (status === "failed") return "bad";
  return "warn";
}

/**
 * 执行任务卡片（会话流内的历史条目）。
 * 运行中的实时视图仍是顶部 ExecutionPanel；这里只负责"翻得回去"的那一份。
 */
export function ExecutionCard({
  data,
  content,
  sessionId,
}: {
  data?: ExecutionCardData;
  content: string;
  sessionId?: number;
}) {
  const [open, setOpen] = useState(false);
  const [resuming, setResuming] = useState(false);
  const [editing, setEditing] = useState(false);
  const [instruction, setInstruction] = useState("");
  const status = String(data?.status ?? "");
  const tone = toneOf(status);
  const label = STATUS_LABEL[status] ?? "已结束";
  const summary = String(data?.summary ?? "").trim();
  const elapsed = Number(data?.elapsed ?? 0);
  const rounds = Number(data?.rounds ?? 0);
  const mode = continuationMode(status);
  const canResume = Boolean(mode && data?.threadId && data?.taskId && sessionId);
  const actionLabel = mode === "revise" ? "继续修改" : mode === "retry" ? "补充条件后再试" : "接着做";

  async function handleResume() {
    if (!canResume || !mode || !sessionId || resuming) return;
    if (mode !== "resume" && (!editing || !instruction.trim())) {
      setEditing(true);
      return;
    }
    setResuming(true);
    try {
      const result = await lianxinApi.runtimeContinue(
        { sessionId, taskId: data!.taskId!, threadId: data!.threadId!, turnId: data?.turnId ?? "" },
        mode, instruction.trim(),
      );
      if (result.ok) {
        setEditing(false);
        setInstruction("");
        window.dispatchEvent(new Event("lianxin-runtime-changed"));
      }
      showExecutionNotice(result.ok ? "后续任务已受理，进度显示在执行面板。" : result.error || "未能继续执行。",
        "runtime_continue", sessionId, data?.taskId, result.ok ? "info" : "warning");
    } catch (error) {
      showExecutionNotice(`请求结果未确认，请先核对任务状态，勿重复提交：${error instanceof Error ? error.message : String(error)}`,
        "runtime_continue", sessionId, data?.taskId);
    } finally {
      setResuming(false);
    }
  }

  return (
    <section className={`exec-card ${tone}`}>
      <div className="exec-card-head">
        <span className="exec-card-title">
          <Wrench size={13} />
          莲心的执行任务
        </span>
        <span className={`exec-card-chip ${tone}`}>
          {tone === "ok" ? <CheckCircle2 size={11} /> : tone === "bad" ? <XCircle size={11} /> : null}
          {label}
        </span>
      </div>
      {data?.goal ? <p className="exec-card-goal">{data.goal}</p> : null}
      <div className="exec-card-meta">
        <span>
          <Clock3 size={11} />
          {elapsed.toFixed(1)}s
        </span>
        <span>动作 {rounds}</span>
        {data?.reason ? <span className="exec-card-reason">{data.reason}</span> : null}
      </div>
      {data?.approvalReason ? <p className="exec-control-status" role="status">审批记录：{data.approvalReason}</p> : null}
      {content ? <p className="exec-card-speech">{content}</p> : null}
      {canResume ? (
        <div className="exec-card-resume">
          <button type="button" onClick={() => void handleResume()} disabled={resuming}>
            <Play size={12} />
            {resuming ? "正在受理…" : editing ? "确认执行" : actionLabel}
          </button>
          {editing ? <>
            <textarea aria-label="后续执行要求" placeholder={mode === "retry" ? "请补充重试条件或修复线索" : "请描述本次需要修改的内容"}
              value={instruction} maxLength={12000} disabled={resuming} onChange={(event) => setInstruction(event.target.value)} />
            <button type="button" disabled={resuming} onClick={() => setEditing(false)}>取消</button>
          </> : null}
        </div>
      ) : null}
      {summary ? (
        <div className="exec-card-detail">
          <button type="button" onClick={() => setOpen((value) => !value)}>
            {open ? "收起引擎结论 ▴" : "查看引擎结论 ▾"}
          </button>
          {open ? <pre>{summary}</pre> : null}
        </div>
      ) : null}
    </section>
  );
}
