import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { lianxinApi, type BilibiliInteractionState, type BilibiliSurfState, type BilibiliHistoryRecord } from "../services/lianxinApi";

import { confirmProactiveAction } from "../utils/proactiveConfirmation";

function editable(state: BilibiliInteractionState) {
  const settings = state.settings;
  return { mode: settings.mode, userUid: settings.userUid, testVideo: settings.testVideo, allowOtherReplies: settings.allowOtherReplies, autoCommentEnabled: settings.autoCommentEnabled, autoReplyEnabled: settings.autoReplyEnabled ?? false, mentionReplyEnabled: settings.mentionReplyEnabled ?? false, paused: settings.paused };
}
export type BilibiliPanelHandle = { applySettings: () => Promise<boolean>; settingsDirty: boolean };
export const ProactiveBilibiliPanel = forwardRef<BilibiliPanelHandle, { disabled?: boolean; onDirtyChange: (dirty: boolean) => void; onBusyChange: (busy: boolean) => void }>(function ProactiveBilibiliPanel({ disabled, onDirtyChange, onBusyChange }, ref) {
  const [bilibili, setBilibili] = useState<BilibiliInteractionState | null>(null);
  const [bilibiliDraft, setBilibiliDraft] = useState({
    mode: "read_only" as "read_only" | "test",
    userUid: "",
    testVideo: "",
    allowOtherReplies: false,
    autoCommentEnabled: false,
    autoReplyEnabled: false,
    mentionReplyEnabled: false,
    paused: false,
  });
  const [bilibiliSaving, setBilibiliSaving] = useState(false);
  const [bilibiliNotice, setBilibiliNotice] = useState("");
  const [uidLookup, setUidLookup] = useState<{ uid: string; name: string } | null>(null);
  const [videoLookup, setVideoLookup] = useState<{ bvid: string; title: string; author: string; commentOpen: boolean } | null>(null);
  const [bilibiliSurf, setBilibiliSurf] = useState<BilibiliSurfState | null>(null);
  const [surfTagDrafts, setSurfTagDrafts] = useState<Record<string, string>>({});
  const [newSurfTag, setNewSurfTag] = useState("");
  const [surfNotice, setSurfNotice] = useState("");
  const [surfSaving, setSurfSaving] = useState(false);
  const [bilibiliLoginKey, setBilibiliLoginKey] = useState("");
  const [bilibiliLoginQr, setBilibiliLoginQr] = useState("");
  const [bilibiliDraftText, setBilibiliDraftText] = useState("");
  const [bilibiliDraftBusy, setBilibiliDraftBusy] = useState(false);
  const [history, setHistory] = useState<BilibiliHistoryRecord[]>([]);
  const [filter, setFilter] = useState("");
  const [intentDrafts, setIntentDrafts] = useState<Record<string, string>>({});
  const [extraBusy, setExtraBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const confirmingRef = useRef(false);
  const settingsDirty = !!bilibili && JSON.stringify(bilibiliDraft) !== JSON.stringify(editable(bilibili));
  const tagDirty = !!newSurfTag.trim() || !!bilibiliSurf?.tags.some(tag => (surfTagDrafts[tag.keyword] ?? tag.search_query) !== tag.search_query || (intentDrafts[tag.keyword] ?? tag.intent_note) !== tag.intent_note);
  const dirty = settingsDirty || tagDirty || bilibiliDraftText !== (bilibili?.draft?.text || "");
  const busy = bilibiliSaving || surfSaving || bilibiliDraftBusy || extraBusy || confirming;
  useEffect(() => { onDirtyChange(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => { onBusyChange(busy); }, [busy, onBusyChange]);
  useImperativeHandle(ref, () => ({ applySettings: () => settingsDirty ? saveBilibiliSettings(false) : Promise.resolve(true), settingsDirty }), [settingsDirty, bilibiliDraft]);
  const confirmAction = async (text: string) => {
    if (confirmingRef.current) return false;
    confirmingRef.current = true; setConfirming(true);
    try { return await confirmProactiveAction(text); }
    catch (error) { setBilibiliNotice(error instanceof Error ? error.message : "确认框不可用；操作未执行"); return false; }
    finally { confirmingRef.current = false; setConfirming(false); }
  };
  const extra = async (operation: () => Promise<void>) => {
    if (bilibiliSaving || surfSaving || bilibiliDraftBusy || extraBusy || confirmingRef.current) return;
    setExtraBusy(true);
    try { await operation(); } catch (error) { setBilibiliNotice(error instanceof Error ? error.message : "操作失败"); }
    finally { setExtraBusy(false); }
  };
  const refreshHistory = async () => { setHistory((await lianxinApi.bilibiliHistory(filter)).items); };
  const deleteTag = async (keyword: string) => {
    if (!await confirmAction(`确定删除兴趣标签「${keyword}」？`)) return;
    await extra(async () => { await lianxinApi.deleteBilibiliTag(keyword); await refreshBilibiliSurf(); });
  };
  const checkAccount = async () => extra(async () => {
    const result = await lianxinApi.checkBilibiliAuthorization();
    if (result.state) setBilibili(result.state);
    setBilibiliNotice(result.ok ? "授权检查通过" : result.error || "授权检查未通过");
  });
  const reloadInteraction = async () => {
    if (dirty && !await confirmAction("重新读取互动状态将放弃未应用的互动设置及正文，继续？兴趣标签草稿仍保留。")) return;
    void extra(async () => { const state = await lianxinApi.bilibiliInteractionState(); setBilibili(state); setBilibiliDraft(editable(state)); setBilibiliDraftText(state.draft?.text || ""); });
  };
  useEffect(() => {
    void lianxinApi
      .bilibiliInteractionState()
      .then((state) => {
        setBilibili(state);
        setBilibiliDraft({
          mode: state.settings.mode,
          userUid: state.settings.userUid,
          testVideo: state.settings.testVideo,
          allowOtherReplies: state.settings.allowOtherReplies,
          autoCommentEnabled: state.settings.autoCommentEnabled,
          autoReplyEnabled: state.settings.autoReplyEnabled ?? false,
          mentionReplyEnabled: state.settings.mentionReplyEnabled ?? false,
          paused: state.settings.paused,
        });
        setBilibiliDraftText(state.draft?.text || "");
      })
      .catch(() => setBilibiliNotice("无法读取 B 站互动设置。"));
  }, []);
  const refreshBilibiliSurf = async () => {
    try {
      const state = await lianxinApi.bilibiliSurfState();
      setBilibiliSurf(state);
      setSurfTagDrafts((current) => Object.fromEntries(state.tags.map((tag) => [tag.keyword, current[tag.keyword] ?? tag.search_query ?? ""])));
    } catch {
      setSurfNotice("兴趣标签暂时无法读取。");
    }
  };
  useEffect(() => {
    void refreshBilibiliSurf();
  }, []);
  const saveSurfTag = async (payload: { keyword: string; search_query?: string; status?: "active" | "paused"; intent_note?: string }) => {
    setSurfSaving(true);
    setSurfNotice("");
    try {
      const state = await lianxinApi.saveBilibiliSurfTag(payload);
      if (!state.ok) {
        setSurfNotice(state.error || "兴趣标签未保存。");
        return;
      }
      setBilibiliSurf(state);
      setSurfTagDrafts((current) => Object.fromEntries(state.tags.map((tag) => [tag.keyword,
        tag.keyword === payload.keyword && payload.search_query !== undefined ? tag.search_query || "" : current[tag.keyword] ?? tag.search_query ?? "",
      ])));
      if (payload.search_query === undefined && payload.status === undefined) setNewSurfTag("");
      if (payload.intent_note !== undefined) setIntentDrafts(current => ({ ...current, [payload.keyword]: state.tags.find(tag => tag.keyword === payload.keyword)?.intent_note || "" }));
      setSurfNotice("兴趣标签已保存；留空搜索词时使用标签原词。");
    } catch {
      setSurfNotice("兴趣标签保存失败，请检查莲心服务。");
    } finally {
      setSurfSaving(false);
    }
  };
  const saveBilibiliSettings = async (closeAfterSave = false) => {
    if (!bilibili) { setBilibiliNotice("尚未读到互动设置，请先重新读取"); return false; }
    setBilibiliSaving(true);
    setBilibiliNotice("");
    try {
      const result = await lianxinApi.saveBilibiliInteractionSettings(bilibiliDraft);
      if (!result.ok || !result.state) {
        setBilibiliNotice(result.error || "设置未保存。");
        return false;
      }
      setBilibili(result.state);
      setBilibiliDraft(editable(result.state));
      if (closeAfterSave) {
        return true;
      }
      setBilibiliNotice(result.state.settings.userUidConfirmed
        ? result.state.settings.paused
          ? "设置已应用。互动已暂停，不会发送评论、续聊或 @ 召唤回复。"
          : "设置已应用。自动评论、评论续聊和 @ 召唤各自按开关运行；可继续调试。"
        : "设置已保存。UID 尚未核验，互动仍保持关闭。");
      return true;
    } catch {
      setBilibiliNotice("设置保存失败，请确认莲心服务正在运行。");
      return false;
    } finally {
      setBilibiliSaving(false);
    }
  };
  const lookupBilibiliUid = async () => {
    setBilibiliSaving(true);
    setBilibiliNotice("");
    setUidLookup(null);
    try {
      const result = await lianxinApi.verifyBilibiliUserUid(bilibiliDraft.userUid);
      if (result.ok && result.uid && result.name) {
        setBilibiliDraft((current) => ({ ...current, userUid: result.uid! }));
        setUidLookup({ uid: result.uid, name: result.name });
      }
      else setBilibiliNotice(result.error || "无法核验 UID。");
    } catch {
      setBilibiliNotice("UID 查询失败，请检查网络后重试。");
    } finally {
      setBilibiliSaving(false);
    }
  };
  const confirmBilibiliUid = async () => {
    if (!uidLookup || uidLookup.uid !== bilibiliDraft.userUid.trim()) return;
    setBilibiliSaving(true);
    try {
      const saved = await lianxinApi.saveBilibiliInteractionSettings({ userUid: uidLookup.uid });
      if (!saved.ok) {
        setBilibiliNotice(saved.error || "UID 未保存，无法确认。");
        return;
      }
      const result = await lianxinApi.confirmBilibiliUserUid(uidLookup.uid);
      if (!result.ok || !result.state) {
        setBilibiliNotice(result.error || "UID 确认失败。");
        return;
      }
      setBilibili(result.state);
      setBilibiliNotice(`已确认 UID ${uidLookup.uid} 对应账号：${uidLookup.name}。这只是资料匹配确认，不是账号所有权认证。`);
    } catch {
      setBilibiliNotice("UID 确认失败，请重试。");
    } finally {
      setBilibiliSaving(false);
    }
  };
  const inspectBilibiliVideo = async () => {
    setBilibiliSaving(true);
    setVideoLookup(null);
    try {
      const result = await lianxinApi.inspectBilibiliTestVideo(bilibiliDraft.testVideo);
      if (result.ok && result.video) setVideoLookup(result.video);
      else setBilibiliNotice(result.error || "无法读取测试视频信息。");
    } catch {
      setBilibiliNotice("测试视频查询失败，请检查网络后重试。");
    } finally {
      setBilibiliSaving(false);
    }
  };
  const recheckBilibiliComment = async (rpid = "") => {
    if (bilibiliSaving) return;
    setBilibiliSaving(true);
    try {
      const result = await lianxinApi.recheckBilibiliComment(rpid);
      if (result.state) setBilibili(result.state);
      setBilibiliNotice(`${result.message || result.error || "复核未完成。"}\n${result.diagnostic || ""}`);
    } catch {
      setBilibiliNotice("只读复核请求失败，没有发送或重发评论。");
    } finally {
      setBilibiliSaving(false);
    }
  };
  const requestBilibiliTest = async () => {
    if (!bilibili || bilibiliSaving || bilibiliDraftBusy) return;
    const draft = bilibili.draft;
    if (bilibiliDraft.testVideo !== bilibili.settings.testVideo) { setBilibiliNotice("测试视频已修改，请先应用互动设置"); return; }
    if (draft && draft.bvid !== bilibili.settings.testBvid) { setBilibiliNotice("测试目标已变化，请先保存设置并重新保存正文"); return; }
    if (!draft || bilibiliDraftText.trim() !== draft.text) {
      setBilibiliNotice("请先在评论正文框中输入内容，再点击“保存当前正文”；不需要先生成草稿。");
      return;
    }
    if (!await confirmAction(`使用账号：${bilibili.account.name}（${bilibili.account.uid}）\n测试视频：${draft.bvid}\n提及 UID：${draft.mentionUid}\n\n${draft.text}\n\n确定公开发布到 B 站吗？`)) return;
    setBilibiliSaving(true);
    try {
      const result = await lianxinApi.requestBilibiliTestComment(bilibiliDraft.testVideo, draft.id, true);
      if (result.state) setBilibili(result.state);
      setBilibiliNotice(result.message || result.error || result.blockers?.join(" ") || "请核对发送结果。");
    } catch {
      setBilibiliNotice("测试请求失败；未确认发送状态，请勿重复操作。 ");
    } finally {
      setBilibiliSaving(false);
    }
  };
  const startBilibiliLogin = async () => {
    setBilibiliSaving(true);
    setBilibiliNotice("");
    try {
      const result = await lianxinApi.startBilibiliLogin();
      if (!result.ok || !result.key) {
        setBilibiliNotice(result.error || "无法生成 B 站登录二维码。");
        return;
      }
      setBilibiliLoginKey(result.key);
      setBilibiliLoginQr(result.qrDataUrl || "");
      setBilibiliNotice(result.qrDataUrl ? "请用 B 站 App 扫码；扫码后点击“检查扫码状态”。" : "二维码图片生成失败，请稍后重试。");
    } catch {
      setBilibiliNotice("登录二维码请求失败，请确认莲心服务正在运行。");
    } finally {
      setBilibiliSaving(false);
    }
  };
  const pollBilibiliLogin = async () => {
    if (!bilibiliLoginKey || bilibiliSaving) return;
    setBilibiliSaving(true);
    try {
      const result = await lianxinApi.pollBilibiliLogin(bilibiliLoginKey);
      if (result.state === "connected") {
        const state = await lianxinApi.bilibiliInteractionState();
        setBilibili(state);
        setBilibiliLoginKey("");
        setBilibiliLoginQr("");
        setBilibiliNotice(`已连接互动账号：${result.account?.name || "B 站账号"}。凭据保存在本机安全存储中。`);
      } else if (result.state === "waiting") {
        setBilibiliNotice("二维码仍在等待扫码。");
      } else if (result.state === "scanned") {
        setBilibiliNotice("已扫码，请在手机上确认登录。");
      } else {
        setBilibiliNotice(result.error || "登录尚未完成，请重新扫码。");
        if (result.state === "expired" || result.state === "no_session") {
          setBilibiliLoginKey("");
          setBilibiliLoginQr("");
        }
      }
    } catch {
      setBilibiliNotice("检查登录状态失败，请稍后重试。");
    } finally {
      setBilibiliSaving(false);
    }
  };
  const logoutBilibili = async () => {
    if (!await confirmAction("确定解除本机 B站互动账号授权？此操作不会删除浏览历史。")) return;
    setBilibiliSaving(true);
    try {
      const result = await lianxinApi.logoutBilibili();
      if (result.ok && result.state) {
        setBilibili(result.state);
        setBilibiliLoginKey("");
        setBilibiliLoginQr("");
        setBilibiliNotice("已删除本机保存的 B 站互动授权。");
      } else setBilibiliNotice(result.error || "解除授权失败。");
    } catch {
      setBilibiliNotice("解除授权失败，请确认莲心服务正在运行。");
    } finally {
      setBilibiliSaving(false);
    }
  };
  const generateBilibiliDraft = async (text?: string) => {
    if (!bilibiliDraft.testVideo.trim() || bilibiliDraftBusy || bilibiliSaving) return;
    if (text !== undefined && !text.trim()) {
      setBilibiliNotice("请先输入要保存的评论正文。");
      return;
    }
    if (text === undefined && bilibiliDraftText.trim()
      && !await confirmAction("生成新草稿会替换正文框中的内容。要保留自己写的文字，请取消并点击“保存当前正文”。\n\n确定替换吗？")) return;
    setBilibiliDraftBusy(true);
    setBilibiliNotice("");
    try {
      const result = await lianxinApi.createBilibiliCommentDraft(bilibiliDraft.testVideo, text ?? "");
      if (result.ok && result.draft) {
        setBilibili(result.state);
        setBilibiliDraftText(result.draft.text);
        setBilibiliNotice(text === undefined
          ? "草稿已生成；可以直接修改正文，修改后请点击“保存当前正文”。当前不会发送到 B 站。"
          : "当前正文已保存；请核对保存后的全文和 @，再确认发送。没有自动改写或发送。");
      } else {
        setBilibiliNotice(result.error || result.blockers?.join(" ") || "草稿未生成。");
      }
    } catch {
      setBilibiliNotice("草稿生成或正文保存失败，请确认莲心服务正在运行。");
    } finally {
      setBilibiliDraftBusy(false);
    }
  };
  const clearBilibiliDraft = async () => {
    if (bilibiliDraftBusy || bilibiliSaving) return;
    if (!await confirmAction("确定清除评论草稿和正文框内容？不会删除已经发布的评论。")) return;
    setBilibiliDraftBusy(true);
    try {
      const result = await lianxinApi.clearBilibiliCommentDraft();
      setBilibili(result.state);
      setBilibiliDraftText("");
      setBilibiliNotice("草稿已清除。");
    } catch {
      setBilibiliNotice("草稿清除失败，请稍后重试。");
    } finally {
      setBilibiliDraftBusy(false);
    }
  };
  if (!bilibili) return <section className="global-settings-card"><h3>B站互动设置</h3><p role="status">{bilibiliNotice || "正在读取互动设置…"}</p><div className="proactive-actions"><button disabled={busy || disabled} onClick={reloadInteraction}>重新读取互动状态</button></div></section>;
  return <fieldset className="proactive-fieldset" disabled={busy || disabled}>
      <section className="global-settings-card bili-interaction-panel" aria-labelledby="bili-interaction-title">
        <h3 id="bili-interaction-title">B 站冲浪与互动</h3>
        <div className="proactive-actions"><button onClick={reloadInteraction}>重新读取互动状态</button></div>
        <p className="muted-copy">
          每轮按一个兴趣标签搜索最多 3 个视频，按播放量选 1 个后读取简介、字幕与评论，结合当前人格决定是否评论。固定测试视频不参与自动冲浪。
        </p>
        <details className="bili-surf-settings">
          <summary>兴趣标签与最近一轮</summary>
          <div className="bili-surf-tag-list">
            {(bilibiliSurf?.tags || []).map((tag) => (
              <div className="bili-surf-tag" key={tag.keyword}>
                <div><strong>{tag.keyword}</strong><small>来源：{tag.source} · 基础 {tag.base_score} + 反馈 {tag.boost_score}</small><span>{tag.status === "paused" ? "已暂停" : tag.search_query ? `按“${tag.search_query}”搜索` : "按标签原词搜索"}</span></div>
                <input
                  aria-label={`${tag.keyword}的搜索词`}
                  maxLength={80}
                  disabled={surfSaving}
                  value={surfTagDrafts[tag.keyword] ?? ""}
                  placeholder={tag.keyword}
                  onChange={(event) => setSurfTagDrafts((current) => ({ ...current, [tag.keyword]: event.target.value }))}
                />
                <input aria-label={`${tag.keyword}的意图备注`} maxLength={200} value={intentDrafts[tag.keyword] ?? tag.intent_note ?? ""} placeholder="意图备注（可选）" onChange={event => setIntentDrafts(current => ({ ...current, [tag.keyword]: event.target.value }))} />
                <button className="secondary-button" disabled={surfSaving} onClick={() => void saveSurfTag({ keyword: tag.keyword, search_query: surfTagDrafts[tag.keyword] ?? "", intent_note: intentDrafts[tag.keyword] ?? tag.intent_note ?? "" })}>保存</button>
                <button className="secondary-button" onClick={() => void deleteTag(tag.keyword)}>删除</button>
                <button className="secondary-button" disabled={surfSaving} onClick={() => void saveSurfTag({ keyword: tag.keyword, status: tag.status === "paused" ? "active" : "paused" })}>{tag.status === "paused" ? "恢复" : "暂停"}</button>
              </div>
            ))}
          </div>
          <div className="bili-surf-add">
            <input aria-label="新增兴趣标签" disabled={surfSaving} maxLength={40} value={newSurfTag} placeholder="添加兴趣标签" onChange={(event) => setNewSurfTag(event.target.value)} />
            <button className="secondary-button" disabled={surfSaving || !newSurfTag.trim()} onClick={() => void saveSurfTag({ keyword: newSurfTag.trim() })}>添加</button>
          </div>
          {surfNotice && <span className="bili-surf-notice">{surfNotice}</span>}
          <div className="bili-surf-latest">
            <strong>最近一轮</strong>
            <button className="secondary-button" disabled={surfSaving} onClick={() => void refreshBilibiliSurf()}>刷新结果（只读）</button>
            {bilibiliSurf?.latest ? (
              <span>{bilibiliSurf.latest.summary} · 标签「{bilibiliSurf.latest.tag || "未选标签"}」/ 查询「{bilibiliSurf.latest.query || "无"}」 · 返回 {bilibiliSurf.latest.returned_count} 条，详情 {bilibiliSurf.latest.enriched_count} 条 · {bilibiliSurf.latest.send_status === "sent_verified" ? "评论已提交并回读匹配" : bilibiliSurf.latest.send_status === "sent_unverified" ? "评论已提交，回读未确认" : bilibiliSurf.latest.send_status === "unknown" ? "发送状态未知，请勿重试" : bilibiliSurf.latest.send_status === "read_only" ? "只读：未发评论" : bilibiliSurf.latest.send_status === "comment_skipped" ? "评论生成未通过，未发送" : bilibiliSurf.latest.send_status === "not_attempted" ? "未进入发送阶段" : `发送状态：${bilibiliSurf.latest.send_status}`} · {new Date(bilibiliSurf.latest.timestamp * 1000).toLocaleString()}</span>
            ) : <span>还没有冲浪诊断记录。</span>}
            {bilibiliSurf?.latest?.share_status && (
              <details>
                <summary>本轮调试详情</summary>
                <p className="muted-copy">聊天只展示一条自然分享。{bilibiliSurf.latest.share_status === "generated" ? "分享文案正常生成。" : "分享文案使用本地短句兜底，未因此重发评论。"}</p>
                {bilibiliSurf.latest.comment_receipt && (
                  <>
                    <p>评论正文：{bilibiliSurf.latest.comment_receipt.text}</p>
                    <p>评论 ID：{bilibiliSurf.latest.comment_receipt.rpid}</p>
                    <p className="muted-copy">评论已提交；主号可见性与 @ 通知仍需单独确认，接口返回或回读匹配不代表通知已送达。</p>
                  </>
                )}
              </details>
            )}
          </div>
        </details>
        <div className="bili-interaction-grid">
          <label>
            用户的 B 站 UID
            <input
              inputMode="numeric"
              autoComplete="off"
              value={bilibiliDraft.userUid}
              onChange={(event) =>
                setBilibiliDraft((current) => ({ ...current, userUid: event.target.value }))
              }
              placeholder="填写主账号 UID"
            />
          </label>
        </div>
        <div className="bili-interaction-row">
          <div>
            <strong>自动评论并 @ 主号</strong>
            <span>开启并保存后，莲心按兴趣搜索并结合证据与当前人格决定是否评论，每轮最多一条；接口受理不保证大号可见。默认关闭。</span>
          </div>
          <button
            className={`toggle ${bilibiliDraft.autoCommentEnabled ? "is-on" : ""}`}
            onClick={() => setBilibiliDraft((current) => ({ ...current, autoCommentEnabled: !current.autoCommentEnabled }))}
            aria-label="自动评论并 @ 主号"
            aria-pressed={bilibiliDraft.autoCommentEnabled}
          ><span /></button>
        </div>
        <div className="bili-interaction-actions">
          <button className="secondary-button" disabled={!bilibiliDraft.userUid.trim() || bilibiliSaving} onClick={() => void lookupBilibiliUid()}>
            查询 UID 昵称
          </button>
          {uidLookup && uidLookup.uid === bilibiliDraft.userUid.trim() && (
            <button className="secondary-button" disabled={bilibiliSaving} onClick={() => void confirmBilibiliUid()}>
              确认是 {uidLookup.name}
            </button>
          )}
        </div>
        {(bilibili?.settings.userUidConfirmed || videoLookup) && (
          <div className="bili-capability-status">
            {bilibili?.settings.userUidConfirmed && (
              <span>已记录用户资料：{bilibili.settings.userName}（UID {bilibili.settings.userUid}）。资料匹配不代表账号所有权已验证。</span>
            )}
            {videoLookup && (
              <span>{videoLookup.title} · {videoLookup.author} · {videoLookup.bvid} · {videoLookup.commentOpen ? "评论开放" : "评论关闭"}</span>
            )}
          </div>
        )}
        <div className="bili-interaction-row">
          <div>
            <strong>互动账号</strong>
            <span>
              扫码只在本机完成，凭据写入 Windows 安全凭据存储，不会显示或写入普通配置文件。
            </span>
          </div>
          <div className="bili-interaction-actions">
            <button className="secondary-button" onClick={() => void checkAccount()}>检查授权</button>
            {bilibili?.account.connected ? (
              <>
                <span className="bili-status">已连接：{bilibili.account.name}</span>
                <button className="secondary-button" disabled={bilibiliSaving} onClick={() => void logoutBilibili()}>
                  解除授权
                </button>
              </>
            ) : (
              <button className="secondary-button" disabled={bilibiliSaving || !bilibili?.account.credentialStoreAvailable} onClick={() => void startBilibiliLogin()}>
                扫码授权互动账号
              </button>
            )}
            {bilibiliLoginKey && (
              <button className="secondary-button" disabled={bilibiliSaving} onClick={() => void pollBilibiliLogin()}>
                检查扫码状态
              </button>
            )}
          </div>
        </div>
        {bilibiliLoginQr && (
          <div className="bili-login-qr">
            <img src={bilibiliLoginQr} alt="B 站扫码登录二维码" />
            <span>二维码约 180 秒有效；扫码后请点击“检查扫码状态”。</span>
          </div>
        )}
        <details className="bili-manual-test">
          <summary>手动测试评论</summary>
          <p className="muted-copy">不影响自动冲浪目标；仅在人工确认后公开发送。</p>
          <div className="bili-interaction-grid">
          <label>
            手动测试模式
            <select
              value={bilibiliDraft.mode}
              onChange={(event) =>
                setBilibiliDraft((current) => ({
                  ...current,
                  mode: event.target.value as "read_only" | "test",
                }))
              }
            >
              <option value="read_only">禁止手动发送</option>
              <option value="test">指定测试区（手动一次）</option>
            </select>
          </label>
          <label className="bili-video-field">
            测试视频
            <input
              autoComplete="off"
              value={bilibiliDraft.testVideo}
              onChange={(event) =>
                setBilibiliDraft((current) => ({ ...current, testVideo: event.target.value }))
              }
              placeholder="BV 号或 bilibili.com/video 链接"
            />
          </label>
          </div>
          <div className="bili-interaction-actions">
          <button className="secondary-button" disabled={!bilibiliDraft.testVideo.trim() || bilibiliSaving} onClick={() => void inspectBilibiliVideo()}>
            查询测试视频
          </button>
          </div>
        <div className="bili-draft-panel">
          <div>
            <strong id="bili-comment-editor-title">评论正文（可直接手写 / 修改）</strong>
            <span id="bili-comment-editor-help">不用先生成草稿：直接输入 → 保存当前正文 → 确认发送测试评论。保存时会整理空白并自动补齐目标账号的 @，请以保存后的全文为准。保留同账号同视频防重；仅发送到指定测试视频，结果不明确时勿重发。</span>
          </div>
          <textarea
            value={bilibiliDraftText}
            onChange={(event) => setBilibiliDraftText(event.target.value)}
            aria-labelledby="bili-comment-editor-title"
            aria-describedby="bili-comment-editor-help bili-comment-editor-state"
            placeholder="在这里输入你想让莲心发送的话，也可以修改已有草稿。保存后确认，不会自动发送。"
            readOnly={bilibiliDraftBusy || bilibiliSaving}
            maxLength={500}
            rows={4}
          />
          <span id="bili-comment-editor-state" role="status">
            {!bilibiliDraftText.trim() ? "尚未填写正文；AI 生成草稿是可选项。"
              : bilibiliDraftText.trim() !== bilibili?.draft?.text ? "正文尚未保存：请先点击“保存当前正文”，否则不会发送新内容。"
                : bilibili?.draft?.status === "pending_confirmation" ? "当前正文已保存（仅本次运行），确认发送时会再次展示全文。"
                  : "此草稿已尝试发送，请先核对发送记录；如需另写内容，请修改后保存。"}
          </span>
          <div className="bili-interaction-actions">
            <button className="secondary-button" disabled={!bilibiliDraft.testVideo.trim() || bilibiliDraftBusy || bilibiliSaving} onClick={() => void generateBilibiliDraft()}>
              {bilibiliDraftBusy ? "处理中…" : "AI 生成草稿（可选）"}
            </button>
            <button className="secondary-button" disabled={!bilibiliDraft.testVideo.trim() || !bilibiliDraftText.trim() || bilibiliDraftBusy || bilibiliSaving} onClick={() => void generateBilibiliDraft(bilibiliDraftText)}>
              保存当前正文
            </button>
            <button className="secondary-button" disabled={(!bilibili?.draft && !bilibiliDraftText) || bilibiliDraftBusy || bilibiliSaving} onClick={() => void clearBilibiliDraft()}>
              清除草稿
            </button>
          </div>
        </div>
        <div className="bili-interaction-actions">
          <button
            className="secondary-button"
            disabled={!bilibili?.writeCapability.ready || bilibiliSaving || bilibiliDraftBusy}
            title={bilibili?.writeCapability.blockers.join(" ") || "写入能力尚未就绪"}
            onClick={() => void requestBilibiliTest()}
          >
            确认发送测试评论
          </button>
          <button className="secondary-button" disabled={bilibiliSaving} onClick={() => void recheckBilibiliComment()}>
            只读复核最近评论（不重发）
          </button>
        </div>
        </details>
        <div className="bili-interaction-row">
          <div>
            <strong>自动回复我的 B 站留言</strong>
            <span>仅回复已核验主号在莲心评论串里的新留言；约每 2 分钟轮询，支持继续聊，不回复其他网友。</span>
          </div>
          <button
            className={`toggle ${bilibiliDraft.autoReplyEnabled ? "is-on" : ""}`}
            onClick={() =>
              setBilibiliDraft((current) => ({
                ...current,
                autoReplyEnabled: !current.autoReplyEnabled,
              }))
            }
            aria-label="自动回复我的 B 站留言"
            aria-pressed={bilibiliDraft.autoReplyEnabled}
          >
            <span />
          </button>
        </div>
        <div className="bili-interaction-row">
          <div>
            <strong>在评论区 @ 莲心时回复我</strong>
            <span>仅响应已核验主号对互动小号的真实 @；先读取该视频资料和可用字幕，再在原留言下回复。默认关闭。</span>
          </div>
          <button className={`toggle ${bilibiliDraft.mentionReplyEnabled ? "is-on" : ""}`}
            onClick={() => setBilibiliDraft((current) => ({ ...current, mentionReplyEnabled: !current.mentionReplyEnabled }))}
            aria-label="在评论区 @ 莲心时回复我" aria-pressed={bilibiliDraft.mentionReplyEnabled}>
            <span />
          </button>
        </div>
        <div className="bili-interaction-row">
          <div>
            <strong>暂停互动</strong>
            <span>暂停只影响未来的评论与回复，不关闭只读冲浪。</span>
          </div>
          <button
            className={`toggle ${bilibiliDraft.paused ? "is-on" : ""}`}
            onClick={() =>
              setBilibiliDraft((current) => ({ ...current, paused: !current.paused }))
            }
            aria-label="暂停 B 站互动"
            aria-pressed={bilibiliDraft.paused}
          >
            <span />
          </button>
        </div>
        <div className="bili-capability-status" role="status">
          <label><input type="checkbox" disabled checked={bilibiliDraft.allowOtherReplies} readOnly /> 回复其他网友（沿用旧版保留值，尚未就绪）</label>
          <strong>已保存状态：{!bilibili ? "读取中" : bilibili.settings.paused ? "互动已暂停"
            : !bilibili.settings.userUidConfirmed || !bilibili.account.connected ? "待完成账号核验"
            : bilibili.settings.autoCommentEnabled || bilibili.settings.autoReplyEnabled || bilibili.settings.mentionReplyEnabled ? "互动已启用" : "仅只读"}</strong>
          <span>三个发言开关独立生效；后台约每 2 分钟检查留言和 @ 通知，发布前仍会实时核验授权。</span>
          <span>使用 B 站的 @ 选择器选中互动小号，仅输入“@莲心”文字不会触发。详细过程请查看后端日志。</span>
        </div>
        <div className="bili-interaction-actions">
          <button className="secondary-button" disabled={bilibiliSaving} onClick={() => void saveBilibiliSettings(false)}>
            {bilibiliSaving ? "保存中…" : "应用互动设置（不关闭）"}
          </button>
        </div>
        <details className="bili-manual-test">
          <summary>最近发送记录与只读复核</summary>
        <p className="muted-copy">小号回读匹配不代表大号可见或 @ 通知送达；未登录最新一页未查到也不代表未发送。复核已有记录即可，不必重新生成草稿或发送。</p>
        {bilibili?.records?.slice(0, 5).map((record, index) => (
          <div key={`${record.rpid}-${index}`} className="muted-copy">
            <span>{record.bvid} · 评论ID {record.rpid || "未知"} · {record.status === "sent_verified" ? "小号回读匹配（非公开可见证明）" : record.status === "sent_unverified" ? "B站已受理，回读未确认" : record.status}</span>
            <p>{record.diagnostic}</p>
            {record.rpid && <button className="secondary-button" disabled={bilibiliSaving} onClick={() => void recheckBilibiliComment(record.rpid)}>只读复核此评论</button>}
          </div>
        ))}
        </details>
        {bilibiliNotice && <p className="muted-copy bili-notice" aria-live="polite">{bilibiliNotice}</p>}
      </section>
      <p className="muted-copy">
        自动评论默认关闭。调试与自动调度共用兴趣标签冲浪链路；开启自动评论且校验通过后可能公开发送一条评论。
      </p>

      <section className="global-settings-card">
        <header><h3>浏览历史与兴趣反馈</h3><p>只显示最近 50 轮；赞 / 踩是本地兴趣反馈，不会在 B 站公开点赞。清空不重置已看视频去重记录。</p></header>
        <div className="proactive-actions"><input aria-label="历史关键词" value={filter} maxLength={120} placeholder="关键词筛选" onChange={event => setFilter(event.target.value)} /><button onClick={() => void extra(refreshHistory)}>查询 / 刷新</button><button onClick={async () => { if (await confirmAction("清空全部浏览历史？已看视频去重记录仍保留。")) void extra(async () => { setHistory((await lianxinApi.clearBilibiliHistory()).items); }); }}>清空历史</button></div>
        {!history.length && <p>暂无结果，点击查询加载浏览历史。</p>}
        {history.map(record => <article className="proactive-history" key={record.id}><h4>{record.keyword} · {record.date}</h4>{record.results.map(video => <div key={video.bvid}><strong>{video.title || video.bvid}</strong><small>{video.author} · 播放 {video.play_count} · {video.user_reaction || "未反馈"}</small><div className="proactive-actions"><button onClick={() => void extra(async () => { const result = await lianxinApi.openBilibiliVideo(video.bvid); if (!result.ok) throw new Error(result.error); })}>浏览器打开</button>{(["liked", "disliked"] as const).map(reaction => <button key={reaction} aria-pressed={video.user_reaction === reaction} onClick={() => void extra(async () => { await lianxinApi.reactBilibiliHistory(record.id, video.bvid, reaction); await refreshHistory(); })}>{reaction === "liked" ? "喜欢" : "不喜欢"}</button>)}</div></div>)}</article>)}
      </section>
  </fieldset>;
});
