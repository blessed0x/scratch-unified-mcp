# Sidecar fork

Forked 2026-09-06 from `upstream-scratch4js` @ `cb1669aa`
(`upstream-scratch4js/packages/scratch-mcp/src/`, files
`index.js runtime.js blocks.js jsonpatch.js bridge.js`).

## Why a fork

`upstream-scratch4js/` is gitignored and declared read-only (updates =
re-clone). Every VM/index.js improvement made for this project would be
clobbered by the next upstream pull. The fork is the editable copy;
upstream stays the clean reference for diffing.

## Dependency wiring

Self-contained: `package.json` pins the same deps as upstream's
`scratch-mcp` package, except `scratch4js`/`s-api4js` resolve from the
public npm registry (`^1.2.4`) instead of the pnpm workspace. `npm
install` inside this dir materializes a real `node_modules` (gitignored).
`LICENSE-UPSTREAM` is the upstream MPL-2.0 text.

## Diffing against upstream

```bash
diff -r upstream-scratch4js/packages/scratch-mcp/src scratch_unified/sidecar/src
```

Expected permanent delta: `node_modules` symlink (ours only).
Feature delta is documented per-release below.

## Feature delta

- 2026-09-06: fork created, byte-identical to upstream @ cb1669aa
  (verified `diff -r` clean). `node_bridge.NODE_INDEX` repointed here.
