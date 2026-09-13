import type { ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import BottomNav from "./components/BottomNav";
import { getToken } from "./lib/api";
import Approvals from "./pages/Approvals";
import Chat from "./pages/Chat";
import Chats from "./pages/Chats";
import Connectors from "./pages/Connectors";
import Login from "./pages/Login";
import ProjectDetail from "./pages/ProjectDetail";
import Projects from "./pages/Projects";
import SettingsPage from "./pages/SettingsPage";
import TaskMonitor from "./pages/TaskMonitor";

function RequireAuth({ children }: { children: ReactNode }) {
  if (!getToken()) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

function Shell() {
  const location = useLocation();
  const hideNav = location.pathname === "/login";
  return (
    <div className="mx-auto min-h-dvh max-w-md bg-zinc-950 text-zinc-100">
      <div className={hideNav ? "" : "pb-nav"}>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route
            path="/"
            element={
              <RequireAuth>
                <Chats />
              </RequireAuth>
            }
          />
          <Route
            path="/c/:id"
            element={
              <RequireAuth>
                <Chat />
              </RequireAuth>
            }
          />
          <Route
            path="/projects"
            element={
              <RequireAuth>
                <Projects />
              </RequireAuth>
            }
          />
          <Route
            path="/connectors"
            element={
              <RequireAuth>
                <Connectors />
              </RequireAuth>
            }
          />
          <Route
            path="/projects/:id"
            element={
              <RequireAuth>
                <ProjectDetail />
              </RequireAuth>
            }
          />
          <Route
            path="/tasks/:id"
            element={
              <RequireAuth>
                <TaskMonitor />
              </RequireAuth>
            }
          />
          <Route
            path="/approvals"
            element={
              <RequireAuth>
                <Approvals />
              </RequireAuth>
            }
          />
          <Route
            path="/settings"
            element={
              <RequireAuth>
                <SettingsPage />
              </RequireAuth>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </div>
      {hideNav ? null : <BottomNav />}
    </div>
  );
}

export default function App() {
  const base = (import.meta.env.BASE_URL || "/").replace(/\/$/, "") || "/";
  return (
    <BrowserRouter basename={base}>
      <Shell />
    </BrowserRouter>
  );
}
