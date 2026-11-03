import { BrainCircuit } from "lucide-react";
import type { MemoryActivationDebug } from "../services/lianxinApi";
import { activationReason } from "../utils/memoryActivation";

export function MemoryActivationCard({ data }: { data: MemoryActivationDebug }) {
  const finished = data.phase === "completed";
  const actions = { suppress: "抑制", prompt_only: "仅作背景提示", light_mention: "可自然轻提" };
  const retrieval = data.retrieval_status === "not_started" ? "未执行"
    : data.retrieval_status === "candidates_ready" ? `找到 ${data.candidate_ids.length} 条：${data.candidate_ids.map((value) => `#${value}`).join("、")}`
    : data.retrieval_status === "no_candidates" ? "0 条安全候选"
    : activationReason(data.retrieval_reason);
  const decision = !data.decision_called ? `未调用${finished ? ` · ${activationReason(data.reason)}` : "（尚未发起）"}`
    : `${data.provider === "local" ? "本地" : "API"} · ${data.model ?? "模型未知"} · ${
      data.decision_status === "running" ? "请求中" : data.decision_status === "failed" ? "调用失败" : "响应已校验"}`;
  return (
    <section className="tool-round memory-activation-card" aria-label="记忆联想调试">
      <div className="tool-round-heading">
        <span><BrainCircuit size={14} />{data.trigger === "forced" ? "瞬忆调试" : "记忆联想"}</span>
        <span>{!finished ? "检查中" : data.status === "injected" ? "已注入素材" : data.status === "failed" ? "已安全回退" : "未注入"}</span>
      </div>
      <dl className="memory-activation-details">
        <dt>触发</dt><dd>{data.trigger === "forced" ? "本条消息强制尝试" : "普通模式已尝试"} · {activationReason(data.reason)}</dd>
        <dt>候选检索</dt><dd>{retrieval}{data.channels.length > 0 && ` · ${data.retrieval_elapsed_ms.toFixed(1)}ms`}</dd>
        <dt>联想决策</dt><dd>{decision}</dd>
        <dt>建议 / 门控</dt><dd>{data.decision_status === "validated" ? finished ? actions[data.action] : "待本地门控" : "尚无有效建议"}（不强制表达）</dd>
        <dt>素材注入</dt><dd>{data.prompt_injected ? `已注入 #${data.candidate_id}` : finished ? "未注入" : "尚未注入"}</dd>
        <dt>回复采用</dt><dd>unknown · 不根据注入推断采用{data.reply_status === "failed" ? "；回复失败" : data.reply_status === "cancelled" ? "；本轮已取消" : ""}</dd>
      </dl>
    </section>
  );
}
