// Bundles the extension entry point into dist/extension.js.
//
// VS Code loads a single CommonJS file; `vscode` is provided by the host at
// runtime and must stay external. Pass --watch for incremental rebuilds during
// development (used by `npm run watch`).

const esbuild = require('esbuild');

const watch = process.argv.includes('--watch');
const production = process.argv.includes('--production') || process.env.NODE_ENV === 'production';

/** @type {import('esbuild').BuildOptions} */
const options = {
  entryPoints: ['src/extension.ts'],
  bundle: true,
  outfile: 'dist/extension.js',
  external: ['vscode'],
  format: 'cjs',
  platform: 'node',
  target: 'node18',
  sourcemap: !production,
  minify: production,
  logLevel: 'info',
};

async function main() {
  if (watch) {
    const ctx = await esbuild.context(options);
    await ctx.watch();
    console.log('[esbuild] watching for changes…');
  } else {
    await esbuild.build(options);
    console.log('[esbuild] build complete');
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
