import { and, eq } from "drizzle-orm";

import { appState } from "./core-schema.ts";
import type { AppDatabase } from "./index.ts";

/** Namespaced key-value persistence for state that does not warrant its own tables. */
export class KeyValueState {
  private readonly db: AppDatabase;
  private readonly namespace: string;

  constructor(db: AppDatabase, namespace: string) {
    this.db = db;
    this.namespace = namespace;
  }

  get<T>(key: string): T | null {
    const row = this.db
      .select()
      .from(appState)
      .where(and(eq(appState.namespace, this.namespace), eq(appState.key, key)))
      .get();

    return row == null ? null : (row.value as T);
  }

  set<T>(key: string, value: T): void {
    this.db
      .insert(appState)
      .values({ namespace: this.namespace, key, value, updatedAt: new Date() })
      .onConflictDoUpdate({
        target: [appState.namespace, appState.key],
        set: { value, updatedAt: new Date() },
      })
      .run();
  }

  delete(key: string): void {
    this.db
      .delete(appState)
      .where(and(eq(appState.namespace, this.namespace), eq(appState.key, key)))
      .run();
  }
}
