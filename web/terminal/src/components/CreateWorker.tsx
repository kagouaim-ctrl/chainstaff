import { ArrowRight, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { post } from "../lib/api";
import { AVATAR_COLORS, AVATAR_SHAPES, refreshAt, type AtAgentRow, type AvatarColor, type AvatarShape } from "../lib/at";
import { DEMO, DEMO_READ_ONLY } from "../lib/demo";
import { closeCreateWorker } from "../lib/shell";
import { errText, go, toast } from "../lib/util";
import { COLOR_HEX, SHAPE_LABEL, ShapeMark, WorkerAvatar } from "./WorkerAvatar";

/**
 * New Worker: a name, a mark and a colour, then Create. The worker is saved as a draft (not activated); its
 * responsibility, workspaces, model, skills and permissions are set in Worker Settings, where it is activated.
 * The seven-step setup is still one click away for people who want everything at once.
 */
export function CreateWorker() {
  const [name, setName] = useState("");
  const [shape, setShape] = useState<AvatarShape>(() => AVATAR_SHAPES[Math.floor(Math.random() * AVATAR_SHAPES.length)]);
  const [color, setColor] = useState<AvatarColor>("gold");
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    input.current?.focus();
    const f = (e: KeyboardEvent) => e.key === "Escape" && closeCreateWorker();
    window.addEventListener("keydown", f);
    return () => window.removeEventListener("keydown", f);
  }, []);
  const ok = !!name.trim() && !DEMO && !busy;
  const create = async () => {
    if (!ok) return;
    setBusy(true);
    try {
      const a = await post<AtAgentRow>("/api/at/agents", { name: name.trim(), appearance: { shape, color } });
      toast(`${a.name} 已创建。写下职责、选择工作区后即可激活。`);
      refreshAt();
      closeCreateWorker();
      go("agents", a.id, { tab: "settings" });
    } catch (e) {
      toast(errText(e), true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <div className="scrim" onClick={closeCreateWorker} />
      <div className="dialog create-worker" role="dialog" aria-modal="true" aria-labelledby="cw-title">
        <button className="btn ghost icon cw-close" onClick={closeCreateWorker} aria-label="关闭"><X size={16} /></button>
        <form className="cw-body" onSubmit={(e) => (e.preventDefault(), create())}>
          <div className="cw-preview">
            <WorkerAvatar id="preview" appearance={{ shape, color }} size={96} />
          </div>
          <h2 id="cw-title" className="cw-title">New Worker</h2>
          <p className="cw-sub">Give it a name and a look. You can set its job later.</p>
          <input
            ref={input}
            className="input cw-name"
            value={name}
            maxLength={40}
            onChange={(e) => setName(e.target.value)}
            placeholder="Worker name, e.g. Wallet Monitor"
            aria-label="Worker name"
          />
          <div className="cw-group">
            <span className="label">Avatar</span>
            <div className="cw-shapes" role="radiogroup" aria-label="Avatar">
              {AVATAR_SHAPES.map((s) => (
                <button key={s} type="button" role="radio" aria-checked={s === shape} className="cw-shape" onClick={() => setShape(s)} title={SHAPE_LABEL[s]}
                  style={{ color: s === shape ? COLOR_HEX[color] : undefined }}>
                  <ShapeMark shape={s} size={22} />
                </button>
              ))}
            </div>
          </div>
          <div className="cw-group">
            <span className="label">Color</span>
            <div className="cw-colors" role="radiogroup" aria-label="Color">
              {AVATAR_COLORS.map((c) => (
                <button key={c} type="button" role="radio" aria-checked={c === color} aria-label={c} className="cw-color" onClick={() => setColor(c)} style={{ background: COLOR_HEX[c] }} />
              ))}
            </div>
          </div>
          {DEMO && <div className="note acc sm">{DEMO_READ_ONLY}</div>}
          <button className={`btn primary lg block ${busy ? "loading" : ""}`} disabled={!ok}>Create Worker</button>
          <button type="button" className="btn ghost sm cw-adv" onClick={() => (closeCreateWorker(), go("agents", undefined, { new: "1" }))}>
            Full setup with templates <ArrowRight size={12} />
          </button>
        </form>
      </div>
    </>
  );
}
