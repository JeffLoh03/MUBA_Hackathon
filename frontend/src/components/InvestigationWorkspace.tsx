import { useVerification } from "../hooks/useVerification";
import { AnalysisProgress } from "./AnalysisProgress";
import { AppHeader } from "./AppHeader";
import { ReportView } from "./ReportView";
import { VerificationForm } from "./VerificationForm";

export function InvestigationWorkspace() {
  const verification = useVerification();

  if (verification.status === "complete" && verification.report) {
    return <><AppHeader /><ReportView report={verification.report} runId={verification.runId} completedAt={verification.completedAt} mode={verification.mode} input={verification.input} imageName={verification.image?.name ?? ""} onReset={verification.reset} /></>;
  }

  return (
    <main className="workspace-shell">
      <AppHeader />
      {verification.status === "idle" ? (
        <VerificationForm
          input={verification.input}
          image={verification.image}
          mode={verification.mode}
          error={verification.error}
          showBrowser={verification.showBrowser}
          onInputChange={verification.setInput}
          onImageSelect={verification.selectImage}
          onImageRemove={verification.removeImage}
          onModeChange={verification.setMode}
          onShowBrowserChange={verification.setShowBrowser}
          onSubmit={verification.startVerification}
        />
      ) : (
        <AnalysisProgress
          progress={verification.progress}
          mode={verification.mode}
          input={verification.input}
          imageName={verification.image?.name ?? ""}
          stages={verification.stages}
          events={verification.events}
          sourceCount={verification.sourceCount}
          showBrowser={verification.showBrowser}
        />
      )}
    </main>
  );
}
