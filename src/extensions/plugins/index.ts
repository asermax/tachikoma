import { isAbsolute, join } from "node:path";

import { Type } from "typebox";

import { expandHome } from "../../workspace.ts";
import { defineExtension } from "../api.ts";
import { InstallManager } from "./installs.ts";
import { loadExtensionModule } from "./loader.ts";
import { createPluginToolsFactory } from "./tools.ts";

interface PluginsConfig {
  sources: string[];
}

/**
 * Third-party plugins: loads Tachikoma extensions (defineExtension contract) from
 * configured local sources and agent-installed records, registering each with the
 * host. Install management tools clone git sources into `{dataDir}/plugins/<alias>`;
 * installs, updates, and removals take effect on restart.
 */
export default defineExtension<PluginsConfig>({
  name: "plugins",

  configSchema: Type.Object({
    sources: Type.Array(Type.String(), { default: [] }),
  }),

  async setup(app) {
    const manager = new InstallManager({
      state: app.state,
      pluginsDir: join(app.workspace.dataDir, "plugins"),
      log: app.log,
    });

    const configured = app.extensionConfig.sources.map((source) => {
      const expanded = expandHome(source);

      return isAbsolute(expanded) ? expanded : app.workspace.resolve(expanded);
    });
    const installed = Object.values(manager.list()).map((record) => record.path);

    for (const source of new Set([...configured, ...installed])) {
      const extension = await loadExtensionModule(source, app.log);

      if (extension != null) {
        app.registerExtension(extension);
        app.log.info({ source, extension: extension.name }, "plugin extension registered");
      }
    }

    app.agent.use(createPluginToolsFactory(manager));
  },
});
