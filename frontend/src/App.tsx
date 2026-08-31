import { Navigate, Route, Routes } from "react-router-dom";
import { InvestigationWorkspace } from "./components/InvestigationWorkspace";
import { LoginPage } from "./pages/LoginPage";

export function App() {
  return (
    <Routes>
      <Route path="/" element={<InvestigationWorkspace />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
