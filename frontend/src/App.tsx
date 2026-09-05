import { Navigate, Route, Routes } from "react-router-dom";
import { InvestigationWorkspace } from "./components/InvestigationWorkspace";
import { LoginPage } from "./pages/LoginPage";
import { TransparencyPage } from "./pages/TransparencyPage";
import { InvestigationPage } from "./pages/InvestigationPage";
import { AuthProvider, ProtectedRoute } from "./hooks/useAuth";

export function App() {
  return (
    <AuthProvider><Routes>
      <Route element={<ProtectedRoute />}>
        <Route path="/" element={<InvestigationWorkspace />} />
        <Route path="/transparency" element={<TransparencyPage />} />
        <Route path="/investigations/:runId" element={<InvestigationPage />} />
      </Route>
      <Route path="/login" element={<LoginPage />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes></AuthProvider>
  );
}
