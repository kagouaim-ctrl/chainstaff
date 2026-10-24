import { QueryClientProvider } from "@tanstack/react-query";
import { lazy, StrictMode, Suspense, useEffect, useState, useSyncExternalStore } from "react";
import { createRoot } from "react-dom/client";
import { BrandMark } from "./components/TerminalHeader";
import { TerminalShell } from "./components/TerminalShell";
import { Loading, Toasts } from "./components/ui";
import { api, onAuthChange, queryClient, setToken, token } from "./lib/api";
import { useMeta } from "./lib/queries";
import { startStream, stopStream } from "./lib/stream";
import { errText, usePref, useRoute } from "./lib/util";
import "./styles.css";

// Each page is its own chunk, so the first paint only loads the shell and the current page.
const PAGES = {
  terminal: lazy(() => import("./pages/Terminal")),
  agents: lazy(() => import("./pages/Agents")),
  squads: lazy(() => import("./pages/Squads")),
  workspace: lazy(() => import("./pages/Workspace")),
  tasks: lazy(() => import("./pages/Tasks")),
  onchain: lazy(() => import("./pages/OnChain")),
  skills: lazy(() => import("./pages/Skills")),
  activity: lazy(() => import("./pages/Activity")),
  settings: lazy(() => import("./pages/Settings")),
  more: lazy(() => import("./pages/More")),
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
            <b className="brand-t">ChainStaff</b>
            <span className="muted xs">AI Workforce Terminal for Web3</span>
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
  const [density] = usePref("density", "compact");
  useEffect(() => {
    document.documentElement.dataset.density = density;
  }, [density]);
  useEffect(() => {
    if (meta.data) startStream(meta.data.last_message_id);
    return stopStream;
  }, [meta.data?.last_message_id === undefined]); // start once, when the cursor is known
  const Page = PAGES[route.page as keyof typeof PAGES] || PAGES.terminal;
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
