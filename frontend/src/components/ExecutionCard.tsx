import { useState } from "react";
import { CheckCircle2, Clock3, Play, Wrench, XCircle } from "lucide-react";
import { lianxinApi } from "../services/lianxinApi";
import type { ExecutionCardData } from "../types/ui";

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
  const [resumeNotice, setResumeNotice] = useState("");
  const status = String(data?.status ?? "");
  const tone = toneOf(status);
  const label = STATUS_LABEL[status] ?? "已结束";
  const summary = String(data?.summary ?? "").trim();
  const elapsed = Number(data?.elapsed ?? 0);
  const rounds = Number(data?.rounds ?? 0);
  const canResume = status === "interrupted" && Boolean(data?.threadId);

  async function handleResume() {
    if (!canResume || resuming) return;
    setResuming(true);
    setResumeNotice("");
    try {
      const result = await lianxinApi.runtimeStart(
        String(data?.goal || "接着上次没做完的任务继续"),
        {
          sessionId,
          resumeThreadId: data?.threadId,
        },
      );
      setResumeNotice(
        result.ok
          ? "已经接着上次的进度继续跑了，进度在执行面板里。"
          : `没能继续：${result.error || "未知原因"}`,
      );
    } catch (error) {
      setResumeNotice(
        `没能继续：${error instanceof Error ? error.message : String(error)}`,
      );
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
      {content ? <p className="exec-card-speech">{content}</p> : null}
      {canResume ? (
        <div className="exec-card-resume">
          <button type="button" onClick={() => void handleResume()} disabled={resuming}>
            <Play size={12} />
            {resuming ? "正在接着做…" : "接着做"}
          </button>
          {resumeNotice ? <span>{resumeNotice}</span> : null}
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
