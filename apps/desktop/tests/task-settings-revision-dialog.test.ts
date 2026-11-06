import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import TestRenderer, { act } from "react-test-renderer";
import { describe, expect, it, vi } from "vitest";
import TaskSettingsRevisionDialog, {
  type ArtifactDisposition
} from "../src/renderer/src/TaskSettingsRevisionDialog";
import type {
  TaskSettingsRevisionPreview
} from "../src/shared/protocol";

const preview: TaskSettingsRevisionPreview = {
  token: "token-1",
  changed: true,
  generation: 2,
  artifactSummary: {
    completedPolicyCount: 7,
    completedBatchCount: 2,
    registeredFileCount: 5,
    eligibleFileCount: 5,
    protectedFileCount: 0,
    directoryCount: 1,
    estimatedFreedBytes: 2048
  },
  protectedFiles: []
};

function buttons(renderer: TestRenderer.ReactTestRenderer) {
  return renderer.root.findAllByType("button").map((button) => ({
    text: String(
      button.children
        .map((child) => String(child))
        .join("")
    ),
    click: button.props.onClick as (() => void) | undefined
  }));
}

describe("TaskSettingsRevisionDialog", () => {
  it("shows both policies and artifact counts", () => {
    const html = renderToStaticMarkup(
      createElement(TaskSettingsRevisionDialog, {
        preview,
        busy: false,
        onCommit: async () => undefined,
        onCancel: () => undefined
      })
    );
    expect(html).toContain("保留已完成文件并重新扫描");
    expect(html).toContain("永久删除旧结果并重新扫描");
    expect(html).toContain("7");
    expect(html).toContain("2.0 KB");
  });

  it("commits preserve immediately", async () => {
    const onCommit = vi.fn(async (_policy: ArtifactDisposition) => undefined);
    let renderer: TestRenderer.ReactTestRenderer | undefined;
    await act(async () => {
      renderer = TestRenderer.create(
        createElement(TaskSettingsRevisionDialog, {
          preview,
          busy: false,
          onCommit,
          onCancel: () => undefined
        })
      );
      await Promise.resolve();
    });
    const found = buttons(renderer as TestRenderer.ReactTestRenderer).find(
      (item) => item.text.includes("保留已完成文件并重新扫描")
    );
    expect(found).toBeDefined();
    await act(async () => {
      found?.click?.();
      await Promise.resolve();
    });
    expect(onCommit).toHaveBeenCalledWith("preserve");
    renderer?.unmount();
  });

  it("requires a second confirmation before permanent delete", async () => {
    const onCommit = vi.fn(async (_policy: ArtifactDisposition) => undefined);
    let renderer: TestRenderer.ReactTestRenderer | undefined;
    await act(async () => {
      renderer = TestRenderer.create(
        createElement(TaskSettingsRevisionDialog, {
          preview,
          busy: false,
          onCommit,
          onCancel: () => undefined
        })
      );
      await Promise.resolve();
    });

    const firstDelete = buttons(
      renderer as TestRenderer.ReactTestRenderer
    ).find((item) => item.text.includes("永久删除旧结果并重新扫描"));
    await act(async () => {
      firstDelete?.click?.();
      await Promise.resolve();
    });
    expect(onCommit).not.toHaveBeenCalled();

    const confirm = buttons(
      renderer as TestRenderer.ReactTestRenderer
    ).find((item) => item.text.includes("确认永久删除并重新扫描"));
    expect(confirm).toBeDefined();
    await act(async () => {
      confirm?.click?.();
      await Promise.resolve();
    });
    expect(onCommit).toHaveBeenCalledWith("delete");
    renderer?.unmount();
  });

  it("warns when protected files exist", () => {
    const html = renderToStaticMarkup(
      createElement(TaskSettingsRevisionDialog, {
        preview: {
          ...preview,
          artifactSummary: {
            ...preview.artifactSummary,
            protectedFileCount: 2
          },
          protectedFiles: [
            {
              path: "D:/shared.docx",
              rawPath: "D:/shared.docx",
              taskIds: ["task-1", "task-2"],
              sources: ["batch"],
              exists: true,
              canTrash: false,
              reason: "SHARED_WITH_OTHER_TASK"
            }
          ]
        },
        busy: false,
        onCommit: async () => undefined,
        onCancel: () => undefined
      })
    );
    expect(html).toContain("有 2 个文件受保护");
  });
});
