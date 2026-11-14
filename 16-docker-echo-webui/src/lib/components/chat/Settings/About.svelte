<script lang="ts">
	import { WEBUI_VERSION } from '$lib/constants';
	import { config } from '$lib/stores';
	import { getContext } from 'svelte';

	import BuildVersion from '$lib/components/common/BuildVersion.svelte';
	import UserSettingRow from './UserSettingRow.svelte';
	import UserSettingSection from './UserSettingSection.svelte';

	const i18n: any = getContext('i18n');

	const openWebUiLicenseText = `Open WebUI License

Copyright (c) 2023- Open WebUI Inc. [Created by Timothy Jaeryang Baek]
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its
   contributors may be used to endorse or promote products derived from
   this software without specific prior written permission.

4. Notwithstanding any other provision of this License, and as a material
   condition of the rights granted herein, licensees are strictly prohibited
   from altering, removing, obscuring, or replacing any "Open WebUI"
   branding, including but not limited to the name, logo, or any visual,
   textual, or symbolic identifiers that distinguish the software and its
   interfaces, in any deployment or distribution, except in the following
   circumstances: (i) deployments or distributions where the total number
   of end users (defined as individual natural persons with direct access
   to the application) does not exceed fifty (50) within any rolling
   thirty (30) day period; (ii) the licensee has obtained specific prior
   written permission from the copyright holder; or (iii) where the
   licensee has obtained a duly executed enterprise license expressly
   permitting such modification. For all other cases, any removal or
   alteration of the "Open WebUI" branding shall constitute a material
   breach of license.

Materials governed by prior licenses retain those original license
terms, as specified in LICENSE_HISTORY.

By contributing to this project, you agree to the project's Contributor
License Agreement (CONTRIBUTOR_LICENSE_AGREEMENT).

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.`;
</script>

<div id="tab-about" class="flex flex-col h-full justify-between text-sm">
	<h2 class="text-sm font-medium text-gray-900 dark:text-white mb-4">
		{$i18n.t('settings.personal.about.title')}
	</h2>

	<div class="flex-1 min-h-0 overflow-y-auto scrollbar-hover pr-1.5">
		<UserSettingSection title="Versions" first>
			<UserSettingRow label="ECHO Framework">
				<span class="text-xs text-gray-700 dark:text-gray-200 font-mono">
					v{$config?.echo_version ?? WEBUI_VERSION}
				</span>
			</UserSettingRow>

			<UserSettingRow label="ECHO-UI">
				<div class="text-xs text-gray-700 dark:text-gray-200 font-mono">
					<BuildVersion />
				</div>
			</UserSettingRow>
		</UserSettingSection>

		<UserSettingSection title="Licences & Attributions">
			<div class="flex flex-col gap-3 text-xs text-gray-600 dark:text-gray-400">
				<div>
					<div class="font-medium text-gray-800 dark:text-gray-200">
						ECHO Framework / ECHO-UI
					</div>
					<div>
						Apache License 2.0 — Copyright © 2025-2026 Wilfried BARNAVON
					</div>
				</div>

				<div>
					<div class="font-medium text-gray-800 dark:text-gray-200">
						Fondation UI dérivée d'Open WebUI
					</div>
					<div>
						Open WebUI License — Copyright © 2023- Open WebUI Inc. [Created by Timothy Jaeryang
						Baek] — All rights reserved.
					</div>
					<div class="mt-0.5 text-gray-500 dark:text-gray-500">
						Conformément à la Clause 4 de la Open WebUI License, l'adaptation de l'identité
						visuelle est autorisée pour les déploiements n'excédant pas 50 utilisateurs finaux sur
						toute période glissante de 30 jours.
					</div>

					<details class="mt-1.5">
						<summary
							class="cursor-pointer select-none text-gray-500 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200"
						>
							Afficher le texte intégral de la Open WebUI License
						</summary>
						<pre
							class="mt-2 max-h-56 overflow-y-auto whitespace-pre-wrap rounded-lg border border-gray-200 bg-gray-50 p-2.5 text-[11px] leading-relaxed text-gray-600 dark:border-white/10 dark:bg-white/[0.03] dark:text-gray-300">{openWebUiLicenseText}</pre>
					</details>
				</div>

				<div class="text-gray-500 dark:text-gray-500">
					Twemoji —
					<a
						href="https://github.com/jdecked/twemoji"
						target="_blank"
						rel="noreferrer"
						class="underline hover:text-gray-700 dark:hover:text-gray-300">GitHub</a
					>,
					<a
						href="https://creativecommons.org/licenses/by/4.0/"
						target="_blank"
						rel="noreferrer"
						class="underline hover:text-gray-700 dark:hover:text-gray-300">CC-BY 4.0</a
					>.
				</div>
			</div>
		</UserSettingSection>
	</div>
</div>
