import { useVerification } from "../hooks/useVerification";
import { AnalysisProgress } from "./AnalysisProgress";
import { AppHeader } from "./AppHeader";
import { ReportView } from "./ReportView";
import { VerificationForm } from "./VerificationForm";

export function InvestigationWorkspace() {
  const verification = useVerification();
  if (verification.status === "complete") return <><AppHeader /><ReportView mode={verification.mode} url={verification.url} onReset={verification.reset} /></>;
  return <main className="workspace-shell"><AppHeader />{verification.status === "idle" ? <VerificationForm url={verification.url} mode={verification.mode} error={verification.error} onUrlChange={verification.setUrl} onModeChange={verification.setMode} onSubmit={verification.startVerification} /> : <AnalysisProgress progress={verification.progress} mode={verification.mode} url={verification.url} stages={verification.stages} />}</main>;
}
