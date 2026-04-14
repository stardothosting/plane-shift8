/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * Validation script: verifies that stripped features are absent from
 * the frontend route configuration and sidebar navigation constants.
 *
 * Run with: node apps/web/tests/validate-stripped-features.mjs
 * Exit code 0 = pass, 1 = fail
 */

import { readFileSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const rootDir = join(__dirname, "..", "..", "..");

let failures = 0;

function assertAbsent(content, pattern, fileName, featureName) {
  if (content.includes(pattern)) {
    console.error(`FAIL: "${pattern}" found in ${fileName} — ${featureName} should be stripped`);
    failures++;
  }
}

function assertPresent(content, pattern, fileName, featureName) {
  if (!content.includes(pattern)) {
    console.error(`FAIL: "${pattern}" NOT found in ${fileName} — ${featureName} should be present`);
    failures++;
  }
}

// --- Route config validation ---
const routesFile = join(rootDir, "apps", "web", "app", "routes", "core.ts");
const routes = readFileSync(routesFile, "utf-8");

// Stripped features should NOT have routes
assertAbsent(routes, "/cycles/", routesFile, "Cycles");
assertAbsent(routes, "/modules/", routesFile, "Modules");
assertAbsent(routes, "/pages/", routesFile, "Pages");
assertAbsent(routes, "/stickies", routesFile, "Stickies");
assertAbsent(routes, "/analytics/", routesFile, "Analytics");
assertAbsent(routes, "/active-cycles", routesFile, "Active Cycles");

// Core features SHOULD still have routes
assertPresent(routes, "/issues", routesFile, "Issues");
assertPresent(routes, "/projects", routesFile, "Projects");
assertPresent(routes, "/notifications", routesFile, "Notifications");
assertPresent(routes, "/drafts", routesFile, "Drafts");
assertPresent(routes, "/views", routesFile, "Views");
assertPresent(routes, "/intake", routesFile, "Intake");
assertPresent(routes, "/settings", routesFile, "Settings");

// --- Sidebar constants validation ---
const sidebarFile = join(rootDir, "packages", "constants", "src", "workspace.ts");
const sidebar = readFileSync(sidebarFile, "utf-8");

// Stripped items should NOT be in sidebar constants
assertAbsent(sidebar, 'key: "analytics"', sidebarFile, "Analytics sidebar item");
assertAbsent(sidebar, 'key: "stickies"', sidebarFile, "Stickies sidebar item");

// Core items SHOULD still be in sidebar constants
assertPresent(sidebar, 'key: "home"', sidebarFile, "Home sidebar item");
assertPresent(sidebar, 'key: "inbox"', sidebarFile, "Inbox sidebar item");
assertPresent(sidebar, 'key: "projects"', sidebarFile, "Projects sidebar item");
assertPresent(sidebar, 'key: "views"', sidebarFile, "Views sidebar item");

// --- Project navigation validation ---
const projNavFile = join(
  rootDir, "apps", "web", "ce", "components", "navigations", "use-navigation-items.ts"
);
const projNav = readFileSync(projNavFile, "utf-8");

assertAbsent(projNav, 'key: "cycles"', projNavFile, "Cycles project nav");
assertAbsent(projNav, 'key: "modules"', projNavFile, "Modules project nav");
assertAbsent(projNav, 'key: "pages"', projNavFile, "Pages project nav");
assertPresent(projNav, 'key: "work_items"', projNavFile, "Work items project nav");
assertPresent(projNav, 'key: "views"', projNavFile, "Views project nav");
assertPresent(projNav, 'key: "intake"', projNavFile, "Intake project nav");

// --- Backend URL aggregation validation ---
const backendUrlFile = join(rootDir, "apps", "api", "plane", "app", "urls", "__init__.py");
const backendUrls = readFileSync(backendUrlFile, "utf-8");

assertAbsent(backendUrls, "analytic_urls", backendUrlFile, "Analytics backend URLs");
assertAbsent(backendUrls, "cycle_urls", backendUrlFile, "Cycles backend URLs");
assertAbsent(backendUrls, "module_urls", backendUrlFile, "Modules backend URLs");
assertAbsent(backendUrls, "page_urls", backendUrlFile, "Pages backend URLs");
assertPresent(backendUrls, "issue_urls", backendUrlFile, "Issues backend URLs");
assertPresent(backendUrls, "project_urls", backendUrlFile, "Projects backend URLs");
assertPresent(backendUrls, "workspace_urls", backendUrlFile, "Workspace backend URLs");

// --- External API URL validation ---
const externalUrlFile = join(rootDir, "apps", "api", "plane", "api", "urls", "__init__.py");
const externalUrls = readFileSync(externalUrlFile, "utf-8");

assertAbsent(externalUrls, "cycle_patterns", externalUrlFile, "Cycles external API");
assertAbsent(externalUrls, "module_patterns", externalUrlFile, "Modules external API");
assertAbsent(externalUrls, "sticky_patterns", externalUrlFile, "Stickies external API");
assertPresent(externalUrls, "work_item_patterns", externalUrlFile, "Work items external API");

// --- Summary ---
if (failures === 0) {
  console.log("PASS: All stripped feature validations passed.");
  process.exit(0);
} else {
  console.error(`\n${failures} validation(s) FAILED.`);
  process.exit(1);
}
