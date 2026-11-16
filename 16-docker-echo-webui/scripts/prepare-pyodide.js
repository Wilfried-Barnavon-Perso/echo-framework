const packages = [
	'micropip',
	'packaging',
	'requests',
	'beautifulsoup4',
	'numpy',
	'pandas',
	'matplotlib',
	'scikit-learn',
	'scipy',
	'regex',
	'sympy',
	'tiktoken',
	'pytz',
	'click',
	'platformdirs',
	'openai',
	'lxml'
];

// Pure-Python packages whose wheels must be downloaded from PyPI and saved into
// static/pyodide/ so that the browser can install them offline via micropip.
// Packages already provided by the Pyodide distribution (click, platformdirs,
// typing_extensions, etc.) do NOT need to be listed here.
// Spell them canonically (dashed): that is the only form pyodide resolves lock entries by.
const pypiPackages = [
	'black',
	'pathspec',
	'mypy-extensions',
	'pytokens',
	'openpyxl',
	'et-xmlfile',
	'seaborn',
	'python-pptx',
	'python-docx',
	'xlsxwriter'
];

const pypiDepends = {
	black: ['click', 'mypy-extensions', 'packaging', 'pathspec', 'platformdirs', 'pytokens'],
	openpyxl: ['et-xmlfile'],
	seaborn: ['matplotlib', 'numpy', 'pandas'],
	'python-pptx': ['lxml', 'pillow', 'xlsxwriter', 'typing-extensions'],
	'python-docx': ['lxml', 'typing-extensions']
};

import { loadPyodide } from 'pyodide';
import { setGlobalDispatcher, ProxyAgent } from 'undici';
import { writeFile, readFile, copyFile, readdir, rmdir, access, mkdir, rm } from 'fs/promises';

/**
 * Loading network proxy configurations from the environment variables.
 * And the proxy config with lowercase name has the highest priority to use.
 */
function initNetworkProxyFromEnv() {
	// we assume all subsequent requests in this script are HTTPS:
	// https://cdn.jsdelivr.net
	// https://pypi.org
	// https://files.pythonhosted.org
	const allProxy = process.env.all_proxy || process.env.ALL_PROXY;
	const httpsProxy = process.env.https_proxy || process.env.HTTPS_PROXY;
	const httpProxy = process.env.http_proxy || process.env.HTTP_PROXY;
	const preferedProxy = httpsProxy || allProxy || httpProxy;
	/**
	 * use only http(s) proxy because socks5 proxy is not supported currently:
	 * @see https://github.com/nodejs/undici/issues/2224
	 */
	if (!preferedProxy || !preferedProxy.startsWith('http')) return;
	let preferedProxyURL;
	try {
		preferedProxyURL = new URL(preferedProxy).toString();
	} catch {
		console.warn(`Invalid network proxy URL: "${preferedProxy}"`);
		return;
	}
	const dispatcher = new ProxyAgent({ uri: preferedProxyURL });
	setGlobalDispatcher(dispatcher);
	console.log(`Initialized network proxy "${preferedProxy}" from env`);
}

async function withRetry(label, fn, retries = 4, baseDelayMs = 1500) {
	for (let attempt = 1; attempt <= retries; attempt++) {
		try {
			return await fn();
		} catch (err) {
			if (attempt >= retries) {
				console.error(`[pyodide:fetch] ${label} failed after ${retries} attempts:`, err);
				throw err;
			}
			const delayMs = baseDelayMs * 2 ** (attempt - 1);
			console.warn(
				`[pyodide:fetch] ${label} failed (attempt ${attempt}/${retries}), retrying in ${delayMs}ms:`,
				err?.message || err
			);
			await new Promise((resolve) => setTimeout(resolve, delayMs));
		}
	}
}

async function fetchWithRetry(url, { asJson = false, timeoutMs = 30000 } = {}) {
	return withRetry(`fetch ${url}`, async () => {
		const res = await fetch(url, { signal: AbortSignal.timeout(timeoutMs) });
		if (!res.ok) {
			throw new Error(`HTTP ${res.status} for ${url}`);
		}
		if (asJson) {
			return await res.json();
		}
		return Buffer.from(await res.arrayBuffer());
	});
}

async function downloadPackages() {
	console.log('Setting up pyodide + micropip');

	const pyodide = await withRetry('loadPyodide', () =>
		loadPyodide({
			packageCacheDir: 'static/pyodide'
		})
	);

	const packageJson = JSON.parse(await readFile('package.json'));
	const pyodideVersion = packageJson.dependencies.pyodide.replace('^', '');

	try {
		const pyodidePackageJson = JSON.parse(await readFile('static/pyodide/package.json'));
		const pyodidePackageVersion = pyodidePackageJson.version.replace('^', '');

		if (pyodideVersion !== pyodidePackageVersion) {
			console.log('Pyodide version mismatch, removing static/pyodide directory');
			await rm('static/pyodide', { recursive: true, force: true });
		}
	} catch (err) {
		console.log('Pyodide package not found, proceeding with download.', err);
	}

	console.log('Loading micropip package');
	await withRetry('loadPackage(micropip)', () => pyodide.loadPackage('micropip'));

	const micropip = pyodide.pyimport('micropip');
	console.log('Downloading Pyodide packages:', packages);

	for (const pkg of packages) {
		console.log(`Installing package: ${pkg}`);
		await withRetry(`micropip.install(${pkg})`, () => micropip.install(pkg));
	}

	console.log('Pyodide packages downloaded, freezing into lock file');

	const lockFile = await micropip.freeze();
	await writeFile('static/pyodide/pyodide-lock.json', lockFile);
}

async function copyPyodide() {
	console.log('Copying Pyodide files into static directory');
	// Copy all files from node_modules/pyodide to static/pyodide
	for await (const entry of await readdir('node_modules/pyodide')) {
		await copyFile(`node_modules/pyodide/${entry}`, `static/pyodide/${entry}`);
	}
}

/**
 * Download pure-Python wheels from PyPI and save them into static/pyodide/.
 * Also injects entries into pyodide-lock.json so that micropip resolves these
 * packages from the local server instead of fetching them from the internet.
 */
async function downloadPyPIWheels() {
	const lockPath = 'static/pyodide/pyodide-lock.json';
	let lockData;
	try {
		lockData = JSON.parse(await readFile(lockPath, 'utf-8'));
	} catch (err) {
		throw new Error(`Could not read ${lockPath}: ${err?.message || err}`);
	}

	for (const pkg of pypiPackages) {
		console.log(`Fetching PyPI metadata for: ${pkg}`);
		const meta = await fetchWithRetry(`https://pypi.org/pypi/${pkg}/json`, {
			asJson: true,
			timeoutMs: 30000
		});
		const version = meta.info.version;
		const files = meta.urls || [];
		// Find the pure-Python wheel (py3-none-any)
		const wheel = files.find(
			(f) => f.filename.endsWith('.whl') && f.filename.includes('py3-none-any')
		);
		if (!wheel) {
			throw new Error(`No pure-Python wheel found for ${pkg}==${version}`);
		}
		const dest = `static/pyodide/${wheel.filename}`;
		// Download wheel if not already present
		try {
			await access(dest);
			console.log(`  Already exists: ${wheel.filename}`);
		} catch {
			console.log(`  Downloading: ${wheel.filename}`);
			const buffer = await fetchWithRetry(wheel.url, { timeoutMs: 60000 });
			await writeFile(dest, buffer);
			console.log(`  Saved: ${dest} (${buffer.length} bytes)`);
		}

		// Inject into pyodide-lock.json so micropip resolves locally
		if (!lockData.packages[pkg]) {
			lockData.packages[pkg] = {
				name: pkg,
				version: version,
				file_name: wheel.filename,
				install_dir: 'site',
				sha256: wheel.digests?.sha256 || '',
				package_type: 'package',
				imports: [pkg.replace(/-/g, '_')],
				depends: pypiDepends[pkg] || []
			};
			console.log(`  Added ${pkg}==${version} to pyodide-lock.json`);
		}
	}

	await writeFile(lockPath, JSON.stringify(lockData, null, 2));
	console.log('Updated pyodide-lock.json with PyPI packages');
}

// A package with no bundled wheel is installed from PyPI in the user's browser instead.
async function verifyBundledWheels() {
	const lockPath = 'static/pyodide/pyodide-lock.json';
	const lockData = JSON.parse(await readFile(lockPath, 'utf-8'));
	const missing = [];

	for (const pkg of new Set([...packages, ...pypiPackages, ...Object.values(pypiDepends).flat()])) {
		const entry = lockData.packages[pkg.toLowerCase().replace(/[-_.]+/g, '-')];
		if (!entry) {
			missing.push(pkg);
			continue;
		}
		try {
			await access(`static/pyodide/${entry.file_name}`);
		} catch {
			missing.push(pkg);
		}
	}

	if (missing.length) {
		throw new Error(`No wheel bundled for: ${missing.join(', ')}`);
	}
	console.log('All listed packages are bundled');
}

initNetworkProxyFromEnv();
if (process.env.USE_SLIM === 'true') {
	// Rebuild generated assets so a previous full build cannot leave bundled wheels behind.
	await rm('static/pyodide', { recursive: true, force: true });
	await mkdir('static/pyodide', { recursive: true });
	await copyPyodide();

	const { version } = JSON.parse(await readFile('node_modules/pyodide/package.json', 'utf-8'));
	const lockPath = 'static/pyodide/pyodide-lock.json';
	const lockData = JSON.parse(await readFile(lockPath, 'utf-8'));
	for (const pkg of Object.values(lockData.packages)) {
		pkg.file_name = new URL(
			pkg.file_name,
			`https://cdn.jsdelivr.net/pyodide/v${version}/full/`
		).href;
	}
	await writeFile(lockPath, JSON.stringify(lockData, null, 2));
} else {
	await downloadPackages();
	await copyPyodide();
	await downloadPyPIWheels();
	await verifyBundledWheels();
}
