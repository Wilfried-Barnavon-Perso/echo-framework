import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';
import { resolveBuildInfo } from './scripts/build-info.js';

import { viteStaticCopy } from 'vite-plugin-static-copy';

const backendTarget = process.env.WEBUI_BACKEND_URL || 'http://localhost:8080';
const buildInfo = resolveBuildInfo();

let uiVersion = process.env.npm_package_version;
try {
	const uiVersionPath = join(process.cwd(), 'UI_VERSION');
	if (existsSync(uiVersionPath)) {
		const raw = readFileSync(uiVersionPath, 'utf8').trim();
		if (raw) uiVersion = raw;
	}
} catch {
	// fallback to npm_package_version
}

export default defineConfig({
	resolve: {
		conditions: ['onnxruntime-web-use-extern-wasm']
	},
	plugins: [
		sveltekit(),
		viteStaticCopy({
			targets: [
				{
					src: 'src/lib/encryption/vendor/THIRD_PARTY_LICENSES.txt',
					dest: 'licenses/encryption'
				},
				{
					src: ['node_modules/@novnc/novnc/docs/LICENSE*', 'node_modules/@novnc/novnc/AUTHORS'],
					dest: 'licenses/novnc'
				},
				{
					src: 'node_modules/onnxruntime-web/dist/*.jsep.*',

					dest: 'wasm'
				},
				{
					src: 'node_modules/pdfjs-dist/wasm/*',
					dest: 'wasm'
				}
			]
		})
	],
	define: {
		APP_VERSION: JSON.stringify(uiVersion),
		APP_BUILD_HASH: JSON.stringify(buildInfo.hash),
		APP_BUILD_CHANNEL: JSON.stringify(buildInfo.channel)
	},
	build: {
		target: 'es2022',
		sourcemap: false
	},
	optimizeDeps: {
		esbuildOptions: { target: 'es2022' }
	},
	server: {
		proxy: {
			'/api': {
				target: backendTarget,
				changeOrigin: true,
				ws: true
			},
			'/ollama': {
				target: backendTarget,
				changeOrigin: true
			},
			'/openai': {
				target: backendTarget,
				changeOrigin: true
			},
			'/oauth': {
				target: backendTarget,
				changeOrigin: true
			},
			'/ws': {
				target: backendTarget,
				changeOrigin: true,
				ws: true
			}
		}
	},
	worker: {
		format: 'es'
	},
	esbuild: {
		pure: process.env.ENV === 'dev' ? [] : ['console.log', 'console.debug', 'console.error']
	}
});
