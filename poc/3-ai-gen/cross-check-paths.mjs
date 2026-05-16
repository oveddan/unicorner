// Cross-check: every binding path in the spec must appear in the catalog.
// Schema validation alone misses this — schema enforces shape, not referential
// integrity against the catalog the spec claims to be derived from.
//
// Usage: node poc/3-ai-gen/cross-check-paths.mjs <catalog.json> <spec.json>
// Exit codes:
//   0 — every path in spec resolves to a catalog parameter
//   1 — at least one path missing; missing paths listed on stderr

import { readFileSync } from 'node:fs';

const [, , catalogPath, specPath] = process.argv;
if (!catalogPath || !specPath) {
  console.error('usage: node cross-check-paths.mjs <catalog.json> <spec.json>');
  process.exit(2);
}

const catalog = JSON.parse(readFileSync(catalogPath, 'utf8'));
const spec = JSON.parse(readFileSync(specPath, 'utf8'));

// Build the set of paths the catalog declares.
const known = new Set();
for (const mod of catalog.modules ?? []) {
  for (const par of mod.parameters ?? []) {
    known.add(par.path);
  }
}

// Walk the spec and collect every referenced path.
const referenced = [];
for (const ctrl of spec.controls ?? []) {
  if (ctrl.bind?.path) referenced.push({ control: ctrl.id, path: ctrl.bind.path });
  for (const mb of ctrl.macro_bindings ?? []) {
    referenced.push({ control: ctrl.id, path: mb.path });
  }
}

const missing = referenced.filter((r) => !known.has(r.path));
if (missing.length > 0) {
  console.error('spec references paths not in catalog:');
  for (const { control, path } of missing) console.error(`  ${control}: ${path}`);
  process.exit(1);
}

console.log(`ok — ${referenced.length} binding(s) all resolve, scene_id=${spec.scene_id}`);
