// Aggregate drizzle schema: core tables plus every first-party extension's tables.
// drizzle-kit reads this module to generate migrations for the whole app.

export * from "./core-schema.ts";
