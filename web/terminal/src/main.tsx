import { QueryClientProvider } from "@tanstack/react-query";
import { lazy, StrictMode, Suspense, useEffect, useState, useSyncExternalStore } from "react";
import { createRoot } from "react-dom/client";
import { AppShell } from "./components/AppShell";
import { Loading, Toasts } from "./components/ui";
import { api, onAuthChange, queryClient, setToken, token } from "./lib/api";
import { useMeta } from "./lib/queries";
import { startStream, stopStream } from "./lib/stream";
import { errText, usePref, useRoute } from "./lib/util";
import "./styles.css";

// Each page is its own chunk, so the first paint only loads the shell and the current page.
const PAGES = {
  overview: lazy(() => import("./pages/Overview")),
  workforce: lazy(() => import("./pages/Workforce")),
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
    <div style={{ minHeight: "100vh", display: "grid", placeItems: "center", padding: 16 }}>
      <form className="dialog" style={{ position: "static", width: 380, maxWidth: "100%" }} onSubmit={(e) => (e.preventDefault(), submit())}>
        <div className="row" style={{ gap: 10, marginBottom: 12 }}>
          <span className="brand-mark">CS</span>
          <b>ChainStaff Terminal</b>
        </div>
        <label className="field">
          <span>Access token</span>
          <input className="input mono" type="password" autoFocus value={value} onChange={(e) => setValue(e.target.value)} placeholder="CHAINSTAFF_WEB_TOKEN" />
        </label>
        {err && <div className="bad" style={{ fontSize: 12, marginBottom: 8 }}>{err}</div>}
        <button className="btn primary" style={{ width: "100%" }} disabled={!value.trim() || busy}>Sign in</button>
        <p className="muted" style={{ fontSize: 11, marginBottom: 0 }}>令牌只存在这个标签页里，和经典控制台共用。</p>
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
  const Page = PAGES[route.page as keyof typeof PAGES] || PAGES.overview;
  return (
    <AppShell route={route}>
      <Suspense fallback={<div className="page"><Loading rows={6} /></div>}>
        <Page route={route} />
      </Suspense>
    </AppShell>
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
