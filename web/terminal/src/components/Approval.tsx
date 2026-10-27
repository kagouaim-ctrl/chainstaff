import { Check, ExternalLink, FileCode2, GitPullRequest, ShieldAlert, ShieldCheck, X } from "lucide-react";
import { useState } from "react";
import { post } from "../lib/api";
import { refreshAt, useAtArtifact, type AtApproval } from "../lib/at";
import { DEMO } from "../lib/demo";
import { ago, errText, fmtTime, go, toast } from "../lib/util";
import { Dialog, ErrorState, Loading } from "./ui";

/** One sentence that says what is waiting: who did what, whether the tests passed. */
function sentence(a: AtApproval): string {
  const who = a.agent_name || a.agent_id || "Agent";
  if (a.kind !== "patch") return `${who}：${a.title}`;
  const tests = a.detail.tests_passed ? "测试通过" : a.detail.tests_passed === false ? "测试没有通过" : "没有运行测试";
  const what = a.detail.pr_url ? "开了一个 Draft PR" : "完成了一项代码修复";
  return `${who} ${what}，${tests}，等待你确认。`;
}

/** What approving actually does, said before the click (the server decides; this mirrors service.decide). */
function effect(a: AtApproval): string {
  if (a.detail.provider === "local") return "批准后 KAGOU 把补丁提交到本地仓库，再由发现问题的 Agent 验证。";
  if (a.detail.pr_url) return "批准只记录你的审核，KAGOU 不会合并；合并请在 GitHub 上操作。";
  return "批准只记录你的审核。";
}

async function decide(a: AtApproval, approve: boolean, note = ""): Promise<boolean> {
  try {
    const r = await post<{ applied_commit?: string; note?: string }>(`/api/at/approvals/${a.id}`, { approve, note });
    toast(approve ? (r.applied_commit ? `已批准，补丁已应用：${r.applied_commit.slice(0, 10)}` : r.note || "已批准") : "已拒绝，补丁不会应用");
    refreshAt();
    return true;
  } catch (e) {
    toast(errText(e), true);
    return false;
  }
}

/**
 * An approval as a short message: one sentence and three buttons. The diff, files, tests and risk live behind
 * "查看修改"; approving and rejecting call the same endpoint as everywhere else, and nothing is ever auto-approved.
 */
export function ApprovalMessage({ a, compact }: { a: AtApproval; compact?: boolean }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const done = a.status !== "pending";
  const run = async (approve: boolean) => {
    if (!approve && !confirm("拒绝这项修改？补丁不会被应用。")) return;
    setBusy(true);
    await decide(a, approve);
    setBusy(false);
  };
  return (
    <div className={`apv ${done ? "done" : ""} ${compact ? "compact" : ""}`}>
      <span className={`apv-ic ${a.status}`}>{a.status === "approved" ? <Check size={14} /> : a.status === "rejected" ? <X size={14} /> : <ShieldCheck size={14} />}</span>
      <div className="apv-b">
        <div className="apv-t">{sentence(a)}</div>
        <div className="apv-s">
          {done ? <>{a.status === "approved" ? "已批准" : "已拒绝"}{a.decided_by ? ` · ${a.decided_by}` : ""}{a.decided_at ? ` · ${ago(a.decided_at)}` : ""}</>
            : <>{a.detail.repo && <span>{a.detail.repo}</span>}{a.detail.stat && <span className="mono">{a.detail.stat}</span>}<span>{ago(a.at)}</span></>}
        </div>
      </div>
      <div className="apv-act">
        <button className="btn sm ghost" onClick={() => setOpen(true)}><FileCode2 size={13} /> 查看修改</button>
        {!done && <button className="btn sm" disabled={busy || DEMO} title={DEMO ? "演示版只能查看" : undefined} onClick={() => run(false)}>拒绝</button>}
        {!done && <button className="btn sm primary" disabled={busy || DEMO} title={DEMO ? "演示版只能查看" : undefined} onClick={() => run(true)}>批准</button>}
      </div>
      {open && <ApprovalReview a={a} onClose={() => setOpen(false)} />}
    </div>
  );
}

/** The full review: what changed, the diff, the tests, the description, the related task and what approving does. */
export function ApprovalReview({ a, onClose }: { a: AtApproval; onClose: () => void }) {
  const art = useAtArtifact(a.artifact_id);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const pending = a.status === "pending";
  const run = async (approve: boolean) => {
    setBusy(true);
    if (await decide(a, approve, note)) onClose();
    setBusy(false);
  };
  const files = a.detail.files || [];
  return (
    <Dialog wide title={a.title} onClose={onClose} footer={pending ? (
      <>
        <input className="input sm grow" placeholder="备注（可选，会记录在审计里）" value={note} onChange={(e) => setNote(e.target.value)} />
        <button className="btn" disabled={busy || DEMO} onClick={() => run(false)}><X size={13} /> 拒绝</button>
        <button className="btn primary" disabled={busy || DEMO} onClick={() => run(true)}><Check size={13} /> 批准</button>
      </>
    ) : <span className="muted sm grow">{a.status === "approved" ? "已批准" : "已拒绝"}{a.decided_by ? `，由 ${a.decided_by}` : ""}{a.decided_at ? `，${fmtTime(a.decided_at)}` : ""}{a.note ? `。备注：${a.note}` : ""}</span>}>
      <div className="col gap12">
        <div className="apv-facts">
          <div><span className="label">提交者</span><span>{a.agent_name || a.agent_id || "—"}</span></div>
          <div><span className="label">仓库</span><span>{a.detail.repo || "—"}{a.detail.provider ? <span className="muted"> · {a.detail.provider}</span> : null}</span></div>
          <div><span className="label">测试</span><span className={a.detail.tests_passed ? "ok" : a.detail.tests_passed === false ? "bad" : "muted"}>
            {a.detail.tests_passed ? "通过" : a.detail.tests_passed === false ? "没有通过" : "没有运行"}</span></div>
          <div><span className="label">改动</span><span>{a.detail.stat || `${files.length} 个文件`}</span></div>
        </div>
        <div className={`note ${pending ? "acc" : ""} row gap8`} style={{ alignItems: "flex-start" }}>
          <ShieldAlert size={15} className="acc" style={{ flex: "none", marginTop: 2 }} />
          <span>{effect(a)} 合并到生产、部署、合约升级和资金操作只能由人完成，Agent 没有这些权限。{a.detail.tests_passed === false ? " 测试没有通过，批准前请仔细检查。" : ""}</span>
        </div>
        <div className="row gap8 wrap">
          {a.task_id && <button className="btn sm ghost" onClick={() => (onClose(), go("missions", undefined, { task: a.task_id! }))}>相关任务 {a.task_id}</button>}
          {a.detail.pr_url && <a className="btn sm ghost" href={a.detail.pr_url} target="_blank" rel="noreferrer"><GitPullRequest size={13} /> Draft PR <ExternalLink size={11} /></a>}
        </div>
        {!!files.length && (
          <div className="col gap6">
            <span className="label">修改的文件</span>
            <div className="apv-files">{files.map((f) => <span key={f} className="mono">{f}</span>)}</div>
          </div>
        )}
        {a.artifact_id ? (art.isLoading ? <Loading rows={4} /> : art.error ? <ErrorState error={art.error} /> : (
          <>
            {art.data!.body && <div className="col gap6"><span className="label">修改说明</span><div className="note pre">{art.data!.body}</div></div>}
            {art.data!.meta?.pr_error && <div className="note bad">Draft PR 没有开成：{String(art.data!.meta.pr_error)}</div>}
            <div className="col gap6"><span className="label">代码差异</span><DiffView text={art.data!.content} /></div>
          </>
        )) : <span className="muted sm">这个审批没有附带补丁。</span>}
      </div>
    </Dialog>
  );
}

export function DiffView({ text }: { text: string }) {
  return (
    <pre className="code diff">
      {text.split("\n").map((l, i) => (
        <span key={i} className={l.startsWith("+") && !l.startsWith("+++") ? "ok" : l.startsWith("-") && !l.startsWith("---") ? "bad" : l.startsWith("@@") ? "info" : ""}>{l}{"\n"}</span>
      ))}
    </pre>
  );
}
