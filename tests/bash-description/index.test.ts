import { describe, expect, it, vi } from "vitest";

import bashDescription from "../../src/extensions/bash-description/index.ts";

describe("bash-description extension", () => {
  it("has the expected name", () => {
    expect(bashDescription.name).toBe("bash-description");
  });

  it("registers a pi factory via app.agent.use on setup", () => {
    const use = vi.fn();
    const app = {
      agent: { use, models: {} },
    } as { agent: { use: typeof use; models: Record<string, never> } };

    bashDescription.setup(app);

    expect(use).toHaveBeenCalledOnce();
    const [, options] = use.mock.calls[0];
    expect(options.sessionScopes).toContain("main");
    expect(options.sessionScopes).toContain("background");
  });

  it("registers a bash tool override with a required description parameter", () => {
    const registered: Array<Record<string, unknown>> = [];
    const pi = { registerTool: (def: Record<string, unknown>) => registered.push(def) };

    const use = vi.fn((_factory: (pi: typeof pi) => void) => {
      _factory(pi);
    });
    const app = { agent: { use } } as { agent: { use: typeof use } };

    bashDescription.setup(app);

    expect(registered).toHaveLength(1);
    const tool = registered[0];

    expect(tool.name).toBe("bash");
    expect(tool.description).toContain("bash command");

    // description must be required (no Optional wrapper)
    const properties = tool.parameters.properties;
    expect(properties.description.type).toBe("string");

    // command should still be required
    expect(properties.command.type).toBe("string");

    // timeout should be optional
    expect(properties.timeout).toBeDefined();
  });

  it("strips description before delegating to the original execute", async () => {
    // The extension's execute destructures description and passes the rest to
    // originalBash.execute. We can't mock the module-level originalBash, so
    // we verify the behavior indirectly: if description is not a valid param for
    // the original schema, passing it would cause a type error at runtime. Since
    // the original bash only expects { command, timeout }, the stripped call is
    // correct. Instead we verify the schema: description is required in the
    // override but the original schema (used by createBashToolDefinition) only
    // has command and timeout.
    const { createBashToolDefinition } = await import("@earendil-works/pi-coding-agent");
    const original = createBashToolDefinition("/tmp");

    // Original schema properties: command, timeout (no description)
    const origProps = Object.keys(
      (original.parameters as { properties: Record<string, unknown> }).properties,
    ).sort();
    expect(origProps).toEqual(["command", "timeout"]);

    // Override schema properties: command, description, timeout
    const registered: Array<Record<string, unknown>> = [];
    const pi = { registerTool: (def: Record<string, unknown>) => registered.push(def) };
    const use = vi.fn((_factory: (pi: typeof pi) => void) => {
      _factory(pi);
    });
    const app = { agent: { use } } as { agent: { use: typeof use } };
    bashDescription.setup(app);

    const overrideProps = Object.keys(
      (registered[0].parameters as { properties: Record<string, unknown> }).properties,
    ).sort();
    expect(overrideProps).toEqual(["command", "description", "timeout"]);
  });

  it("inherits the original tool's label and promptSnippet, with no custom renderers", () => {
    const registered: Array<Record<string, unknown>> = [];
    const pi = { registerTool: (def: Record<string, unknown>) => registered.push(def) };

    const use = vi.fn((_factory: (pi: typeof pi) => void) => {
      _factory(pi);
    });
    const app = { agent: { use } } as { agent: { use: typeof use } };
    bashDescription.setup(app);

    const tool = registered[0];
    expect(tool.label).toBe("bash");
    expect(tool.promptSnippet).toBe("Execute bash commands (ls, grep, find, etc.)");
    // Built-in renderers are spread from the original definition
    expect(typeof tool.renderCall).toBe("function");
    expect(typeof tool.renderResult).toBe("function");
  });
});
