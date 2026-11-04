import type { ReactNode } from "react";
import type { BrowserHostState, TaskMode } from "../../shared/protocol";
import type { ManualRecoveryView } from "../../shared/task-workspace";
import BrowserSurface from "./BrowserSurface";
import { ManualRecoveryStatus } from "./TaskRuntimePanel";

export interface TaskWorkspaceFrameProps {
  mode: TaskMode;
  taskPanel: ReactNode;
  settingsPanel?: ReactNode;
  browser: BrowserHostState;
  settingsOpen: boolean;
  pauseDetailOpen: boolean;
  manualRecovery: ManualRecoveryView;
  onToggleSettings: () => void;
}

export default function TaskWorkspaceFrame({
  mode,
  taskPanel,
  settingsPanel,
  browser,
  settingsOpen,
  pauseDetailOpen,
  manualRecovery,
  onToggleSettings
}: TaskWorkspaceFrameProps): React.JSX.Element {
  const modeLabel = mode === "single" ? "逐篇采集" : "批量采集";
  const browserVisible = !settingsOpen && !pauseDetailOpen;
  const bodyClassName = [
    "task-workspace-body",
    settingsOpen ? "settings-open" : "",
    pauseDetailOpen ? "dialog-open" : ""
  ].filter(Boolean).join(" ");
  return (
    <section className="task-workspace-frame">
      <header className="task-workspace-header">
        <div>
          <span className="eyebrow">{modeLabel}运行工作台</span>
          <h2>{settingsOpen ? "任务设置" : "当前任务"}</h2>
          {!settingsOpen && manualRecovery.phase !== "idle" && (
            <ManualRecoveryStatus
              recovery={manualRecovery}
            />
          )}
        </div>
        <button
          type="button"
          className="secondary-button compact"
          onClick={onToggleSettings}
        >
          {settingsOpen ? "返回任务" : "任务设置"}
        </button>
      </header>

      <div className={bodyClassName}>
        <div className="task-workspace-primary">
          {settingsOpen ? settingsPanel : taskPanel}
        </div>
        <BrowserSurface
          browser={browser}
          visible={browserVisible}
          variant="task"
          onRefresh={() =>
            void window.pkulaw.browserNavigate("reload")
          }
          onContinue={() => void window.pkulaw.browserContinue()}
        />
      </div>
    </section>
  );
}
