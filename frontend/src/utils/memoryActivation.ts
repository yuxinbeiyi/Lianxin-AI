import type { ChatRequestMetadata } from "../services/lianxinApi";

export function createMemoryActivationToggle() {
  let armedScope: string | null = null;
  return {
    arm(scope: string) { armedScope = scope; },
    clear() { armedScope = null; },
    consume(scope: string): ChatRequestMetadata | undefined {
      const enabled = armedScope === scope;
      armedScope = null;
      return enabled ? { force_memory_activation: true } : undefined;
    },
  };
}

export function activationReason(reason: string) {
  const labels: Record<string, string> = {
    pending: "等待检查", disabled: "决策模型链路未启用", memory_not_allowed: "本轮无记忆读取权限",
    permission_denied: "权限不足", cloud_not_allowed: "未允许向 API 发送上下文",
    higher_priority_path: "优先处理明确任务、回忆或必要背景", cancelled: "本轮已取消",
    higher_priority_or_early_exit: "既有链路已处理，未进入可选联想", request_failed: "本轮回复失败",
    low_signal: "普通模式信号不足", cooldown: "普通模式冷却中", invalid_message: "不支持本轮输入",
    sensitive_context: "输入涉及敏感信息", budget_exhausted: "共享预算已耗尽",
    storage_unavailable: "记忆库暂不可用", retrieval_unavailable: "候选检索失败",
    no_candidates: "没有合适候选", no_safe_candidates: "没有通过本地初筛的候选",
    observation_only: "候选初筛完成", candidates_changed_or_repeated: "候选已变化或已注入过",
    candidate_changed: "选中事实已变化，停止注入", request_changed: "会话或轮次已变化",
    provider_changed: "决策通道已变更", route_decision_unavailable_or_prioritized: "原决策不可用或优先处理其他需求",
    model_suppressed: "模型建议不提及", low_relevance_or_fit: "关联或社交适配不足",
    unsupported_inference: "需要未经证实的推断", invalid_or_unavailable_decision: "决策失败或响应无效",
    association_unavailable: "联想链路不可用", passed: "已通过本地门控",
  };
  return labels[reason] ?? reason;
}
