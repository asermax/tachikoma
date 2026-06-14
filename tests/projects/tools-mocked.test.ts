import type { ExtensionFactory } from "@earendil-works/pi-coding-agent";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { fakeLogger } from "./helpers.ts";

const access = vi.fn();
const addSubmodule = vi.fn();
const checkoutBranch = vi.fn();
const resolveDefaultBranch = vi.fn();
const removeSubmodule = vi.fn();
const uncommittedChangesDetail = vi.fn();
const listSubmodules = vi.fn();

vi.mock("node:fs/promises", () => ({
  access: (...args: unknown[]) => access(...args),
}));

vi.mock("../../src/extensions/projects/git.ts", () => ({
  addSubmodule: (...args: unknown[]) => addSubmodule(...args),
  checkoutBranch: (...args: unknown[]) => checkoutBranch(...args),
  describeProjectState: vi.fn(),
  listSubmodules: (...args: unknown[]) => listSubmodules(...args),
  projectState: vi.fn(),
  removeSubmodule: (...args: unknown[]) => removeSubmodule(...args),
  resolveDefaultBranch: (...args: unknown[]) => resolveDefaultBranch(...args),
  uncommittedChangesDetail: (...args: unknown[]) => uncommittedChangesDetail(...args),
}));

const { handleRegisterProject, handleDeregisterProject, createProjectsToolsFactory } = await import(
  "../../src/extensions/projects/tools.ts"
);

const log = fakeLogger();
const deps = { workspaceRoot: "/ws", log };

beforeEach(() => {
  vi.clearAllMocks();
});

describe("handleRegisterProject rollback", () => {
  it("rolls back a partially-created submodule when checkout fails", async () => {
    access.mockRejectedValueOnce(new Error("missing")).mockResolvedValueOnce(undefined);
    addSubmodule.mockResolvedValue(undefined);
    resolveDefaultBranch.mockResolvedValue("main");
    checkoutBranch.mockRejectedValue(new Error("checkout boom"));
    removeSubmodule.mockResolvedValue(undefined);

    await expect(handleRegisterProject(deps, { name: "app", url: "u" })).rejects.toThrow(
      /Error registering project: checkout boom/,
    );

    expect(removeSubmodule).toHaveBeenCalledWith("/ws", "app");
    expect(log.warn).toHaveBeenCalledWith(
      expect.objectContaining({ name: "app" }),
      "project registration failed",
    );
  });

  it("swallows cleanup errors and surfaces the original failure", async () => {
    access.mockRejectedValueOnce(new Error("missing")).mockResolvedValueOnce(undefined);
    addSubmodule.mockRejectedValue(new Error("add boom"));
    removeSubmodule.mockRejectedValue(new Error("cleanup boom"));

    await expect(handleRegisterProject(deps, { name: "app", url: "u" })).rejects.toThrow(
      /Error registering project: add boom/,
    );

    expect(removeSubmodule).toHaveBeenCalledWith("/ws", "app");
  });

  it("does not attempt cleanup when no partial state was created", async () => {
    access.mockRejectedValue(new Error("missing"));
    addSubmodule.mockRejectedValue(new Error("add boom"));

    await expect(handleRegisterProject(deps, { name: "app", url: "u" })).rejects.toThrow(
      /add boom/,
    );

    expect(removeSubmodule).not.toHaveBeenCalled();
  });
});

describe("handleDeregisterProject force path", () => {
  it("rejects an empty name", async () => {
    await expect(handleDeregisterProject(deps, { name: "" })).rejects.toThrow(/'name' is required/);
  });

  it("removes a dirty project when force=true without throwing", async () => {
    access.mockResolvedValue(undefined);
    uncommittedChangesDetail.mockResolvedValue(" M wip.txt");
    removeSubmodule.mockResolvedValue(undefined);

    const output = await handleDeregisterProject(deps, { name: "app", force: true });

    expect(output).toContain("Deregistered project 'app'");
    expect(removeSubmodule).toHaveBeenCalledWith("/ws", "app");
  });
});

interface CapturedTool {
  name: string;
  parameters: unknown;
  execute: (id: string, params: unknown) => Promise<{ content: { text: string }[] }>;
}

const captureTools = (): CapturedTool[] => {
  const tools: CapturedTool[] = [];
  const pi = { registerTool: (tool: CapturedTool) => tools.push(tool) };

  createProjectsToolsFactory(deps)(pi as unknown as Parameters<ExtensionFactory>[0]);

  return tools;
};

describe("createProjectsToolsFactory", () => {
  it("registers the three project tools", () => {
    const tools = captureTools();

    expect(tools.map((tool) => tool.name)).toEqual([
      "register_project",
      "deregister_project",
      "list_projects",
    ]);
  });

  it("register_project execute wraps the handler result as text", async () => {
    access.mockRejectedValue(new Error("missing"));
    addSubmodule.mockResolvedValue(undefined);
    resolveDefaultBranch.mockResolvedValue("main");
    checkoutBranch.mockResolvedValue(undefined);

    const tool = captureTools().find((candidate) => candidate.name === "register_project");
    const result = await (tool as CapturedTool).execute("call-1", { name: "app", url: "u" });

    expect(result.content[0]?.text).toContain("Registered project 'app'");
  });

  it("deregister_project execute wraps the handler result as text", async () => {
    access.mockResolvedValue(undefined);
    uncommittedChangesDetail.mockResolvedValue(null);
    removeSubmodule.mockResolvedValue(undefined);

    const tool = captureTools().find((candidate) => candidate.name === "deregister_project");
    const result = await (tool as CapturedTool).execute("call-2", { name: "app" });

    expect(result.content[0]?.text).toContain("Deregistered project 'app'");
  });

  it("list_projects execute reports when nothing is registered", async () => {
    listSubmodules.mockResolvedValue([]);

    const tool = captureTools().find((candidate) => candidate.name === "list_projects");
    const result = await (tool as CapturedTool).execute("call-3", {});

    expect(result.content[0]?.text).toContain("No projects registered");
  });
});
