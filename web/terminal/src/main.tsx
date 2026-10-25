import { QueryClientProvider } from "@tanstack/react-query";
import { lazy, StrictMode, Suspense, useEffect, useState, useSyncExternalStore } from "react";
import { createRoot } from "react-dom/client";
import { BrandMark } from "./components/TerminalHeader";
import { TerminalShell } from "./components/TerminalShell";
import { Loading, Toasts } from "./components/ui";
import { api, onAuthChange, queryClient, setToken, token } from "./lib/api";
import { startAtStream, stopAtStream, useAtMeta } from "./lib/at";
import { useMeta } from "./lib/queries";
import { startStream, stopStream } from "./lib/stream";
import { errText, usePref, useRoute } from "./lib/util";
import "./styles.css";

// Each page is its own chunk, so the first paint only loads the shell and the current page.
const PAGES = {
  overview: lazy(() => import("./pages/Overview")),
  agents: lazy(() => import("./pages/Agents")),
  missions: lazy(() => import("./pages/Missions")),
  workspace: lazy(() => import("./pages/WorkspaceAt")),
  activity: lazy(() => import("./pages/ActivityAt")),
  settings: lazy(() => import("./pages/SettingsAt")),
  more: lazy(() => import("./pages/More")),
  // Earlier workflow, still on its real backend; linked from Settings → Legacy, not from the navigation.
  terminal: lazy(() => import("./pages/Terminal")),
  team: lazy(() => import("./pages/Team")),
  board: lazy(() => import("./pages/Board")),
  tasks: lazy(() => import("./pages/Tasks")),
  squads: lazy(() => import("./pages/Squads")),
  skills: lazy(() => import("./pages/Skills")),
  onchain: lazy(() => import("./pages/OnChain")),
  log: lazy(() => import("./pages/Log")),
} as const;

function useAuthed() {
  return useSyncExternalStore(onAuthChange, () => !!token());
}

function Login() {
  const [value, setValue] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true);
    setErr("");
    setToken(value.trim());
    try {
      await api("/api/wf/meta");
    } catch (e) {
      setToken("");
      setErr(errText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="login">
      <form onSubmit={(e) => (e.preventDefault(), submit())}>
        <div className="row" style={{ gap: 10 }}>
          <BrandMark size={24} />
          <div className="col gap2">
            <b className="brand-t">KAGOU</b>
            <span className="muted xs">Real-Time On-Chain Multi-Agent Platform · BNB Chain</span>
          </div>
        </div>
        <label className="field">
          <span>Access token</span>
          <input className="input mono" type="password" autoFocus value={value} onChange={(e) => setValue(e.target.value)} placeholder="CHAINSTAFF_CONSOLE_TOKEN" />
        </label>
        {err && <div className="bad sm">{err}</div>}
        <button className="btn primary block" disabled={!value.trim() || busy}>Sign in</button>
        <p className="muted xs" style={{ margin: 0 }}>令牌只存在这个标签页里，和经典控制台共用。</p>
      </form>
    </div>
  );
}

function Terminal() {
  const route = useRoute();
  const meta = useMeta();
  const at = useAtMeta();
  const [density] = usePref("density", "compact");
  useEffect(() => {
    document.documentElement.dataset.density = density;
  }, [density]);
  useEffect(() => {
    if (meta.data) startStream(meta.data.last_message_id);
    return stopStream;
  }, [meta.data?.last_message_id === undefined]); // start once, when the cursor is known
  useEffect(() => {
    if (at.data) startAtStream(at.data.last_activity_id);
    return stopAtStream;
  }, [at.data?.last_activity_id === undefined]);
  const Page = PAGES[route.page as keyof typeof PAGES] || PAGES.overview;
  return (
    <TerminalShell route={route}>
      <Suspense fallback={<div className="page"><Loading rows={6} /></div>}>
        <Page route={route} />
      </Suspense>
    </TerminalShell>
  );
}

function App() {
  const authed = useAuthed();
  useEffect(() => {
    if (!authed) queryClient.clear();
  }, [authed]);
  return (
    <QueryClientProvider client={queryClient}>
      {authed ? <Terminal /> : <Login />}
      <Toasts />
    </QueryClientProvider>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
