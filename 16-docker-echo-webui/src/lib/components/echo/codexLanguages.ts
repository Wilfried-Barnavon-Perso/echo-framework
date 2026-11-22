// Copyright (c) 2025-2026 Wilfried BARNAVON
// SPDX-License-Identifier: Apache-2.0
import '$lib/utils/codemirror';
import { LanguageDescription, type LanguageSupport } from '@codemirror/language';
import { languages } from '@codemirror/language-data';

export type CodexDocCategory = 'code' | 'document' | 'media' | 'binary';

export interface CodexLanguageSpec {
	id: string;
	label: string;
	category: CodexDocCategory;
	canonicalExt?: string;
	extensions?: string[];
	cmAliases?: string[];
}

export const CODEX_LANGUAGE_SPECS: CodexLanguageSpec[] = [
	{
		id: 'plaintext',
		label: 'Texte brut',
		category: 'document',
		canonicalExt: '.txt',
		extensions: ['txt', 'log', 'conf']
	},
	{
		id: 'markdown',
		label: 'Markdown',
		category: 'document',
		canonicalExt: '.md',
		extensions: ['md', 'markdown', 'mkd']
	},
	{
		id: 'python',
		label: 'Python',
		category: 'code',
		canonicalExt: '.py',
		extensions: ['py', 'pyw']
	},
	{
		id: 'javascript',
		label: 'JavaScript',
		category: 'code',
		canonicalExt: '.js',
		extensions: ['js', 'mjs', 'cjs', 'jsx'],
		cmAliases: ['javascript', 'jsx']
	},
	{
		id: 'typescript',
		label: 'TypeScript',
		category: 'code',
		canonicalExt: '.ts',
		extensions: ['ts', 'mts', 'cts', 'tsx'],
		cmAliases: ['typescript', 'tsx']
	},
	{
		id: 'svelte',
		label: 'Svelte',
		category: 'code',
		canonicalExt: '.svelte',
		extensions: ['svelte']
	},
	{
		id: 'vue',
		label: 'Vue',
		category: 'code',
		canonicalExt: '.vue',
		extensions: ['vue']
	},
	{
		id: 'html',
		label: 'HTML',
		category: 'document',
		canonicalExt: '.html',
		extensions: ['html', 'htm']
	},
	{
		id: 'css',
		label: 'CSS',
		category: 'code',
		canonicalExt: '.css',
		extensions: ['css']
	},
	{
		id: 'scss',
		label: 'SCSS',
		category: 'code',
		canonicalExt: '.scss',
		extensions: ['scss']
	},
	{
		id: 'less',
		label: 'LESS',
		category: 'code',
		canonicalExt: '.less',
		extensions: ['less']
	},
	{
		id: 'json',
		label: 'JSON',
		category: 'document',
		canonicalExt: '.json',
		extensions: ['json', 'map']
	},
	{
		id: 'yaml',
		label: 'YAML',
		category: 'document',
		canonicalExt: '.yaml',
		extensions: ['yaml', 'yml']
	},
	{
		id: 'toml',
		label: 'TOML',
		category: 'document',
		canonicalExt: '.toml',
		extensions: ['toml']
	},
	{
		id: 'xml',
		label: 'XML',
		category: 'document',
		canonicalExt: '.xml',
		extensions: ['xml', 'xsl', 'xsd']
	},
	{
		id: 'svg',
		label: 'SVG',
		category: 'document',
		canonicalExt: '.svg',
		extensions: ['svg'],
		cmAliases: ['xml']
	},
	{
		id: 'csv',
		label: 'CSV',
		category: 'document',
		canonicalExt: '.csv',
		extensions: ['csv']
	},
	{
		id: 'tsv',
		label: 'TSV',
		category: 'document',
		canonicalExt: '.tsv',
		extensions: ['tsv']
	},
	{
		id: 'ini',
		label: 'INI / Config',
		category: 'document',
		canonicalExt: '.ini',
		extensions: ['ini', 'env', 'properties', 'cfg'],
		cmAliases: ['properties files', 'ini']
	},
	{
		id: 'sql',
		label: 'SQL',
		category: 'code',
		canonicalExt: '.sql',
		extensions: ['sql']
	},
	{
		id: 'shell',
		label: 'Shell / Bash',
		category: 'code',
		canonicalExt: '.sh',
		extensions: ['sh', 'bash', 'zsh'],
		cmAliases: ['shell', 'bash']
	},
	{
		id: 'powershell',
		label: 'PowerShell',
		category: 'code',
		canonicalExt: '.ps1',
		extensions: ['ps1', 'psd1', 'psm1']
	},
	{
		id: 'bat',
		label: 'Batch',
		category: 'code',
		canonicalExt: '.bat',
		extensions: ['bat', 'cmd']
	},
	{
		id: 'dockerfile',
		label: 'Dockerfile',
		category: 'code',
		canonicalExt: '.dockerfile',
		extensions: ['dockerfile']
	},
	{
		id: 'makefile',
		label: 'Makefile',
		category: 'code',
		extensions: ['mk'],
		cmAliases: ['shell']
	},
	{
		id: 'cmake',
		label: 'CMake',
		category: 'code',
		extensions: ['cmake']
	},
	{
		id: 'c',
		label: 'C',
		category: 'code',
		canonicalExt: '.c',
		extensions: ['c', 'h']
	},
	{
		id: 'cpp',
		label: 'C++',
		category: 'code',
		canonicalExt: '.cpp',
		extensions: ['cpp', 'cc', 'cxx', 'hpp', 'hxx']
	},
	{
		id: 'csharp',
		label: 'C#',
		category: 'code',
		canonicalExt: '.cs',
		extensions: ['cs']
	},
	{
		id: 'java',
		label: 'Java',
		category: 'code',
		canonicalExt: '.java',
		extensions: ['java']
	},
	{
		id: 'kotlin',
		label: 'Kotlin',
		category: 'code',
		canonicalExt: '.kt',
		extensions: ['kt', 'kts']
	},
	{
		id: 'go',
		label: 'Go',
		category: 'code',
		canonicalExt: '.go',
		extensions: ['go']
	},
	{
		id: 'rust',
		label: 'Rust',
		category: 'code',
		canonicalExt: '.rs',
		extensions: ['rs']
	},
	{
		id: 'ruby',
		label: 'Ruby',
		category: 'code',
		canonicalExt: '.rb',
		extensions: ['rb']
	},
	{
		id: 'php',
		label: 'PHP',
		category: 'code',
		canonicalExt: '.php',
		extensions: ['php']
	},
	{
		id: 'swift',
		label: 'Swift',
		category: 'code',
		canonicalExt: '.swift',
		extensions: ['swift']
	},
	{
		id: 'dart',
		label: 'Dart',
		category: 'code',
		canonicalExt: '.dart',
		extensions: ['dart']
	},
	{
		id: 'scala',
		label: 'Scala',
		category: 'code',
		canonicalExt: '.scala',
		extensions: ['scala']
	},
	{
		id: 'groovy',
		label: 'Groovy',
		category: 'code',
		canonicalExt: '.groovy',
		extensions: ['groovy', 'gradle']
	},
	{
		id: 'r',
		label: 'R',
		category: 'code',
		canonicalExt: '.r',
		extensions: ['r']
	},
	{
		id: 'lua',
		label: 'Lua',
		category: 'code',
		canonicalExt: '.lua',
		extensions: ['lua']
	},
	{
		id: 'perl',
		label: 'Perl',
		category: 'code',
		canonicalExt: '.pl',
		extensions: ['pl', 'pm']
	},
	{
		id: 'vb',
		label: 'VB.NET',
		category: 'code',
		canonicalExt: '.vb',
		extensions: ['vb'],
		cmAliases: ['vb.net']
	},
	{
		id: 'hcl',
		label: 'HCL / Terraform',
		category: 'code',
		canonicalExt: '.tf',
		extensions: ['tf', 'hcl']
	},
	{
		id: 'elixir',
		label: 'Elixir',
		category: 'code',
		canonicalExt: '.ex',
		extensions: ['ex', 'exs']
	},
	{
		id: 'protobuf',
		label: 'ProtoBuf',
		category: 'code',
		canonicalExt: '.proto',
		extensions: ['proto']
	},
	{
		id: 'latex',
		label: 'LaTeX',
		category: 'document',
		canonicalExt: '.tex',
		extensions: ['tex']
	},
	{
		id: 'diff',
		label: 'Diff / Patch',
		category: 'document',
		canonicalExt: '.diff',
		extensions: ['diff', 'patch']
	},
	{
		id: 'pdf',
		label: 'Document PDF',
		category: 'document',
		extensions: ['pdf']
	},
	{
		id: 'image',
		label: 'Image',
		category: 'media',
		extensions: ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'ico', 'avif', 'tiff', 'tif']
	},
	{
		id: 'audio',
		label: 'Audio',
		category: 'media',
		extensions: ['mp3', 'wav', 'ogg', 'flac', 'm4a', 'aac', 'opus', 'weba']
	},
	{
		id: 'video',
		label: 'Vidéo',
		category: 'media',
		extensions: ['mp4', 'webm', 'mov', 'avi', 'mkv', 'm4v', 'ogv']
	},
	{
		id: 'binary',
		label: 'Binaire',
		category: 'binary'
	}
];

export const SELECTABLE_CODEX_LANGUAGES = CODEX_LANGUAGE_SPECS.filter(
	(s) => s.category === 'code' || (s.category === 'document' && s.id !== 'pdf')
);

const SPEC_BY_ID = new Map<string, CodexLanguageSpec>(
	CODEX_LANGUAGE_SPECS.map((spec) => [spec.id, spec])
);

const EXT_TO_LANG = new Map<string, string>();
for (const spec of CODEX_LANGUAGE_SPECS) {
	if (!spec.extensions) continue;
	for (const ext of spec.extensions) {
		if (!EXT_TO_LANG.has(ext)) {
			EXT_TO_LANG.set(ext, spec.id);
		}
	}
}

const IMAGE_EXTS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'ico', 'avif', 'tiff', 'tif']);
const AUDIO_EXTS = new Set(['mp3', 'wav', 'ogg', 'flac', 'm4a', 'aac', 'opus', 'weba']);
const VIDEO_EXTS = new Set(['mp4', 'webm', 'mov', 'avi', 'mkv', 'm4v', 'ogv']);

const BACKEND_LANG_ALIASES: Record<string, string> = {
	text: 'plaintext',
	txt: 'plaintext',
	py: 'python',
	js: 'javascript',
	jsx: 'javascript',
	ts: 'typescript',
	tsx: 'typescript',
	md: 'markdown',
	yml: 'yaml',
	sh: 'shell',
	bash: 'shell',
	zsh: 'shell',
	ps1: 'powershell',
	'c++': 'cpp',
	'c#': 'csharp',
	cs: 'csharp',
	rs: 'rust',
	rb: 'ruby',
	kt: 'kotlin',
	tf: 'hcl',
	terraform: 'hcl',
	tex: 'latex',
	patch: 'diff',
	docker: 'dockerfile'
};

const NO_HIGHLIGHT_LANGS = new Set([
	'plaintext',
	'csv',
	'tsv',
	'bat',
	'pdf',
	'image',
	'audio',
	'video',
	'binary'
]);

const PLAINTEXT_SPEC = SPEC_BY_ID.get('plaintext')!;

export function getCodexLanguageSpec(langId: string): CodexLanguageSpec {
	const key = (langId || '').trim().toLowerCase();
	if (!key || key === 'plaintext') return PLAINTEXT_SPEC;
	const found = SPEC_BY_ID.get(key);
	if (found) return found;
	return {
		id: key,
		label: langId,
		category: 'code'
	};
}

export function getCanonicalExtForLanguage(langId: string): string | null {
	return getCodexLanguageSpec(langId).canonicalExt ?? null;
}

export function detectCodexLanguage(input: {
	path?: string;
	backendLang?: string | null;
	mime?: string | null;
	isBinary?: boolean;
	content?: string | null;
}): string {
	const rawPath = input.path || '';
	const baseName = rawPath.split(/[\\/]/).pop()?.toLowerCase() ?? '';
	const dotIdx = baseName.lastIndexOf('.');
	const ext = dotIdx > 0 ? baseName.slice(dotIdx + 1) : '';
	const mime = (input.mime || '').toLowerCase().trim();

	// 1. MIME / extension média, PDF et binaire
	if (ext === 'pdf' || mime === 'application/pdf') return 'pdf';
	if (!input.isBinary && (ext === 'svg' || mime === 'image/svg+xml')) return 'svg';
	if (mime.startsWith('image/') || IMAGE_EXTS.has(ext)) return 'image';
	if (mime.startsWith('audio/') || AUDIO_EXTS.has(ext)) return 'audio';
	if (mime.startsWith('video/') || VIDEO_EXTS.has(ext)) return 'video';
	if (input.isBinary) return 'binary';

	// 2. Noms de fichiers spéciaux
	if (/^(?:dockerfile|containerfile)(?:\..+)?$/i.test(baseName)) return 'dockerfile';
	if (/^(?:gnumakefile|makefile)(?:\..+)?$/i.test(baseName)) return 'makefile';
	if (baseName === 'cmakelists.txt') return 'cmake';
	if (baseName === 'jenkinsfile') return 'groovy';
	if (
		/^\.env(?:\..+)?$/i.test(baseName) ||
		baseName === '.gitignore' ||
		baseName === '.dockerignore'
	) {
		return 'ini';
	}

	// 3. Extension exacte
	if (ext && EXT_TO_LANG.has(ext)) {
		return EXT_TO_LANG.get(ext)!;
	}

	// 4. Langage fourni par le backend
	if (input.backendLang) {
		const rawBackend = input.backendLang.trim().toLowerCase();
		if (rawBackend && rawBackend !== 'plaintext' && rawBackend !== 'folder') {
			const normalized = BACKEND_LANG_ALIASES[rawBackend] ?? rawBackend;
			if (normalized !== 'plaintext' && SPEC_BY_ID.has(normalized)) {
				return normalized;
			}
		}
	}

	// 5. Heuristique légère sur le contenu
	const content = input.content;
	if (content && content.length > 0) {
		const firstLine = content.split(/\r?\n/, 1)[0].trim().toLowerCase();
		if (firstLine.startsWith('#!')) {
			if (firstLine.includes('python')) return 'python';
			if (
				firstLine.includes('node') ||
				firstLine.includes('deno') ||
				firstLine.includes('bun')
			) {
				return 'javascript';
			}
			if (
				firstLine.includes('bash') ||
				firstLine.includes('/sh') ||
				firstLine.includes(' sh') ||
				firstLine.includes('zsh')
			) {
				return 'shell';
			}
			if (firstLine.includes('pwsh') || firstLine.includes('powershell')) return 'powershell';
			if (firstLine.includes('ruby')) return 'ruby';
			if (firstLine.includes('perl')) return 'perl';
			if (firstLine.includes('php')) return 'php';
			if (firstLine.includes('lua')) return 'lua';
		}

		const trimmed = content.trim();
		const lowerHead = trimmed.slice(0, 64).toLowerCase();
		if (lowerHead.startsWith('<!doctype html') || lowerHead.startsWith('<html')) return 'html';
		if (lowerHead.startsWith('<svg')) return 'svg';
		if (lowerHead.startsWith('<?xml')) return 'xml';
		if (lowerHead.startsWith('<?php')) return 'php';

		if (
			(trimmed.startsWith('{') && trimmed.endsWith('}')) ||
			(trimmed.startsWith('[') && trimmed.endsWith(']'))
		) {
			try {
				JSON.parse(trimmed);
				return 'json';
			} catch {
				// Not valid JSON
			}
		}

		if (trimmed.startsWith('---\n') || trimmed.startsWith('---\r\n')) return 'yaml';
	}

	// 6. Fallback
	return 'plaintext';
}

export async function loadCodeMirrorLanguage(
	langId: string,
	filePath?: string | null
): Promise<LanguageSupport | null> {
	const normalized = (langId || 'plaintext').trim().toLowerCase();
	if (NO_HIGHLIGHT_LANGS.has(normalized)) return null;

	const baseName = filePath?.split(/[\\/]/).pop() ?? '';
	const lowerBase = baseName.toLowerCase();

	let desc: LanguageDescription | null = null;

	if (
		(normalized === 'javascript' && lowerBase.endsWith('.jsx')) ||
		(normalized === 'typescript' && lowerBase.endsWith('.tsx'))
	) {
		desc =
			LanguageDescription.matchFilename(languages, baseName) ??
			LanguageDescription.matchFilename(languages, lowerBase);
	}

	if (!desc) {
		const spec = SPEC_BY_ID.get(normalized);
		const candidates = [...(spec?.cmAliases ?? []), normalized];
		for (const name of candidates) {
			const match = LanguageDescription.matchLanguageName(languages, name, true);
			if (match) {
				desc = match;
				break;
			}
		}
	}

	if (!desc && baseName) {
		desc =
			LanguageDescription.matchFilename(languages, baseName) ??
			LanguageDescription.matchFilename(languages, lowerBase);
	}

	if (!desc) return null;

	try {
		return await desc.load();
	} catch {
		return null;
	}
}
