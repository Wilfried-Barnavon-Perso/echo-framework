# Documentation API Open WebUI (0.1.0)

None

---

## Table des Matières

1. [Endpoints](#endpoints)
2. [Modèles de Données (Schemas)](#modèles-de-données)

## Endpoints

### GET `/api/changelog`

**Tags:**

**Résumé:** Get App Changelog

---
### POST `/api/chat/actions/{action_id}`

**Tags:**

**Résumé:** Chat Action

**Paramètres URL / Query :**

- `action_id` (path) - **Requis** :

**Corps de la requête (Body) :**


---
### POST `/api/chat/completed`

**Tags:**

**Résumé:** Chat Completed

**Corps de la requête (Body) :**


---
### POST `/api/chat/completions`

**Tags:**

**Résumé:** Chat Completion

**Corps de la requête (Body) :**


---
### GET `/api/config`

**Tags:**

**Résumé:** Get App Config

---
### POST `/api/embeddings`

**Tags:**

**Résumé:** Embeddings

> OpenAI-compatible embeddings endpoint.

This handler:
  - Performs user/model checks and dispatches to the correct backend.
  - Supports OpenAI, Ollama, arena models, pipelines, and any compatible provider.

Args:
    request (Request): Request context.
    form_data (dict): OpenAI-like payload (e.g., {"model": "...", "input": [...]})
    user (UserModel): Authenticated user.

Returns:
    dict: OpenAI-compatible embeddings response.

**Corps de la requête (Body) :**


---
### GET `/api/models`

**Tags:**

**Résumé:** Get Models

**Paramètres URL / Query :**

- `refresh` (query) - Optionnel :

---
### GET `/api/models/base`

**Tags:**

**Résumé:** Get Base Models

---
### GET `/api/tasks`

**Tags:**

**Résumé:** List Tasks Endpoint

---
### GET `/api/tasks/chat/{chat_id}`

**Tags:**

**Résumé:** List Tasks By Chat Id Endpoint

**Paramètres URL / Query :**

- `chat_id` (path) - **Requis** :

---
### POST `/api/tasks/stop/{task_id}`

**Tags:**

**Résumé:** Stop Task Endpoint

**Paramètres URL / Query :**

- `task_id` (path) - **Requis** :

---
### GET `/api/usage`

**Tags:**

**Résumé:** Get Current Usage

> Get current usage statistics for Open WebUI.
This is an experimental endpoint and subject to change.

---
### GET `/api/v1/audio/config`

**Tags:** audio

**Résumé:** Get Audio Config

---
### POST `/api/v1/audio/config/update`

**Tags:** audio

**Résumé:** Update Audio Config

**Corps de la requête (Body) :**

- **tts** ([TTSConfigForm](#model-ttsconfigform)) *(required)*:
  - **OPENAI_API_BASE_URL** (string) *(required)*:
  - **OPENAI_API_KEY** (string) *(required)*:
  - **OPENAI_PARAMS** (any) :
  - **API_KEY** (string) *(required)*:
  - **ENGINE** (string) *(required)*:
  - **MODEL** (string) *(required)*:
  - **VOICE** (string) *(required)*:
  - **SPLIT_ON** (string) *(required)*:
  - **AZURE_SPEECH_REGION** (string) *(required)*:
  - **AZURE_SPEECH_BASE_URL** (string) *(required)*:
  - **AZURE_SPEECH_OUTPUT_FORMAT** (string) *(required)*:
- **stt** ([STTConfigForm](#model-sttconfigform)) *(required)*:
  - **OPENAI_API_BASE_URL** (string) *(required)*:
  - **OPENAI_API_KEY** (string) *(required)*:
  - **ENGINE** (string) *(required)*:
  - **MODEL** (string) *(required)*:
  - **SUPPORTED_CONTENT_TYPES** (array) :
  - **WHISPER_MODEL** (string) *(required)*:
  - **DEEPGRAM_API_KEY** (string) *(required)*:
  - **AZURE_API_KEY** (string) *(required)*:
  - **AZURE_REGION** (string) *(required)*:
  - **AZURE_LOCALES** (string) *(required)*:
  - **AZURE_BASE_URL** (string) *(required)*:
  - **AZURE_MAX_SPEAKERS** (string) *(required)*:
  - **MISTRAL_API_KEY** (string) *(required)*:
  - **MISTRAL_API_BASE_URL** (string) *(required)*:
  - **MISTRAL_USE_CHAT_COMPLETIONS** (boolean) *(required)*:

---
### GET `/api/v1/audio/models`

**Tags:** audio

**Résumé:** Get Models

---
### POST `/api/v1/audio/speech`

**Tags:** audio

**Résumé:** Speech

---
### POST `/api/v1/audio/transcriptions`

**Tags:** audio

**Résumé:** Transcription

**Corps de la requête (Body) :**

- **file** (string) *(required)*:
- **language** (any) :

---
### GET `/api/v1/audio/voices`

**Tags:** audio

**Résumé:** Get Voices

---
### GET `/api/v1/auths/`

**Tags:** auths

**Résumé:** Get Session User

---
### POST `/api/v1/auths/add`

**Tags:** auths

**Résumé:** Add User

**Corps de la requête (Body) :**

- **name** (string) *(required)*:
- **email** (string) *(required)*:
- **password** (string) *(required)*:
- **profile_image_url** (any) :
- **role** (any) :

---
### GET `/api/v1/auths/admin/config`

**Tags:** auths

**Résumé:** Get Admin Config

---
### POST `/api/v1/auths/admin/config`

**Tags:** auths

**Résumé:** Update Admin Config

**Corps de la requête (Body) :**

- **SHOW_ADMIN_DETAILS** (boolean) *(required)*:
- **ADMIN_EMAIL** (any) :
- **WEBUI_URL** (string) *(required)*:
- **ENABLE_SIGNUP** (boolean) *(required)*:
- **ENABLE_API_KEYS** (boolean) *(required)*:
- **ENABLE_API_KEYS_ENDPOINT_RESTRICTIONS** (boolean) *(required)*:
- **API_KEYS_ALLOWED_ENDPOINTS** (string) *(required)*:
- **DEFAULT_USER_ROLE** (string) *(required)*:
- **DEFAULT_GROUP_ID** (string) *(required)*:
- **JWT_EXPIRES_IN** (string) *(required)*:
- **ENABLE_COMMUNITY_SHARING** (boolean) *(required)*:
- **ENABLE_MESSAGE_RATING** (boolean) *(required)*:
- **ENABLE_FOLDERS** (boolean) *(required)*:
- **FOLDER_MAX_FILE_COUNT** (any) :
- **ENABLE_CHANNELS** (boolean) *(required)*:
- **ENABLE_MEMORIES** (boolean) *(required)*:
- **ENABLE_NOTES** (boolean) *(required)*:
- **ENABLE_USER_WEBHOOKS** (boolean) *(required)*:
- **ENABLE_USER_STATUS** (boolean) *(required)*:
- **PENDING_USER_OVERLAY_TITLE** (any) :
- **PENDING_USER_OVERLAY_CONTENT** (any) :
- **RESPONSE_WATERMARK** (any) :

---
### GET `/api/v1/auths/admin/config/ldap`

**Tags:** auths

**Résumé:** Get Ldap Config

---
### POST `/api/v1/auths/admin/config/ldap`

**Tags:** auths

**Résumé:** Update Ldap Config

**Corps de la requête (Body) :**

- **enable_ldap** (any) :

---
### GET `/api/v1/auths/admin/config/ldap/server`

**Tags:** auths

**Résumé:** Get Ldap Server

---
### POST `/api/v1/auths/admin/config/ldap/server`

**Tags:** auths

**Résumé:** Update Ldap Server

**Corps de la requête (Body) :**

- **label** (string) *(required)*:
- **host** (string) *(required)*:
- **port** (any) :
- **attribute_for_mail** (string) :
- **attribute_for_username** (string) :
- **app_dn** (string) *(required)*:
- **app_dn_password** (string) *(required)*:
- **search_base** (string) *(required)*:
- **search_filters** (string) :
- **use_tls** (boolean) :
- **certificate_path** (any) :
- **validate_cert** (boolean) :
- **ciphers** (any) :

---
### GET `/api/v1/auths/admin/details`

**Tags:** auths

**Résumé:** Get Admin Details

---
### GET `/api/v1/auths/api_key`

**Tags:** auths

**Résumé:** Get Api Key

---
### POST `/api/v1/auths/api_key`

**Tags:** auths

**Résumé:** Generate Api Key

---
### DELETE `/api/v1/auths/api_key`

**Tags:** auths

**Résumé:** Delete Api Key

---
### POST `/api/v1/auths/ldap`

**Tags:** auths

**Résumé:** Ldap Auth

**Corps de la requête (Body) :**

- **user** (string) *(required)*:
- **password** (string) *(required)*:

---
### POST `/api/v1/auths/signin`

**Tags:** auths

**Résumé:** Signin

**Corps de la requête (Body) :**

- **email** (string) *(required)*:
- **password** (string) *(required)*:

---
### GET `/api/v1/auths/signout`

**Tags:** auths

**Résumé:** Signout

---
### POST `/api/v1/auths/signup`

**Tags:** auths

**Résumé:** Signup

**Corps de la requête (Body) :**

- **name** (string) *(required)*:
- **email** (string) *(required)*:
- **password** (string) *(required)*:
- **profile_image_url** (any) :

---
### POST `/api/v1/auths/update/password`

**Tags:** auths

**Résumé:** Update Password

**Corps de la requête (Body) :**

- **password** (string) *(required)*:
- **new_password** (string) *(required)*:

---
### POST `/api/v1/auths/update/profile`

**Tags:** auths

**Résumé:** Update Profile

**Corps de la requête (Body) :**

- **profile_image_url** (string) *(required)*:
- **name** (string) *(required)*:
- **bio** (any) :
- **gender** (any) :
- **date_of_birth** (any) :

---
### POST `/api/v1/auths/update/timezone`

**Tags:** auths

**Résumé:** Update Timezone

**Corps de la requête (Body) :**

- **timezone** (string) *(required)*:

---
### GET `/api/v1/channels/`

**Tags:** channels

**Résumé:** Get Channels

---
### POST `/api/v1/channels/create`

**Tags:** channels

**Résumé:** Create New Channel

**Corps de la requête (Body) :**

- **name** (string) :
- **description** (any) :
- **is_private** (any) :
- **data** (any) :
- **meta** (any) :
- **access_control** (any) :
- **group_ids** (any) :
- **user_ids** (any) :
- **type** (any) :

---
### GET `/api/v1/channels/list`

**Tags:** channels

**Résumé:** Get All Channels

---
### GET `/api/v1/channels/users/{user_id}`

**Tags:** channels

**Résumé:** Get Dm Channel By User Id

**Paramètres URL / Query :**

- `user_id` (path) - **Requis** :

---
### GET `/api/v1/channels/webhooks/{webhook_id}/profile/image`

**Tags:** channels

**Résumé:** Get Webhook Profile Image

> Get webhook profile image by webhook ID.

**Paramètres URL / Query :**

- `webhook_id` (path) - **Requis** :

---
### POST `/api/v1/channels/webhooks/{webhook_id}/{token}`

**Tags:** channels

**Résumé:** Post Webhook Message

> Public endpoint to post messages via webhook. No authentication required.

**Paramètres URL / Query :**

- `webhook_id` (path) - **Requis** :
- `token` (path) - **Requis** :

**Corps de la requête (Body) :**

- **content** (string) *(required)*:

---
### GET `/api/v1/channels/{id}`

**Tags:** channels

**Résumé:** Get Channel By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### DELETE `/api/v1/channels/{id}/delete`

**Tags:** channels

**Résumé:** Delete Channel By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### GET `/api/v1/channels/{id}/members`

**Tags:** channels

**Résumé:** Get Channel Members By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :
- `query` (query) - Optionnel :
- `order_by` (query) - Optionnel :
- `direction` (query) - Optionnel :
- `page` (query) - Optionnel :

---
### POST `/api/v1/channels/{id}/members/active`

**Tags:** channels

**Résumé:** Update Is Active Member By Id And User Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

**Corps de la requête (Body) :**

- **is_active** (boolean) *(required)*:

---
### GET `/api/v1/channels/{id}/messages`

**Tags:** channels

**Résumé:** Get Channel Messages

**Paramètres URL / Query :**

- `id` (path) - **Requis** :
- `skip` (query) - Optionnel :
- `limit` (query) - Optionnel :

---
### GET `/api/v1/channels/{id}/messages/pinned`

**Tags:** channels

**Résumé:** Get Pinned Channel Messages

**Paramètres URL / Query :**

- `id` (path) - **Requis** :
- `page` (query) - Optionnel :

---
### POST `/api/v1/channels/{id}/messages/post`

**Tags:** channels

**Résumé:** Post New Message

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

**Corps de la requête (Body) :**

- **temp_id** (any) :
- **content** (string) *(required)*:
- **reply_to_id** (any) :
- **parent_id** (any) :
- **data** (any) :
- **meta** (any) :

---
### GET `/api/v1/channels/{id}/messages/{message_id}`

**Tags:** channels

**Résumé:** Get Channel Message

**Paramètres URL / Query :**

- `id` (path) - **Requis** :
- `message_id` (path) - **Requis** :

---
### GET `/api/v1/channels/{id}/messages/{message_id}/data`

**Tags:** channels

**Résumé:** Get Channel Message Data

**Paramètres URL / Query :**

- `id` (path) - **Requis** :
- `message_id` (path) - **Requis** :

---
### DELETE `/api/v1/channels/{id}/messages/{message_id}/delete`

**Tags:** channels

**Résumé:** Delete Message By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :
- `message_id` (path) - **Requis** :

---
### POST `/api/v1/channels/{id}/messages/{message_id}/pin`

**Tags:** channels

**Résumé:** Pin Channel Message

**Paramètres URL / Query :**

- `id` (path) - **Requis** :
- `message_id` (path) - **Requis** :

**Corps de la requête (Body) :**

- **is_pinned** (boolean) *(required)*:

---
### POST `/api/v1/channels/{id}/messages/{message_id}/reactions/add`

**Tags:** channels

**Résumé:** Add Reaction To Message

**Paramètres URL / Query :**

- `id` (path) - **Requis** :
- `message_id` (path) - **Requis** :

**Corps de la requête (Body) :**

- **name** (string) *(required)*:

---
### POST `/api/v1/channels/{id}/messages/{message_id}/reactions/remove`

**Tags:** channels

**Résumé:** Remove Reaction By Id And User Id And Name

**Paramètres URL / Query :**

- `id` (path) - **Requis** :
- `message_id` (path) - **Requis** :

**Corps de la requête (Body) :**

- **name** (string) *(required)*:

---
### GET `/api/v1/channels/{id}/messages/{message_id}/thread`

**Tags:** channels

**Résumé:** Get Channel Thread Messages

**Paramètres URL / Query :**

- `id` (path) - **Requis** :
- `message_id` (path) - **Requis** :
- `skip` (query) - Optionnel :
- `limit` (query) - Optionnel :

---
### POST `/api/v1/channels/{id}/messages/{message_id}/update`

**Tags:** channels

**Résumé:** Update Message By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :
- `message_id` (path) - **Requis** :

**Corps de la requête (Body) :**

- **temp_id** (any) :
- **content** (string) *(required)*:
- **reply_to_id** (any) :
- **parent_id** (any) :
- **data** (any) :
- **meta** (any) :

---
### POST `/api/v1/channels/{id}/update`

**Tags:** channels

**Résumé:** Update Channel By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

**Corps de la requête (Body) :**

- **name** (string) :
- **description** (any) :
- **is_private** (any) :
- **data** (any) :
- **meta** (any) :
- **access_control** (any) :
- **group_ids** (any) :
- **user_ids** (any) :

---
### POST `/api/v1/channels/{id}/update/members/add`

**Tags:** channels

**Résumé:** Add Members By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

**Corps de la requête (Body) :**

- **user_ids** (array) :
- **group_ids** (array) :

---
### POST `/api/v1/channels/{id}/update/members/remove`

**Tags:** channels

**Résumé:** Remove Members By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

**Corps de la requête (Body) :**

- **user_ids** (array) :

---
### GET `/api/v1/channels/{id}/webhooks`

**Tags:** channels

**Résumé:** Get Channel Webhooks

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### POST `/api/v1/channels/{id}/webhooks/create`

**Tags:** channels

**Résumé:** Create Channel Webhook

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

**Corps de la requête (Body) :**

- **name** (string) *(required)*:
- **profile_image_url** (any) :

---
### DELETE `/api/v1/channels/{id}/webhooks/{webhook_id}/delete`

**Tags:** channels

**Résumé:** Delete Channel Webhook

**Paramètres URL / Query :**

- `id` (path) - **Requis** :
- `webhook_id` (path) - **Requis** :

---
### POST `/api/v1/channels/{id}/webhooks/{webhook_id}/update`

**Tags:** channels

**Résumé:** Update Channel Webhook

**Paramètres URL / Query :**

- `id` (path) - **Requis** :
- `webhook_id` (path) - **Requis** :

**Corps de la requête (Body) :**

- **name** (string) *(required)*:
- **profile_image_url** (any) :

---
### POST `/api/v1/chat/completions`

**Tags:**

**Résumé:** Chat Completion

**Corps de la requête (Body) :**


---
### GET `/api/v1/chats/`

**Tags:** chats

**Résumé:** Get Session User Chat List

**Paramètres URL / Query :**

- `page` (query) - Optionnel :
- `include_pinned` (query) - Optionnel :
- `include_folders` (query) - Optionnel :

---
### DELETE `/api/v1/chats/`

**Tags:** chats

**Résumé:** Delete All User Chats

---
### GET `/api/v1/chats/all`

**Tags:** chats

**Résumé:** Get User Chats

---
### GET `/api/v1/chats/all/archived`

**Tags:** chats

**Résumé:** Get User Archived Chats

---
### GET `/api/v1/chats/all/db`

**Tags:** chats

**Résumé:** Get All User Chats In Db

---
### GET `/api/v1/chats/all/tags`

**Tags:** chats

**Résumé:** Get All User Tags

---
### POST `/api/v1/chats/archive/all`

**Tags:** chats

**Résumé:** Archive All Chats

---
### GET `/api/v1/chats/archived`

**Tags:** chats

**Résumé:** Get Archived Session User Chat List

**Paramètres URL / Query :**

- `page` (query) - Optionnel :
- `query` (query) - Optionnel :
- `order_by` (query) - Optionnel :
- `direction` (query) - Optionnel :

---
### GET `/api/v1/chats/folder/{folder_id}`

**Tags:** chats

**Résumé:** Get Chats By Folder Id

**Paramètres URL / Query :**

- `folder_id` (path) - **Requis** :

---
### GET `/api/v1/chats/folder/{folder_id}/list`

**Tags:** chats

**Résumé:** Get Chat List By Folder Id

**Paramètres URL / Query :**

- `folder_id` (path) - **Requis** :
- `page` (query) - Optionnel :

---
### POST `/api/v1/chats/import`

**Tags:** chats

**Résumé:** Import Chats

**Corps de la requête (Body) :**

- **chats** (array) *(required)*:

---
### GET `/api/v1/chats/list`

**Tags:** chats

**Résumé:** Get Session User Chat List

**Paramètres URL / Query :**

- `page` (query) - Optionnel :
- `include_pinned` (query) - Optionnel :
- `include_folders` (query) - Optionnel :

---
### GET `/api/v1/chats/list/user/{user_id}`

**Tags:** chats

**Résumé:** Get User Chat List By User Id

**Paramètres URL / Query :**

- `user_id` (path) - **Requis** :
- `page` (query) - Optionnel :
- `query` (query) - Optionnel :
- `order_by` (query) - Optionnel :
- `direction` (query) - Optionnel :

---
### POST `/api/v1/chats/new`

**Tags:** chats

**Résumé:** Create New Chat

**Corps de la requête (Body) :**

- **chat** (object) *(required)*:
- **folder_id** (any) :

---
### GET `/api/v1/chats/pinned`

**Tags:** chats

**Résumé:** Get User Pinned Chats

---
### GET `/api/v1/chats/search`

**Tags:** chats

**Résumé:** Search User Chats

**Paramètres URL / Query :**

- `text` (query) - **Requis** :
- `page` (query) - Optionnel :

---
### GET `/api/v1/chats/share/{share_id}`

**Tags:** chats

**Résumé:** Get Shared Chat By Id

**Paramètres URL / Query :**

- `share_id` (path) - **Requis** :

---
### GET `/api/v1/chats/stats/export`

**Tags:** chats

**Résumé:** Export Chat Stats

**Paramètres URL / Query :**

- `updated_at` (query) - Optionnel :
- `page` (query) - Optionnel :
- `stream` (query) - Optionnel :

---
### GET `/api/v1/chats/stats/export/{chat_id}`

**Tags:** chats

**Résumé:** Export Single Chat Stats

> Export stats for exactly one chat by ID.
Returns ChatStatsExport for the specified chat.

**Paramètres URL / Query :**

- `chat_id` (path) - **Requis** :

---
### GET `/api/v1/chats/stats/usage`

**Tags:** chats

**Résumé:** Get Session User Chat Usage Stats

**Paramètres URL / Query :**

- `items_per_page` (query) - Optionnel :
- `page` (query) - Optionnel :

---
### POST `/api/v1/chats/tags`

**Tags:** chats

**Résumé:** Get User Chat List By Tag Name

**Corps de la requête (Body) :**

- **name** (string) *(required)*:
- **skip** (any) :
- **limit** (any) :

---
### POST `/api/v1/chats/unarchive/all`

**Tags:** chats

**Résumé:** Unarchive All Chats

---
### GET `/api/v1/chats/{id}`

**Tags:** chats

**Résumé:** Get Chat By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### POST `/api/v1/chats/{id}`

**Tags:** chats

**Résumé:** Update Chat By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

**Corps de la requête (Body) :**

- **chat** (object) *(required)*:
- **folder_id** (any) :

---
### DELETE `/api/v1/chats/{id}`

**Tags:** chats

**Résumé:** Delete Chat By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### POST `/api/v1/chats/{id}/archive`

**Tags:** chats

**Résumé:** Archive Chat By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### POST `/api/v1/chats/{id}/clone`

**Tags:** chats

**Résumé:** Clone Chat By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

**Corps de la requête (Body) :**

- **title** (any) :

---
### POST `/api/v1/chats/{id}/clone/shared`

**Tags:** chats

**Résumé:** Clone Shared Chat By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### POST `/api/v1/chats/{id}/folder`

**Tags:** chats

**Résumé:** Update Chat Folder Id By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

**Corps de la requête (Body) :**

- **folder_id** (any) :

---
### POST `/api/v1/chats/{id}/messages/{message_id}`

**Tags:** chats

**Résumé:** Update Chat Message By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :
- `message_id` (path) - **Requis** :

**Corps de la requête (Body) :**

- **content** (string) *(required)*:

---
### POST `/api/v1/chats/{id}/messages/{message_id}/event`

**Tags:** chats

**Résumé:** Send Chat Message Event By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :
- `message_id` (path) - **Requis** :

**Corps de la requête (Body) :**

- **type** (string) *(required)*:
- **data** (object) *(required)*:

---
### POST `/api/v1/chats/{id}/pin`

**Tags:** chats

**Résumé:** Pin Chat By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### GET `/api/v1/chats/{id}/pinned`

**Tags:** chats

**Résumé:** Get Pinned Status By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### POST `/api/v1/chats/{id}/share`

**Tags:** chats

**Résumé:** Share Chat By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### DELETE `/api/v1/chats/{id}/share`

**Tags:** chats

**Résumé:** Delete Shared Chat By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### GET `/api/v1/chats/{id}/tags`

**Tags:** chats

**Résumé:** Get Chat Tags By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### POST `/api/v1/chats/{id}/tags`

**Tags:** chats

**Résumé:** Add Tag By Id And Tag Name

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

**Corps de la requête (Body) :**

- **name** (string) *(required)*:

---
### DELETE `/api/v1/chats/{id}/tags`

**Tags:** chats

**Résumé:** Delete Tag By Id And Tag Name

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

**Corps de la requête (Body) :**

- **name** (string) *(required)*:

---
### DELETE `/api/v1/chats/{id}/tags/all`

**Tags:** chats

**Résumé:** Delete All Tags By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### GET `/api/v1/configs/banners`

**Tags:** configs

**Résumé:** Get Banners

---
### POST `/api/v1/configs/banners`

**Tags:** configs

**Résumé:** Set Banners

**Corps de la requête (Body) :**

- **banners** (array) *(required)*:

---
### GET `/api/v1/configs/code_execution`

**Tags:** configs

**Résumé:** Get Code Execution Config

---
### POST `/api/v1/configs/code_execution`

**Tags:** configs

**Résumé:** Set Code Execution Config

**Corps de la requête (Body) :**

- **ENABLE_CODE_EXECUTION** (boolean) *(required)*:
- **CODE_EXECUTION_ENGINE** (string) *(required)*:
- **CODE_EXECUTION_JUPYTER_URL** (any) *(required)*:
- **CODE_EXECUTION_JUPYTER_AUTH** (any) *(required)*:
- **CODE_EXECUTION_JUPYTER_AUTH_TOKEN** (any) *(required)*:
- **CODE_EXECUTION_JUPYTER_AUTH_PASSWORD** (any) *(required)*:
- **CODE_EXECUTION_JUPYTER_TIMEOUT** (any) *(required)*:
- **ENABLE_CODE_INTERPRETER** (boolean) *(required)*:
- **CODE_INTERPRETER_ENGINE** (string) *(required)*:
- **CODE_INTERPRETER_PROMPT_TEMPLATE** (any) *(required)*:
- **CODE_INTERPRETER_JUPYTER_URL** (any) *(required)*:
- **CODE_INTERPRETER_JUPYTER_AUTH** (any) *(required)*:
- **CODE_INTERPRETER_JUPYTER_AUTH_TOKEN** (any) *(required)*:
- **CODE_INTERPRETER_JUPYTER_AUTH_PASSWORD** (any) *(required)*:
- **CODE_INTERPRETER_JUPYTER_TIMEOUT** (any) *(required)*:

---
### GET `/api/v1/configs/connections`

**Tags:** configs

**Résumé:** Get Connections Config

---
### POST `/api/v1/configs/connections`

**Tags:** configs

**Résumé:** Set Connections Config

**Corps de la requête (Body) :**

- **ENABLE_DIRECT_CONNECTIONS** (boolean) *(required)*:
- **ENABLE_BASE_MODELS_CACHE** (boolean) *(required)*:

---
### GET `/api/v1/configs/export`

**Tags:** configs

**Résumé:** Export Config

---
### POST `/api/v1/configs/import`

**Tags:** configs

**Résumé:** Import Config

**Corps de la requête (Body) :**

- **config** (object) *(required)*:

---
### GET `/api/v1/configs/models`

**Tags:** configs

**Résumé:** Get Models Config

---
### POST `/api/v1/configs/models`

**Tags:** configs

**Résumé:** Set Models Config

**Corps de la requête (Body) :**

- **DEFAULT_MODELS** (any) *(required)*:
- **DEFAULT_PINNED_MODELS** (any) *(required)*:
- **MODEL_ORDER_LIST** (any) *(required)*:

---
### POST `/api/v1/configs/oauth/clients/register`

**Tags:** configs

**Résumé:** Register Oauth Client

**Paramètres URL / Query :**

- `type` (query) - Optionnel :

**Corps de la requête (Body) :**

- **url** (string) *(required)*:
- **client_id** (string) *(required)*:
- **client_name** (any) :

---
### POST `/api/v1/configs/suggestions`

**Tags:** configs

**Résumé:** Set Default Suggestions

**Corps de la requête (Body) :**

- **suggestions** (array) *(required)*:

---
### GET `/api/v1/configs/tool_servers`

**Tags:** configs

**Résumé:** Get Tool Servers Config

---
### POST `/api/v1/configs/tool_servers`

**Tags:** configs

**Résumé:** Set Tool Servers Config

**Corps de la requête (Body) :**

- **TOOL_SERVER_CONNECTIONS** (array) *(required)*:

---
### POST `/api/v1/configs/tool_servers/verify`

**Tags:** configs

**Résumé:** Verify Tool Servers Config

> Verify the connection to the tool server.

**Corps de la requête (Body) :**

- **url** (string) *(required)*:
- **path** (string) *(required)*:
- **type** (any) :
- **auth_type** (any) *(required)*:
- **headers** (any) :
- **key** (any) *(required)*:
- **config** (any) *(required)*:

---
### POST `/api/v1/embeddings`

**Tags:**

**Résumé:** Embeddings

> OpenAI-compatible embeddings endpoint.

This handler:
  - Performs user/model checks and dispatches to the correct backend.
  - Supports OpenAI, Ollama, arena models, pipelines, and any compatible provider.

Args:
    request (Request): Request context.
    form_data (dict): OpenAI-like payload (e.g., {"model": "...", "input": [...]})
    user (UserModel): Authenticated user.

Returns:
    dict: OpenAI-compatible embeddings response.

**Corps de la requête (Body) :**


---
### GET `/api/v1/evaluations/config`

**Tags:** evaluations

**Résumé:** Get Config

---
### POST `/api/v1/evaluations/config`

**Tags:** evaluations

**Résumé:** Update Config

**Corps de la requête (Body) :**

- **ENABLE_EVALUATION_ARENA_MODELS** (any) :
- **EVALUATION_ARENA_MODELS** (any) :

---
### POST `/api/v1/evaluations/feedback`

**Tags:** evaluations

**Résumé:** Create Feedback

**Corps de la requête (Body) :**

- **type** (string) *(required)*:
- **data** (any) :
- **meta** (any) :
- **snapshot** (any) :

---
### GET `/api/v1/evaluations/feedback/{id}`

**Tags:** evaluations

**Résumé:** Get Feedback By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### POST `/api/v1/evaluations/feedback/{id}`

**Tags:** evaluations

**Résumé:** Update Feedback By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

**Corps de la requête (Body) :**

- **type** (string) *(required)*:
- **data** (any) :
- **meta** (any) :
- **snapshot** (any) :

---
### DELETE `/api/v1/evaluations/feedback/{id}`

**Tags:** evaluations

**Résumé:** Delete Feedback By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### DELETE `/api/v1/evaluations/feedbacks`

**Tags:** evaluations

**Résumé:** Delete Feedbacks

---
### GET `/api/v1/evaluations/feedbacks/all`

**Tags:** evaluations

**Résumé:** Get All Feedbacks

---
### DELETE `/api/v1/evaluations/feedbacks/all`

**Tags:** evaluations

**Résumé:** Delete All Feedbacks

---
### GET `/api/v1/evaluations/feedbacks/all/export`

**Tags:** evaluations

**Résumé:** Export All Feedbacks

---
### GET `/api/v1/evaluations/feedbacks/all/ids`

**Tags:** evaluations

**Résumé:** Get All Feedback Ids

---
### GET `/api/v1/evaluations/feedbacks/list`

**Tags:** evaluations

**Résumé:** Get Feedbacks

**Paramètres URL / Query :**

- `order_by` (query) - Optionnel :
- `direction` (query) - Optionnel :
- `page` (query) - Optionnel :

---
### GET `/api/v1/evaluations/feedbacks/user`

**Tags:** evaluations

**Résumé:** Get Feedbacks

---
### GET `/api/v1/evaluations/leaderboard`

**Tags:** evaluations

**Résumé:** Get Leaderboard

> Get model leaderboard with Elo ratings. Query filters by tag similarity.

**Paramètres URL / Query :**

- `query` (query) - Optionnel :

---
### GET `/api/v1/evaluations/leaderboard/{model_id}/history`

**Tags:** evaluations

**Résumé:** Get Model History

> Get daily win/loss history for a specific model.

**Paramètres URL / Query :**

- `model_id` (path) - **Requis** :
- `days` (query) - Optionnel :

---
### POST `/api/v1/files/`

**Tags:** files

**Résumé:** Upload File

**Paramètres URL / Query :**

- `process` (query) - Optionnel :
- `process_in_background` (query) - Optionnel :

**Corps de la requête (Body) :**

- **file** (string) *(required)*:
- **metadata** (any) :

---
### GET `/api/v1/files/`

**Tags:** files

**Résumé:** List Files

**Paramètres URL / Query :**

- `content` (query) - Optionnel :

---
### DELETE `/api/v1/files/all`

**Tags:** files

**Résumé:** Delete All Files

---
### GET `/api/v1/files/search`

**Tags:** files

**Résumé:** Search Files

> Search for files by filename with support for wildcard patterns.
Uses SQL-based filtering with pagination for better performance.

**Paramètres URL / Query :**

- `filename` (query) - **Requis** : Filename pattern to search for. Supports wildcards such as '*.txt'
- `content` (query) - Optionnel :
- `skip` (query) - Optionnel : Number of files to skip
- `limit` (query) - Optionnel : Maximum number of files to return

---
### GET `/api/v1/files/{id}`

**Tags:** files

**Résumé:** Get File By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### DELETE `/api/v1/files/{id}`

**Tags:** files

**Résumé:** Delete File By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### GET `/api/v1/files/{id}/content`

**Tags:** files

**Résumé:** Get File Content By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :
- `attachment` (query) - Optionnel :

---
### GET `/api/v1/files/{id}/content/html`

**Tags:** files

**Résumé:** Get Html File Content By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### GET `/api/v1/files/{id}/content/{file_name}`

**Tags:** files

**Résumé:** Get File Content By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### GET `/api/v1/files/{id}/data/content`

**Tags:** files

**Résumé:** Get File Data Content By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### POST `/api/v1/files/{id}/data/content/update`

**Tags:** files

**Résumé:** Update File Data Content By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

**Corps de la requête (Body) :**

- **content** (string) *(required)*:

---
### GET `/api/v1/files/{id}/process/status`

**Tags:** files

**Résumé:** Get File Process Status

**Paramètres URL / Query :**

- `id` (path) - **Requis** :
- `stream` (query) - Optionnel :

---
### GET `/api/v1/folders/`

**Tags:** folders

**Résumé:** Get Folders

---
### POST `/api/v1/folders/`

**Tags:** folders

**Résumé:** Create Folder

**Corps de la requête (Body) :**

- **name** (string) *(required)*:
- **data** (any) :
- **meta** (any) :

---
### GET `/api/v1/folders/{id}`

**Tags:** folders

**Résumé:** Get Folder By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### DELETE `/api/v1/folders/{id}`

**Tags:** folders

**Résumé:** Delete Folder By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :
- `delete_contents` (query) - Optionnel :

---
### POST `/api/v1/folders/{id}/update`

**Tags:** folders

**Résumé:** Update Folder Name By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

**Corps de la requête (Body) :**

- **name** (any) :
- **data** (any) :
- **meta** (any) :

---
### POST `/api/v1/folders/{id}/update/expanded`

**Tags:** folders

**Résumé:** Update Folder Is Expanded By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

**Corps de la requête (Body) :**

- **is_expanded** (boolean) *(required)*:

---
### POST `/api/v1/folders/{id}/update/parent`

**Tags:** folders

**Résumé:** Update Folder Parent Id By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

**Corps de la requête (Body) :**

- **parent_id** (any) :

---
### GET `/api/v1/functions/`

**Tags:** functions

**Résumé:** Get Functions

---
### POST `/api/v1/functions/create`

**Tags:** functions

**Résumé:** Create New Function

**Corps de la requête (Body) :**

- **id** (string) *(required)*:
- **name** (string) *(required)*:
- **content** (string) *(required)*:
- **meta** ([FunctionMeta](#model-functionmeta)) *(required)*:
  - **description** (any) :
  - **manifest** (any) :

---
### GET `/api/v1/functions/export`

**Tags:** functions

**Résumé:** Get Functions

**Paramètres URL / Query :**

- `include_valves` (query) - Optionnel :

---
### GET `/api/v1/functions/id/{id}`

**Tags:** functions

**Résumé:** Get Function By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### DELETE `/api/v1/functions/id/{id}/delete`

**Tags:** functions

**Résumé:** Delete Function By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### POST `/api/v1/functions/id/{id}/toggle`

**Tags:** functions

**Résumé:** Toggle Function By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### POST `/api/v1/functions/id/{id}/toggle/global`

**Tags:** functions

**Résumé:** Toggle Global By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### POST `/api/v1/functions/id/{id}/update`

**Tags:** functions

**Résumé:** Update Function By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

**Corps de la requête (Body) :**

- **id** (string) *(required)*:
- **name** (string) *(required)*:
- **content** (string) *(required)*:
- **meta** ([FunctionMeta](#model-functionmeta)) *(required)*:
  - **description** (any) :
  - **manifest** (any) :

---
### GET `/api/v1/functions/id/{id}/valves`

**Tags:** functions

**Résumé:** Get Function Valves By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### GET `/api/v1/functions/id/{id}/valves/spec`

**Tags:** functions

**Résumé:** Get Function Valves Spec By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### POST `/api/v1/functions/id/{id}/valves/update`

**Tags:** functions

**Résumé:** Update Function Valves By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

**Corps de la requête (Body) :**


---
### GET `/api/v1/functions/id/{id}/valves/user`

**Tags:** functions

**Résumé:** Get Function User Valves By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### GET `/api/v1/functions/id/{id}/valves/user/spec`

**Tags:** functions

**Résumé:** Get Function User Valves Spec By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### POST `/api/v1/functions/id/{id}/valves/user/update`

**Tags:** functions

**Résumé:** Update Function User Valves By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

**Corps de la requête (Body) :**


---
### GET `/api/v1/functions/list`

**Tags:** functions

**Résumé:** Get Function List

---
### POST `/api/v1/functions/load/url`

**Tags:** functions

**Résumé:** Load Function From Url

**Corps de la requête (Body) :**

- **url** (string) *(required)*:

---
### POST `/api/v1/functions/sync`

**Tags:** functions

**Résumé:** Sync Functions

**Corps de la requête (Body) :**

- **functions** (array) :

---
### GET `/api/v1/groups/`

**Tags:** groups

**Résumé:** Get Groups

**Paramètres URL / Query :**

- `share` (query) - Optionnel :

---
### POST `/api/v1/groups/create`

**Tags:** groups

**Résumé:** Create New Group

**Corps de la requête (Body) :**

- **name** (string) *(required)*:
- **description** (string) *(required)*:
- **permissions** (any) :
- **data** (any) :

---
### GET `/api/v1/groups/id/{id}`

**Tags:** groups

**Résumé:** Get Group By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### DELETE `/api/v1/groups/id/{id}/delete`

**Tags:** groups

**Résumé:** Delete Group By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### GET `/api/v1/groups/id/{id}/export`

**Tags:** groups

**Résumé:** Export Group By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### POST `/api/v1/groups/id/{id}/update`

**Tags:** groups

**Résumé:** Update Group By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

**Corps de la requête (Body) :**

- **name** (string) *(required)*:
- **description** (string) *(required)*:
- **permissions** (any) :
- **data** (any) :

---
### POST `/api/v1/groups/id/{id}/users`

**Tags:** groups

**Résumé:** Get Users In Group

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### POST `/api/v1/groups/id/{id}/users/add`

**Tags:** groups

**Résumé:** Add User To Group

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

**Corps de la requête (Body) :**

- **user_ids** (any) :

---
### POST `/api/v1/groups/id/{id}/users/remove`

**Tags:** groups

**Résumé:** Remove Users From Group

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

**Corps de la requête (Body) :**

- **user_ids** (any) :

---
### GET `/api/v1/images/config`

**Tags:** images

**Résumé:** Get Config

---
### POST `/api/v1/images/config/update`

**Tags:** images

**Résumé:** Update Config

**Corps de la requête (Body) :**

- **ENABLE_IMAGE_GENERATION** (boolean) *(required)*:
- **ENABLE_IMAGE_PROMPT_GENERATION** (boolean) *(required)*:
- **IMAGE_GENERATION_ENGINE** (string) *(required)*:
- **IMAGE_GENERATION_MODEL** (string) *(required)*:
- **IMAGE_SIZE** (any) *(required)*:
- **IMAGE_STEPS** (any) *(required)*:
- **IMAGES_OPENAI_API_BASE_URL** (string) *(required)*:
- **IMAGES_OPENAI_API_KEY** (string) *(required)*:
- **IMAGES_OPENAI_API_VERSION** (string) *(required)*:
- **IMAGES_OPENAI_API_PARAMS** (any) *(required)*:
- **AUTOMATIC1111_BASE_URL** (string) *(required)*:
- **AUTOMATIC1111_API_AUTH** (any) *(required)*:
- **AUTOMATIC1111_PARAMS** (any) *(required)*:
- **COMFYUI_BASE_URL** (string) *(required)*:
- **COMFYUI_API_KEY** (string) *(required)*:
- **COMFYUI_WORKFLOW** (string) *(required)*:
- **COMFYUI_WORKFLOW_NODES** (array) *(required)*:
- **IMAGES_GEMINI_API_BASE_URL** (string) *(required)*:
- **IMAGES_GEMINI_API_KEY** (string) *(required)*:
- **IMAGES_GEMINI_ENDPOINT_METHOD** (string) *(required)*:
- **ENABLE_IMAGE_EDIT** (boolean) *(required)*:
- **IMAGE_EDIT_ENGINE** (string) *(required)*:
- **IMAGE_EDIT_MODEL** (string) *(required)*:
- **IMAGE_EDIT_SIZE** (any) *(required)*:
- **IMAGES_EDIT_OPENAI_API_BASE_URL** (string) *(required)*:
- **IMAGES_EDIT_OPENAI_API_KEY** (string) *(required)*:
- **IMAGES_EDIT_OPENAI_API_VERSION** (string) *(required)*:
- **IMAGES_EDIT_GEMINI_API_BASE_URL** (string) *(required)*:
- **IMAGES_EDIT_GEMINI_API_KEY** (string) *(required)*:
- **IMAGES_EDIT_COMFYUI_BASE_URL** (string) *(required)*:
- **IMAGES_EDIT_COMFYUI_API_KEY** (string) *(required)*:
- **IMAGES_EDIT_COMFYUI_WORKFLOW** (string) *(required)*:
- **IMAGES_EDIT_COMFYUI_WORKFLOW_NODES** (array) *(required)*:

---
### GET `/api/v1/images/config/url/verify`

**Tags:** images

**Résumé:** Verify Url

---
### POST `/api/v1/images/edit`

**Tags:** images

**Résumé:** Image Edits

**Corps de la requête (Body) :**

- **form_data** ([EditImageForm](#model-editimageform)) *(required)*:
  - **image** (any) *(required)*:
  - **prompt** (string) *(required)*:
  - **model** (any) :
  - **size** (any) :
  - **n** (any) :
  - **negative_prompt** (any) :
- **metadata** (any) :

---
### POST `/api/v1/images/generations`

**Tags:** images

**Résumé:** Generate Images

**Corps de la requête (Body) :**

- **model** (any) :
- **prompt** (string) *(required)*:
- **size** (any) :
- **n** (integer) :
- **steps** (any) :
- **negative_prompt** (any) :

---
### GET `/api/v1/images/models`

**Tags:** images

**Résumé:** Get Models

---
### GET `/api/v1/knowledge/`

**Tags:** knowledge

**Résumé:** Get Knowledge Bases

**Paramètres URL / Query :**

- `page` (query) - Optionnel :

---
### POST `/api/v1/knowledge/create`

**Tags:** knowledge

**Résumé:** Create New Knowledge

**Corps de la requête (Body) :**

- **name** (string) *(required)*:
- **description** (string) *(required)*:
- **access_control** (any) :

---
### POST `/api/v1/knowledge/metadata/reindex`

**Tags:** knowledge

**Résumé:** Reindex Knowledge Base Metadata Embeddings

> Batch embed all existing knowledge bases. Admin only.

---
### POST `/api/v1/knowledge/reindex`

**Tags:** knowledge

**Résumé:** Reindex Knowledge Files

---
### GET `/api/v1/knowledge/search`

**Tags:** knowledge

**Résumé:** Search Knowledge Bases

**Paramètres URL / Query :**

- `query` (query) - Optionnel :
- `view_option` (query) - Optionnel :
- `page` (query) - Optionnel :

---
### GET `/api/v1/knowledge/search/files`

**Tags:** knowledge

**Résumé:** Search Knowledge Files

**Paramètres URL / Query :**

- `query` (query) - Optionnel :
- `page` (query) - Optionnel :

---
### GET `/api/v1/knowledge/{id}`

**Tags:** knowledge

**Résumé:** Get Knowledge By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### DELETE `/api/v1/knowledge/{id}/delete`

**Tags:** knowledge

**Résumé:** Delete Knowledge By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### GET `/api/v1/knowledge/{id}/export`

**Tags:** knowledge

**Résumé:** Export Knowledge By Id

> Export a knowledge base as a zip file containing .txt files.
Admin only.

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### POST `/api/v1/knowledge/{id}/file/add`

**Tags:** knowledge

**Résumé:** Add File To Knowledge By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

**Corps de la requête (Body) :**

- **file_id** (string) *(required)*:

---
### POST `/api/v1/knowledge/{id}/file/remove`

**Tags:** knowledge

**Résumé:** Remove File From Knowledge By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :
- `delete_file` (query) - Optionnel :

**Corps de la requête (Body) :**

- **file_id** (string) *(required)*:

---
### POST `/api/v1/knowledge/{id}/file/update`

**Tags:** knowledge

**Résumé:** Update File From Knowledge By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

**Corps de la requête (Body) :**

- **file_id** (string) *(required)*:

---
### GET `/api/v1/knowledge/{id}/files`

**Tags:** knowledge

**Résumé:** Get Knowledge Files By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :
- `query` (query) - Optionnel :
- `view_option` (query) - Optionnel :
- `order_by` (query) - Optionnel :
- `direction` (query) - Optionnel :
- `page` (query) - Optionnel :

---
### POST `/api/v1/knowledge/{id}/files/batch/add`

**Tags:** knowledge

**Résumé:** Add Files To Knowledge Batch

> Add multiple files to a knowledge base

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

**Corps de la requête (Body) :**

- Liste de :
  - **file_id** (string) *(required)*:

---
### POST `/api/v1/knowledge/{id}/reset`

**Tags:** knowledge

**Résumé:** Reset Knowledge By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### POST `/api/v1/knowledge/{id}/update`

**Tags:** knowledge

**Résumé:** Update Knowledge By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

**Corps de la requête (Body) :**

- **name** (string) *(required)*:
- **description** (string) *(required)*:
- **access_control** (any) :

---
### GET `/api/v1/memories/`

**Tags:** memories

**Résumé:** Get Memories

---
### POST `/api/v1/memories/add`

**Tags:** memories

**Résumé:** Add Memory

**Corps de la requête (Body) :**

- **content** (string) *(required)*:

---
### DELETE `/api/v1/memories/delete/user`

**Tags:** memories

**Résumé:** Delete Memory By User Id

---
### GET `/api/v1/memories/ef`

**Tags:** memories

**Résumé:** Get Embeddings

---
### POST `/api/v1/memories/query`

**Tags:** memories

**Résumé:** Query Memory

**Corps de la requête (Body) :**

- **content** (string) *(required)*:
- **k** (any) :

---
### POST `/api/v1/memories/reset`

**Tags:** memories

**Résumé:** Reset Memory From Vector Db

---
### DELETE `/api/v1/memories/{memory_id}`

**Tags:** memories

**Résumé:** Delete Memory By Id

**Paramètres URL / Query :**

- `memory_id` (path) - **Requis** :

---
### POST `/api/v1/memories/{memory_id}/update`

**Tags:** memories

**Résumé:** Update Memory By Id

**Paramètres URL / Query :**

- `memory_id` (path) - **Requis** :

**Corps de la requête (Body) :**

- **content** (any) :

---
### GET `/api/v1/models`

**Tags:**

**Résumé:** Get Models

**Paramètres URL / Query :**

- `refresh` (query) - Optionnel :

---
### GET `/api/v1/models/base`

**Tags:** models

**Résumé:** Get Base Models

---
### POST `/api/v1/models/create`

**Tags:** models

**Résumé:** Create New Model

**Corps de la requête (Body) :**

- **id** (string) *(required)*:
- **base_model_id** (any) :
- **name** (string) *(required)*:
- **meta** ([ModelMeta](#model-modelmeta)) *(required)*:
  - **profile_image_url** (any) :
  - **description** (any) :
  - **capabilities** (any) :
- **params** ([ModelParams](#model-modelparams)) *(required)*:
- **access_control** (any) :
- **is_active** (boolean) :

---
### DELETE `/api/v1/models/delete/all`

**Tags:** models

**Résumé:** Delete All Models

---
### GET `/api/v1/models/export`

**Tags:** models

**Résumé:** Export Models

---
### POST `/api/v1/models/import`

**Tags:** models

**Résumé:** Import Models

**Corps de la requête (Body) :**

- **models** (array) *(required)*:

---
### GET `/api/v1/models/list`

**Tags:** models

**Résumé:** Get Models

**Paramètres URL / Query :**

- `query` (query) - Optionnel :
- `view_option` (query) - Optionnel :
- `tag` (query) - Optionnel :
- `order_by` (query) - Optionnel :
- `direction` (query) - Optionnel :
- `page` (query) - Optionnel :

---
### GET `/api/v1/models/model`

**Tags:** models

**Résumé:** Get Model By Id

**Paramètres URL / Query :**

- `id` (query) - **Requis** :

---
### POST `/api/v1/models/model/delete`

**Tags:** models

**Résumé:** Delete Model By Id

**Corps de la requête (Body) :**

- **id** (string) *(required)*:

---
### GET `/api/v1/models/model/profile/image`

**Tags:** models

**Résumé:** Get Model Profile Image

**Paramètres URL / Query :**

- `id` (query) - **Requis** :

---
### POST `/api/v1/models/model/toggle`

**Tags:** models

**Résumé:** Toggle Model By Id

**Paramètres URL / Query :**

- `id` (query) - **Requis** :

---
### POST `/api/v1/models/model/update`

**Tags:** models

**Résumé:** Update Model By Id

**Corps de la requête (Body) :**

- **id** (string) *(required)*:
- **base_model_id** (any) :
- **name** (string) *(required)*:
- **meta** ([ModelMeta](#model-modelmeta)) *(required)*:
  - **profile_image_url** (any) :
  - **description** (any) :
  - **capabilities** (any) :
- **params** ([ModelParams](#model-modelparams)) *(required)*:
- **access_control** (any) :
- **is_active** (boolean) :

---
### POST `/api/v1/models/sync`

**Tags:** models

**Résumé:** Sync Models

**Corps de la requête (Body) :**

- **models** (array) :

---
### GET `/api/v1/models/tags`

**Tags:** models

**Résumé:** Get Model Tags

---
### GET `/api/v1/notes/`

**Tags:** notes

**Résumé:** Get Notes

**Paramètres URL / Query :**

- `page` (query) - Optionnel :

---
### POST `/api/v1/notes/create`

**Tags:** notes

**Résumé:** Create New Note

**Corps de la requête (Body) :**

- **title** (string) *(required)*:
- **data** (any) :
- **meta** (any) :
- **access_control** (any) :

---
### GET `/api/v1/notes/search`

**Tags:** notes

**Résumé:** Search Notes

**Paramètres URL / Query :**

- `query` (query) - Optionnel :
- `view_option` (query) - Optionnel :
- `permission` (query) - Optionnel :
- `order_by` (query) - Optionnel :
- `direction` (query) - Optionnel :
- `page` (query) - Optionnel :

---
### GET `/api/v1/notes/{id}`

**Tags:** notes

**Résumé:** Get Note By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### DELETE `/api/v1/notes/{id}/delete`

**Tags:** notes

**Résumé:** Delete Note By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### POST `/api/v1/notes/{id}/update`

**Tags:** notes

**Résumé:** Update Note By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

**Corps de la requête (Body) :**

- **title** (string) *(required)*:
- **data** (any) :
- **meta** (any) :
- **access_control** (any) :

---
### GET `/api/v1/pipelines/`

**Tags:** pipelines

**Résumé:** Get Pipelines

**Paramètres URL / Query :**

- `urlIdx` (query) - Optionnel :

---
### POST `/api/v1/pipelines/add`

**Tags:** pipelines

**Résumé:** Add Pipeline

**Corps de la requête (Body) :**

- **url** (string) *(required)*:
- **urlIdx** (integer) *(required)*:

---
### DELETE `/api/v1/pipelines/delete`

**Tags:** pipelines

**Résumé:** Delete Pipeline

**Corps de la requête (Body) :**

- **id** (string) *(required)*:
- **urlIdx** (integer) *(required)*:

---
### GET `/api/v1/pipelines/list`

**Tags:** pipelines

**Résumé:** Get Pipelines List

---
### POST `/api/v1/pipelines/upload`

**Tags:** pipelines

**Résumé:** Upload Pipeline

**Corps de la requête (Body) :**

- **urlIdx** (integer) *(required)*:
- **file** (string) *(required)*:

---
### GET `/api/v1/pipelines/{pipeline_id}/valves`

**Tags:** pipelines

**Résumé:** Get Pipeline Valves

**Paramètres URL / Query :**

- `pipeline_id` (path) - **Requis** :
- `urlIdx` (query) - **Requis** :

---
### GET `/api/v1/pipelines/{pipeline_id}/valves/spec`

**Tags:** pipelines

**Résumé:** Get Pipeline Valves Spec

**Paramètres URL / Query :**

- `pipeline_id` (path) - **Requis** :
- `urlIdx` (query) - **Requis** :

---
### POST `/api/v1/pipelines/{pipeline_id}/valves/update`

**Tags:** pipelines

**Résumé:** Update Pipeline Valves

**Paramètres URL / Query :**

- `pipeline_id` (path) - **Requis** :
- `urlIdx` (query) - **Requis** :

**Corps de la requête (Body) :**


---
### GET `/api/v1/prompts/`

**Tags:** prompts

**Résumé:** Get Prompts

---
### GET `/api/v1/prompts/command/{command}`

**Tags:** prompts

**Résumé:** Get Prompt By Command

**Paramètres URL / Query :**

- `command` (path) - **Requis** :

---
### DELETE `/api/v1/prompts/command/{command}/delete`

**Tags:** prompts

**Résumé:** Delete Prompt By Command

**Paramètres URL / Query :**

- `command` (path) - **Requis** :

---
### POST `/api/v1/prompts/command/{command}/update`

**Tags:** prompts

**Résumé:** Update Prompt By Command

**Paramètres URL / Query :**

- `command` (path) - **Requis** :

**Corps de la requête (Body) :**

- **command** (string) *(required)*:
- **title** (string) *(required)*:
- **content** (string) *(required)*:
- **access_control** (any) :

---
### POST `/api/v1/prompts/create`

**Tags:** prompts

**Résumé:** Create New Prompt

**Corps de la requête (Body) :**

- **command** (string) *(required)*:
- **title** (string) *(required)*:
- **content** (string) *(required)*:
- **access_control** (any) :

---
### GET `/api/v1/prompts/list`

**Tags:** prompts

**Résumé:** Get Prompt List

---
### GET `/api/v1/retrieval/`

**Tags:** retrieval

**Résumé:** Get Status

---
### GET `/api/v1/retrieval/config`

**Tags:** retrieval

**Résumé:** Get Rag Config

---
### POST `/api/v1/retrieval/config/update`

**Tags:** retrieval

**Résumé:** Update Rag Config

**Corps de la requête (Body) :**

- **RAG_TEMPLATE** (any) :
- **TOP_K** (any) :
- **BYPASS_EMBEDDING_AND_RETRIEVAL** (any) :
- **RAG_FULL_CONTEXT** (any) :
- **ENABLE_RAG_HYBRID_SEARCH** (any) :
- **ENABLE_RAG_HYBRID_SEARCH_ENRICHED_TEXTS** (any) :
- **TOP_K_RERANKER** (any) :
- **RELEVANCE_THRESHOLD** (any) :
- **HYBRID_BM25_WEIGHT** (any) :
- **CONTENT_EXTRACTION_ENGINE** (any) :
- **PDF_EXTRACT_IMAGES** (any) :
- **DATALAB_MARKER_API_KEY** (any) :
- **DATALAB_MARKER_API_BASE_URL** (any) :
- **DATALAB_MARKER_ADDITIONAL_CONFIG** (any) :
- **DATALAB_MARKER_SKIP_CACHE** (any) :
- **DATALAB_MARKER_FORCE_OCR** (any) :
- **DATALAB_MARKER_PAGINATE** (any) :
- **DATALAB_MARKER_STRIP_EXISTING_OCR** (any) :
- **DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION** (any) :
- **DATALAB_MARKER_FORMAT_LINES** (any) :
- **DATALAB_MARKER_USE_LLM** (any) :
- **DATALAB_MARKER_OUTPUT_FORMAT** (any) :
- **EXTERNAL_DOCUMENT_LOADER_URL** (any) :
- **EXTERNAL_DOCUMENT_LOADER_API_KEY** (any) :
- **TIKA_SERVER_URL** (any) :
- **DOCLING_SERVER_URL** (any) :
- **DOCLING_API_KEY** (any) :
- **DOCLING_PARAMS** (any) :
- **DOCUMENT_INTELLIGENCE_ENDPOINT** (any) :
- **DOCUMENT_INTELLIGENCE_KEY** (any) :
- **DOCUMENT_INTELLIGENCE_MODEL** (any) :
- **MISTRAL_OCR_API_BASE_URL** (any) :
- **MISTRAL_OCR_API_KEY** (any) :
- **MINERU_API_MODE** (any) :
- **MINERU_API_URL** (any) :
- **MINERU_API_KEY** (any) :
- **MINERU_API_TIMEOUT** (any) :
- **MINERU_PARAMS** (any) :
- **RAG_RERANKING_MODEL** (any) :
- **RAG_RERANKING_ENGINE** (any) :
- **RAG_EXTERNAL_RERANKER_URL** (any) :
- **RAG_EXTERNAL_RERANKER_API_KEY** (any) :
- **RAG_EXTERNAL_RERANKER_TIMEOUT** (any) :
- **TEXT_SPLITTER** (any) :
- **ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER** (any) :
- **CHUNK_SIZE** (any) :
- **CHUNK_MIN_SIZE_TARGET** (any) :
- **CHUNK_OVERLAP** (any) :
- **FILE_MAX_SIZE** (any) :
- **FILE_MAX_COUNT** (any) :
- **FILE_IMAGE_COMPRESSION_WIDTH** (any) :
- **FILE_IMAGE_COMPRESSION_HEIGHT** (any) :
- **ALLOWED_FILE_EXTENSIONS** (any) :
- **ENABLE_GOOGLE_DRIVE_INTEGRATION** (any) :
- **ENABLE_ONEDRIVE_INTEGRATION** (any) :
- **web** (any) :

---
### POST `/api/v1/retrieval/delete`

**Tags:** retrieval

**Résumé:** Delete Entries From Collection

**Corps de la requête (Body) :**

- **collection_name** (string) *(required)*:
- **file_id** (string) *(required)*:

---
### GET `/api/v1/retrieval/ef/{text}`

**Tags:** retrieval

**Résumé:** Get Embeddings

**Paramètres URL / Query :**

- `text` (path) - **Requis** :

---
### GET `/api/v1/retrieval/embedding`

**Tags:** retrieval

**Résumé:** Get Embedding Config

---
### POST `/api/v1/retrieval/embedding/update`

**Tags:** retrieval

**Résumé:** Update Embedding Config

**Corps de la requête (Body) :**

- **openai_config** (any) :
- **ollama_config** (any) :
- **azure_openai_config** (any) :
- **RAG_EMBEDDING_ENGINE** (string) *(required)*:
- **RAG_EMBEDDING_MODEL** (string) *(required)*:
- **RAG_EMBEDDING_BATCH_SIZE** (any) :
- **ENABLE_ASYNC_EMBEDDING** (any) :

---
### POST `/api/v1/retrieval/process/file`

**Tags:** retrieval

**Résumé:** Process File

> Process a file and save its content to the vector database.

**Corps de la requête (Body) :**

- **file_id** (string) *(required)*:
- **content** (any) :
- **collection_name** (any) :

---
### POST `/api/v1/retrieval/process/files/batch`

**Tags:** retrieval

**Résumé:** Process Files Batch

> Process a batch of files and save them to the vector database.

**Corps de la requête (Body) :**

- **files** (array) *(required)*:
- **collection_name** (string) *(required)*:

---
### POST `/api/v1/retrieval/process/text`

**Tags:** retrieval

**Résumé:** Process Text

**Corps de la requête (Body) :**

- **name** (string) *(required)*:
- **content** (string) *(required)*:
- **collection_name** (any) :

---
### POST `/api/v1/retrieval/process/web`

**Tags:** retrieval

**Résumé:** Process Web

**Paramètres URL / Query :**

- `process` (query) - Optionnel : Whether to process and save the content

**Corps de la requête (Body) :**

- **collection_name** (any) :
- **url** (string) *(required)*:

---
### POST `/api/v1/retrieval/process/web/search`

**Tags:** retrieval

**Résumé:** Process Web Search

**Corps de la requête (Body) :**

- **queries** (array) *(required)*:

---
### POST `/api/v1/retrieval/process/youtube`

**Tags:** retrieval

**Résumé:** Process Web

**Paramètres URL / Query :**

- `process` (query) - Optionnel : Whether to process and save the content

**Corps de la requête (Body) :**

- **collection_name** (any) :
- **url** (string) *(required)*:

---
### POST `/api/v1/retrieval/query/collection`

**Tags:** retrieval

**Résumé:** Query Collection Handler

**Corps de la requête (Body) :**

- **collection_names** (array) *(required)*:
- **query** (string) *(required)*:
- **k** (any) :
- **k_reranker** (any) :
- **r** (any) :
- **hybrid** (any) :
- **hybrid_bm25_weight** (any) :
- **enable_enriched_texts** (any) :

---
### POST `/api/v1/retrieval/query/doc`

**Tags:** retrieval

**Résumé:** Query Doc Handler

**Corps de la requête (Body) :**

- **collection_name** (string) *(required)*:
- **query** (string) *(required)*:
- **k** (any) :
- **k_reranker** (any) :
- **r** (any) :
- **hybrid** (any) :

---
### POST `/api/v1/retrieval/reset/db`

**Tags:** retrieval

**Résumé:** Reset Vector Db

---
### POST `/api/v1/retrieval/reset/uploads`

**Tags:** retrieval

**Résumé:** Reset Upload Dir

---
### POST `/api/v1/tasks/auto/completions`

**Tags:** tasks

**Résumé:** Generate Autocompletion

**Corps de la requête (Body) :**


---
### GET `/api/v1/tasks/config`

**Tags:** tasks

**Résumé:** Get Task Config

---
### POST `/api/v1/tasks/config/update`

**Tags:** tasks

**Résumé:** Update Task Config

**Corps de la requête (Body) :**

- **TASK_MODEL** (any) *(required)*:
- **TASK_MODEL_EXTERNAL** (any) *(required)*:
- **ENABLE_TITLE_GENERATION** (boolean) *(required)*:
- **TITLE_GENERATION_PROMPT_TEMPLATE** (string) *(required)*:
- **IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE** (string) *(required)*:
- **ENABLE_AUTOCOMPLETE_GENERATION** (boolean) *(required)*:
- **AUTOCOMPLETE_GENERATION_INPUT_MAX_LENGTH** (integer) *(required)*:
- **TAGS_GENERATION_PROMPT_TEMPLATE** (string) *(required)*:
- **FOLLOW_UP_GENERATION_PROMPT_TEMPLATE** (string) *(required)*:
- **ENABLE_FOLLOW_UP_GENERATION** (boolean) *(required)*:
- **ENABLE_TAGS_GENERATION** (boolean) *(required)*:
- **ENABLE_SEARCH_QUERY_GENERATION** (boolean) *(required)*:
- **ENABLE_RETRIEVAL_QUERY_GENERATION** (boolean) *(required)*:
- **QUERY_GENERATION_PROMPT_TEMPLATE** (string) *(required)*:
- **TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE** (string) *(required)*:
- **VOICE_MODE_PROMPT_TEMPLATE** (any) *(required)*:

---
### POST `/api/v1/tasks/emoji/completions`

**Tags:** tasks

**Résumé:** Generate Emoji

**Corps de la requête (Body) :**


---
### POST `/api/v1/tasks/follow_up/completions`

**Tags:** tasks

**Résumé:** Generate Follow Ups

**Corps de la requête (Body) :**


---
### POST `/api/v1/tasks/image_prompt/completions`

**Tags:** tasks

**Résumé:** Generate Image Prompt

**Corps de la requête (Body) :**


---
### POST `/api/v1/tasks/moa/completions`

**Tags:** tasks

**Résumé:** Generate Moa Response

**Corps de la requête (Body) :**


---
### POST `/api/v1/tasks/queries/completions`

**Tags:** tasks

**Résumé:** Generate Queries

**Corps de la requête (Body) :**


---
### POST `/api/v1/tasks/tags/completions`

**Tags:** tasks

**Résumé:** Generate Chat Tags

**Corps de la requête (Body) :**


---
### POST `/api/v1/tasks/title/completions`

**Tags:** tasks

**Résumé:** Generate Title

**Corps de la requête (Body) :**


---
### GET `/api/v1/tools/`

**Tags:** tools

**Résumé:** Get Tools

---
### POST `/api/v1/tools/create`

**Tags:** tools

**Résumé:** Create New Tools

**Corps de la requête (Body) :**

- **id** (string) *(required)*:
- **name** (string) *(required)*:
- **content** (string) *(required)*:
- **meta** ([ToolMeta](#model-toolmeta)) *(required)*:
  - **description** (any) :
  - **manifest** (any) :
- **access_control** (any) :

---
### GET `/api/v1/tools/export`

**Tags:** tools

**Résumé:** Export Tools

---
### GET `/api/v1/tools/id/{id}`

**Tags:** tools

**Résumé:** Get Tools By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### DELETE `/api/v1/tools/id/{id}/delete`

**Tags:** tools

**Résumé:** Delete Tools By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### POST `/api/v1/tools/id/{id}/update`

**Tags:** tools

**Résumé:** Update Tools By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

**Corps de la requête (Body) :**

- **id** (string) *(required)*:
- **name** (string) *(required)*:
- **content** (string) *(required)*:
- **meta** ([ToolMeta](#model-toolmeta)) *(required)*:
  - **description** (any) :
  - **manifest** (any) :
- **access_control** (any) :

---
### GET `/api/v1/tools/id/{id}/valves`

**Tags:** tools

**Résumé:** Get Tools Valves By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### GET `/api/v1/tools/id/{id}/valves/spec`

**Tags:** tools

**Résumé:** Get Tools Valves Spec By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### POST `/api/v1/tools/id/{id}/valves/update`

**Tags:** tools

**Résumé:** Update Tools Valves By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

**Corps de la requête (Body) :**


---
### GET `/api/v1/tools/id/{id}/valves/user`

**Tags:** tools

**Résumé:** Get Tools User Valves By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### GET `/api/v1/tools/id/{id}/valves/user/spec`

**Tags:** tools

**Résumé:** Get Tools User Valves Spec By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

---
### POST `/api/v1/tools/id/{id}/valves/user/update`

**Tags:** tools

**Résumé:** Update Tools User Valves By Id

**Paramètres URL / Query :**

- `id` (path) - **Requis** :

**Corps de la requête (Body) :**


---
### GET `/api/v1/tools/list`

**Tags:** tools

**Résumé:** Get Tool List

---
### POST `/api/v1/tools/load/url`

**Tags:** tools

**Résumé:** Load Tool From Url

**Corps de la requête (Body) :**

- **url** (string) *(required)*:

---
### GET `/api/v1/users/`

**Tags:** users

**Résumé:** Get Users

**Paramètres URL / Query :**

- `query` (query) - Optionnel :
- `order_by` (query) - Optionnel :
- `direction` (query) - Optionnel :
- `page` (query) - Optionnel :

---
### GET `/api/v1/users/all`

**Tags:** users

**Résumé:** Get All Users

---
### GET `/api/v1/users/default/permissions`

**Tags:** users

**Résumé:** Get Default User Permissions

---
### POST `/api/v1/users/default/permissions`

**Tags:** users

**Résumé:** Update Default User Permissions

**Corps de la requête (Body) :**

- **workspace** ([WorkspacePermissions](#model-workspacepermissions)) *(required)*:
  - **models** (boolean) :
  - **knowledge** (boolean) :
  - **prompts** (boolean) :
  - **tools** (boolean) :
  - **models_import** (boolean) :
  - **models_export** (boolean) :
  - **prompts_import** (boolean) :
  - **prompts_export** (boolean) :
  - **tools_import** (boolean) :
  - **tools_export** (boolean) :
- **sharing** ([SharingPermissions](#model-sharingpermissions)) *(required)*:
  - **models** (boolean) :
  - **public_models** (boolean) :
  - **knowledge** (boolean) :
  - **public_knowledge** (boolean) :
  - **prompts** (boolean) :
  - **public_prompts** (boolean) :
  - **tools** (boolean) :
  - **public_tools** (boolean) :
  - **notes** (boolean) :
  - **public_notes** (boolean) :
- **chat** ([ChatPermissions](#model-chatpermissions)) *(required)*:
  - **controls** (boolean) :
  - **valves** (boolean) :
  - **system_prompt** (boolean) :
  - **params** (boolean) :
  - **file_upload** (boolean) :
  - **delete** (boolean) :
  - **delete_message** (boolean) :
  - **continue_response** (boolean) :
  - **regenerate_response** (boolean) :
  - **rate_response** (boolean) :
  - **edit** (boolean) :
  - **share** (boolean) :
  - **export** (boolean) :
  - **stt** (boolean) :
  - **tts** (boolean) :
  - **call** (boolean) :
  - **multiple_models** (boolean) :
  - **temporary** (boolean) :
  - **temporary_enforced** (boolean) :
- **features** ([FeaturesPermissions](#model-featurespermissions)) *(required)*:
  - **api_keys** (boolean) :
  - **notes** (boolean) :
  - **channels** (boolean) :
  - **folders** (boolean) :
  - **direct_tool_servers** (boolean) :
  - **web_search** (boolean) :
  - **image_generation** (boolean) :
  - **code_interpreter** (boolean) :
  - **memories** (boolean) :
- **settings** ([SettingsPermissions](#model-settingspermissions)) *(required)*:
  - **interface** (boolean) :

---
### GET `/api/v1/users/groups`

**Tags:** users

**Résumé:** Get User Groups

---
### GET `/api/v1/users/permissions`

**Tags:** users

**Résumé:** Get User Permissisions

---
### GET `/api/v1/users/search`

**Tags:** users

**Résumé:** Search Users

**Paramètres URL / Query :**

- `query` (query) - Optionnel :
- `order_by` (query) - Optionnel :
- `direction` (query) - Optionnel :
- `page` (query) - Optionnel :

---
### GET `/api/v1/users/user/info`

**Tags:** users

**Résumé:** Get User Info By Session User

---
### POST `/api/v1/users/user/info/update`

**Tags:** users

**Résumé:** Update User Info By Session User

**Corps de la requête (Body) :**


---
### GET `/api/v1/users/user/settings`

**Tags:** users

**Résumé:** Get User Settings By Session User

---
### POST `/api/v1/users/user/settings/update`

**Tags:** users

**Résumé:** Update User Settings By Session User

**Corps de la requête (Body) :**

- **ui** (any) :

---
### GET `/api/v1/users/user/status`

**Tags:** users

**Résumé:** Get User Status By Session User

---
### POST `/api/v1/users/user/status/update`

**Tags:** users

**Résumé:** Update User Status By Session User

**Corps de la requête (Body) :**

- **status_emoji** (any) :
- **status_message** (any) :
- **status_expires_at** (any) :

---
### GET `/api/v1/users/{user_id}`

**Tags:** users

**Résumé:** Get User By Id

**Paramètres URL / Query :**

- `user_id` (path) - **Requis** :

---
### DELETE `/api/v1/users/{user_id}`

**Tags:** users

**Résumé:** Delete User By Id

**Paramètres URL / Query :**

- `user_id` (path) - **Requis** :

---
### GET `/api/v1/users/{user_id}/active`

**Tags:** users

**Résumé:** Get User Active Status By Id

**Paramètres URL / Query :**

- `user_id` (path) - **Requis** :

---
### GET `/api/v1/users/{user_id}/groups`

**Tags:** users

**Résumé:** Get User Groups By Id

**Paramètres URL / Query :**

- `user_id` (path) - **Requis** :

---
### GET `/api/v1/users/{user_id}/oauth/sessions`

**Tags:** users

**Résumé:** Get User Oauth Sessions By Id

**Paramètres URL / Query :**

- `user_id` (path) - **Requis** :

---
### GET `/api/v1/users/{user_id}/profile/image`

**Tags:** users

**Résumé:** Get User Profile Image By Id

**Paramètres URL / Query :**

- `user_id` (path) - **Requis** :

---
### POST `/api/v1/users/{user_id}/update`

**Tags:** users

**Résumé:** Update User By Id

**Paramètres URL / Query :**

- `user_id` (path) - **Requis** :

**Corps de la requête (Body) :**

- **role** (string) *(required)*:
- **name** (string) *(required)*:
- **email** (string) *(required)*:
- **profile_image_url** (string) *(required)*:
- **password** (any) :

---
### POST `/api/v1/utils/code/execute`

**Tags:** utils

**Résumé:** Execute Code

**Corps de la requête (Body) :**

- **code** (string) *(required)*:

---
### POST `/api/v1/utils/code/format`

**Tags:** utils

**Résumé:** Format Code

**Corps de la requête (Body) :**

- **code** (string) *(required)*:

---
### GET `/api/v1/utils/db/download`

**Tags:** utils

**Résumé:** Download Db

---
### GET `/api/v1/utils/gravatar`

**Tags:** utils

**Résumé:** Get Gravatar

**Paramètres URL / Query :**

- `email` (query) - **Requis** :

---
### POST `/api/v1/utils/markdown`

**Tags:** utils

**Résumé:** Get Html From Markdown

**Corps de la requête (Body) :**

- **md** (string) *(required)*:

---
### POST `/api/v1/utils/pdf`

**Tags:** utils

**Résumé:** Download Chat As Pdf

**Corps de la requête (Body) :**

- **title** (string) *(required)*:
- **messages** (array) *(required)*:

---
### GET `/api/version`

**Tags:**

**Résumé:** Get App Version

---
### GET `/api/version/updates`

**Tags:**

**Résumé:** Get App Latest Release Version

---
### GET `/api/webhook`

**Tags:**

**Résumé:** Get Webhook Url

---
### POST `/api/webhook`

**Tags:**

**Résumé:** Update Webhook Url

**Corps de la requête (Body) :**

- **url** (string) *(required)*:

---
### GET `/cache/{path}`

**Tags:**

**Résumé:** Serve Cache File

**Paramètres URL / Query :**

- `path` (path) - **Requis** :

---
### GET `/health`

**Tags:**

**Résumé:** Healthcheck

---
### GET `/health/db`

**Tags:**

**Résumé:** Healthcheck With Db

---
### GET `/manifest.json`

**Tags:**

**Résumé:** Get Manifest Json

---
### GET `/oauth/clients/{client_id}/authorize`

**Tags:**

**Résumé:** Oauth Client Authorize

**Paramètres URL / Query :**

- `client_id` (path) - **Requis** :

---
### GET `/oauth/clients/{client_id}/callback`

**Tags:**

**Résumé:** Oauth Client Callback

**Paramètres URL / Query :**

- `client_id` (path) - **Requis** :

---
### GET `/oauth/{provider}/callback`

**Tags:**

**Résumé:** Oauth Login Callback

**Paramètres URL / Query :**

- `provider` (path) - **Requis** :

---
### GET `/oauth/{provider}/login`

**Tags:**

**Résumé:** Oauth Login

**Paramètres URL / Query :**

- `provider` (path) - **Requis** :

---
### GET `/oauth/{provider}/login/callback`

**Tags:**

**Résumé:** Oauth Login Callback

**Paramètres URL / Query :**

- `provider` (path) - **Requis** :

---
### GET `/ollama/`

**Tags:** ollama

**Résumé:** Get Status

---
### HEAD `/ollama/`

**Tags:** ollama

**Résumé:** Get Status

---
### POST `/ollama/api/chat`

**Tags:** ollama

**Résumé:** Generate Chat Completion

**Paramètres URL / Query :**

- `url_idx` (query) - Optionnel :
- `bypass_filter` (query) - Optionnel :
- `bypass_system_prompt` (query) - Optionnel :

**Corps de la requête (Body) :**


---
### POST `/ollama/api/chat/{url_idx}`

**Tags:** ollama

**Résumé:** Generate Chat Completion

**Paramètres URL / Query :**

- `url_idx` (path) - **Requis** :
- `bypass_filter` (query) - Optionnel :
- `bypass_system_prompt` (query) - Optionnel :

**Corps de la requête (Body) :**


---
### POST `/ollama/api/copy`

**Tags:** ollama

**Résumé:** Copy Model

**Paramètres URL / Query :**

- `url_idx` (query) - Optionnel :

**Corps de la requête (Body) :**

- **source** (string) *(required)*:
- **destination** (string) *(required)*:

---
### POST `/ollama/api/copy/{url_idx}`

**Tags:** ollama

**Résumé:** Copy Model

**Paramètres URL / Query :**

- `url_idx` (path) - **Requis** :

**Corps de la requête (Body) :**

- **source** (string) *(required)*:
- **destination** (string) *(required)*:

---
### POST `/ollama/api/create`

**Tags:** ollama

**Résumé:** Create Model

**Paramètres URL / Query :**

- `url_idx` (query) - Optionnel :

**Corps de la requête (Body) :**

- **model** (any) :
- **stream** (any) :
- **path** (any) :

---
### POST `/ollama/api/create/{url_idx}`

**Tags:** ollama

**Résumé:** Create Model

**Paramètres URL / Query :**

- `url_idx` (path) - **Requis** :

**Corps de la requête (Body) :**

- **model** (any) :
- **stream** (any) :
- **path** (any) :

---
### DELETE `/ollama/api/delete`

**Tags:** ollama

**Résumé:** Delete Model

**Paramètres URL / Query :**

- `url_idx` (query) - Optionnel :

**Corps de la requête (Body) :**

- **model** (any) :

---
### DELETE `/ollama/api/delete/{url_idx}`

**Tags:** ollama

**Résumé:** Delete Model

**Paramètres URL / Query :**

- `url_idx` (path) - **Requis** :

**Corps de la requête (Body) :**

- **model** (any) :

---
### POST `/ollama/api/embed`

**Tags:** ollama

**Résumé:** Embed

**Paramètres URL / Query :**

- `url_idx` (query) - Optionnel :

**Corps de la requête (Body) :**

- **model** (string) *(required)*:
- **input** (any) *(required)*:
- **truncate** (any) :
- **options** (any) :
- **keep_alive** (any) :

---
### POST `/ollama/api/embed/{url_idx}`

**Tags:** ollama

**Résumé:** Embed

**Paramètres URL / Query :**

- `url_idx` (path) - **Requis** :

**Corps de la requête (Body) :**

- **model** (string) *(required)*:
- **input** (any) *(required)*:
- **truncate** (any) :
- **options** (any) :
- **keep_alive** (any) :

---
### POST `/ollama/api/embeddings`

**Tags:** ollama

**Résumé:** Embeddings

**Paramètres URL / Query :**

- `url_idx` (query) - Optionnel :

**Corps de la requête (Body) :**

- **model** (string) *(required)*:
- **prompt** (string) *(required)*:
- **options** (any) :
- **keep_alive** (any) :

---
### POST `/ollama/api/embeddings/{url_idx}`

**Tags:** ollama

**Résumé:** Embeddings

**Paramètres URL / Query :**

- `url_idx` (path) - **Requis** :

**Corps de la requête (Body) :**

- **model** (string) *(required)*:
- **prompt** (string) *(required)*:
- **options** (any) :
- **keep_alive** (any) :

---
### POST `/ollama/api/generate`

**Tags:** ollama

**Résumé:** Generate Completion

**Paramètres URL / Query :**

- `url_idx` (query) - Optionnel :

**Corps de la requête (Body) :**

- **model** (string) *(required)*:
- **prompt** (string) *(required)*:
- **suffix** (any) :
- **images** (any) :
- **format** (any) :
- **options** (any) :
- **system** (any) :
- **template** (any) :
- **context** (any) :
- **stream** (any) :
- **raw** (any) :
- **keep_alive** (any) :

---
### POST `/ollama/api/generate/{url_idx}`

**Tags:** ollama

**Résumé:** Generate Completion

**Paramètres URL / Query :**

- `url_idx` (path) - **Requis** :

**Corps de la requête (Body) :**

- **model** (string) *(required)*:
- **prompt** (string) *(required)*:
- **suffix** (any) :
- **images** (any) :
- **format** (any) :
- **options** (any) :
- **system** (any) :
- **template** (any) :
- **context** (any) :
- **stream** (any) :
- **raw** (any) :
- **keep_alive** (any) :

---
### GET `/ollama/api/ps`

**Tags:** ollama

**Résumé:** Get Ollama Loaded Models

> List models that are currently loaded into Ollama memory, and which node they are loaded on.

---
### POST `/ollama/api/pull`

**Tags:** ollama

**Résumé:** Pull Model

**Paramètres URL / Query :**

- `url_idx` (query) - Optionnel :

**Corps de la requête (Body) :**

- **model** (any) :

---
### POST `/ollama/api/pull/{url_idx}`

**Tags:** ollama

**Résumé:** Pull Model

**Paramètres URL / Query :**

- `url_idx` (path) - **Requis** :

**Corps de la requête (Body) :**

- **model** (any) :

---
### DELETE `/ollama/api/push`

**Tags:** ollama

**Résumé:** Push Model

**Paramètres URL / Query :**

- `url_idx` (query) - Optionnel :

**Corps de la requête (Body) :**

- **model** (string) *(required)*:
- **insecure** (any) :
- **stream** (any) :

---
### DELETE `/ollama/api/push/{url_idx}`

**Tags:** ollama

**Résumé:** Push Model

**Paramètres URL / Query :**

- `url_idx` (path) - **Requis** :

**Corps de la requête (Body) :**

- **model** (string) *(required)*:
- **insecure** (any) :
- **stream** (any) :

---
### POST `/ollama/api/show`

**Tags:** ollama

**Résumé:** Show Model Info

**Corps de la requête (Body) :**

- **model** (any) :

---
### GET `/ollama/api/tags`

**Tags:** ollama

**Résumé:** Get Ollama Tags

**Paramètres URL / Query :**

- `url_idx` (query) - Optionnel :

---
### GET `/ollama/api/tags/{url_idx}`

**Tags:** ollama

**Résumé:** Get Ollama Tags

**Paramètres URL / Query :**

- `url_idx` (path) - **Requis** :

---
### POST `/ollama/api/unload`

**Tags:** ollama

**Résumé:** Unload Model

**Corps de la requête (Body) :**

- **model** (any) :

---
### GET `/ollama/api/version`

**Tags:** ollama

**Résumé:** Get Ollama Versions

**Paramètres URL / Query :**

- `url_idx` (query) - Optionnel :

---
### GET `/ollama/api/version/{url_idx}`

**Tags:** ollama

**Résumé:** Get Ollama Versions

**Paramètres URL / Query :**

- `url_idx` (path) - **Requis** :

---
### GET `/ollama/config`

**Tags:** ollama

**Résumé:** Get Config

---
### POST `/ollama/config/update`

**Tags:** ollama

**Résumé:** Update Config

**Corps de la requête (Body) :**

- **ENABLE_OLLAMA_API** (any) :
- **OLLAMA_BASE_URLS** (array) *(required)*:
- **OLLAMA_API_CONFIGS** (object) *(required)*:

---
### POST `/ollama/models/download`

**Tags:** ollama

**Résumé:** Download Model

**Paramètres URL / Query :**

- `url_idx` (query) - Optionnel :

**Corps de la requête (Body) :**

- **url** (string) *(required)*:

---
### POST `/ollama/models/download/{url_idx}`

**Tags:** ollama

**Résumé:** Download Model

**Paramètres URL / Query :**

- `url_idx` (path) - **Requis** :

**Corps de la requête (Body) :**

- **url** (string) *(required)*:

---
### POST `/ollama/models/upload`

**Tags:** ollama

**Résumé:** Upload Model

**Paramètres URL / Query :**

- `url_idx` (query) - Optionnel :

**Corps de la requête (Body) :**

- **file** (string) *(required)*:

---
### POST `/ollama/models/upload/{url_idx}`

**Tags:** ollama

**Résumé:** Upload Model

**Paramètres URL / Query :**

- `url_idx` (path) - **Requis** :

**Corps de la requête (Body) :**

- **file** (string) *(required)*:

---
### POST `/ollama/v1/chat/completions`

**Tags:** ollama

**Résumé:** Generate Openai Chat Completion

**Paramètres URL / Query :**

- `url_idx` (query) - Optionnel :

**Corps de la requête (Body) :**


---
### POST `/ollama/v1/chat/completions/{url_idx}`

**Tags:** ollama

**Résumé:** Generate Openai Chat Completion

**Paramètres URL / Query :**

- `url_idx` (path) - **Requis** :

**Corps de la requête (Body) :**


---
### POST `/ollama/v1/completions`

**Tags:** ollama

**Résumé:** Generate Openai Completion

**Paramètres URL / Query :**

- `url_idx` (query) - Optionnel :

**Corps de la requête (Body) :**


---
### POST `/ollama/v1/completions/{url_idx}`

**Tags:** ollama

**Résumé:** Generate Openai Completion

**Paramètres URL / Query :**

- `url_idx` (path) - **Requis** :

**Corps de la requête (Body) :**


---
### GET `/ollama/v1/models`

**Tags:** ollama

**Résumé:** Get Openai Models

**Paramètres URL / Query :**

- `url_idx` (query) - Optionnel :

---
### GET `/ollama/v1/models/{url_idx}`

**Tags:** ollama

**Résumé:** Get Openai Models

**Paramètres URL / Query :**

- `url_idx` (path) - **Requis** :

---
### POST `/ollama/verify`

**Tags:** ollama

**Résumé:** Verify Connection

**Corps de la requête (Body) :**

- **url** (string) *(required)*:
- **key** (any) :

---
### POST `/openai/audio/speech`

**Tags:** openai

**Résumé:** Speech

---
### POST `/openai/chat/completions`

**Tags:** openai

**Résumé:** Generate Chat Completion

**Paramètres URL / Query :**

- `bypass_filter` (query) - Optionnel :
- `bypass_system_prompt` (query) - Optionnel :

**Corps de la requête (Body) :**


---
### GET `/openai/config`

**Tags:** openai

**Résumé:** Get Config

---
### POST `/openai/config/update`

**Tags:** openai

**Résumé:** Update Config

**Corps de la requête (Body) :**

- **ENABLE_OPENAI_API** (any) :
- **OPENAI_API_BASE_URLS** (array) *(required)*:
- **OPENAI_API_KEYS** (array) *(required)*:
- **OPENAI_API_CONFIGS** (object) *(required)*:

---
### GET `/openai/models`

**Tags:** openai

**Résumé:** Get Models

**Paramètres URL / Query :**

- `url_idx` (query) - Optionnel :

---
### GET `/openai/models/{url_idx}`

**Tags:** openai

**Résumé:** Get Models

**Paramètres URL / Query :**

- `url_idx` (path) - **Requis** :

---
### POST `/openai/verify`

**Tags:** openai

**Résumé:** Verify Connection

**Corps de la requête (Body) :**

- **url** (string) *(required)*:
- **key** (string) *(required)*:
- **config** (any) :

---
### DELETE `/openai/{path}`

**Tags:** openai

**Résumé:** Proxy

> Deprecated: proxy all requests to OpenAI API

**Paramètres URL / Query :**

- `path` (path) - **Requis** :

---
### POST `/openai/{path}`

**Tags:** openai

**Résumé:** Proxy

> Deprecated: proxy all requests to OpenAI API

**Paramètres URL / Query :**

- `path` (path) - **Requis** :

---
### GET `/openai/{path}`

**Tags:** openai

**Résumé:** Proxy

> Deprecated: proxy all requests to OpenAI API

**Paramètres URL / Query :**

- `path` (path) - **Requis** :

---
### PUT `/openai/{path}`

**Tags:** openai

**Résumé:** Proxy

> Deprecated: proxy all requests to OpenAI API

**Paramètres URL / Query :**

- `path` (path) - **Requis** :

---
### GET `/opensearch.xml`

**Tags:**

**Résumé:** Get Opensearch Xml

---
## Modèles de Données

Ces objets définissent la structure des réponses et des requêtes.

### <a id='model-addmemoryform'></a>Object: AddMemoryForm

- **content** (string) *(required)*:

---
### <a id='model-addpipelineform'></a>Object: AddPipelineForm

- **url** (string) *(required)*:
- **urlIdx** (integer) *(required)*:

---
### <a id='model-adduserform'></a>Object: AddUserForm

- **name** (string) *(required)*:
- **email** (string) *(required)*:
- **password** (string) *(required)*:
- **profile_image_url** (any) :
- **role** (any) :

---
### <a id='model-adminconfig'></a>Object: AdminConfig

- **SHOW_ADMIN_DETAILS** (boolean) *(required)*:
- **ADMIN_EMAIL** (any) :
- **WEBUI_URL** (string) *(required)*:
- **ENABLE_SIGNUP** (boolean) *(required)*:
- **ENABLE_API_KEYS** (boolean) *(required)*:
- **ENABLE_API_KEYS_ENDPOINT_RESTRICTIONS** (boolean) *(required)*:
- **API_KEYS_ALLOWED_ENDPOINTS** (string) *(required)*:
- **DEFAULT_USER_ROLE** (string) *(required)*:
- **DEFAULT_GROUP_ID** (string) *(required)*:
- **JWT_EXPIRES_IN** (string) *(required)*:
- **ENABLE_COMMUNITY_SHARING** (boolean) *(required)*:
- **ENABLE_MESSAGE_RATING** (boolean) *(required)*:
- **ENABLE_FOLDERS** (boolean) *(required)*:
- **FOLDER_MAX_FILE_COUNT** (any) :
- **ENABLE_CHANNELS** (boolean) *(required)*:
- **ENABLE_MEMORIES** (boolean) *(required)*:
- **ENABLE_NOTES** (boolean) *(required)*:
- **ENABLE_USER_WEBHOOKS** (boolean) *(required)*:
- **ENABLE_USER_STATUS** (boolean) *(required)*:
- **PENDING_USER_OVERLAY_TITLE** (any) :
- **PENDING_USER_OVERLAY_CONTENT** (any) :
- **RESPONSE_WATERMARK** (any) :

---
### <a id='model-aggregatechatstats'></a>Object: AggregateChatStats

- **average_response_time** (number) *(required)*:
- **average_user_message_content_length** (number) *(required)*:
- **average_assistant_message_content_length** (number) *(required)*:
- **models** (object) *(required)*:
- **message_count** (integer) *(required)*:
- **history_models** (object) *(required)*:
- **history_message_count** (integer) *(required)*:
- **history_user_message_count** (integer) *(required)*:
- **history_assistant_message_count** (integer) *(required)*:

---
### <a id='model-apikey'></a>Object: ApiKey

- **api_key** (any) :

---
### <a id='model-audioconfigupdateform'></a>Object: AudioConfigUpdateForm

- **tts** ([TTSConfigForm](#model-ttsconfigform)) *(required)*:
  - **OPENAI_API_BASE_URL** (string) *(required)*:
  - **OPENAI_API_KEY** (string) *(required)*:
  - **OPENAI_PARAMS** (any) :
  - **API_KEY** (string) *(required)*:
  - **ENGINE** (string) *(required)*:
  - **MODEL** (string) *(required)*:
  - **VOICE** (string) *(required)*:
  - **SPLIT_ON** (string) *(required)*:
  - **AZURE_SPEECH_REGION** (string) *(required)*:
  - **AZURE_SPEECH_BASE_URL** (string) *(required)*:
  - **AZURE_SPEECH_OUTPUT_FORMAT** (string) *(required)*:
- **stt** ([STTConfigForm](#model-sttconfigform)) *(required)*:
  - **OPENAI_API_BASE_URL** (string) *(required)*:
  - **OPENAI_API_KEY** (string) *(required)*:
  - **ENGINE** (string) *(required)*:
  - **MODEL** (string) *(required)*:
  - **SUPPORTED_CONTENT_TYPES** (array) :
  - **WHISPER_MODEL** (string) *(required)*:
  - **DEEPGRAM_API_KEY** (string) *(required)*:
  - **AZURE_API_KEY** (string) *(required)*:
  - **AZURE_REGION** (string) *(required)*:
  - **AZURE_LOCALES** (string) *(required)*:
  - **AZURE_BASE_URL** (string) *(required)*:
  - **AZURE_MAX_SPEAKERS** (string) *(required)*:
  - **MISTRAL_API_KEY** (string) *(required)*:
  - **MISTRAL_API_BASE_URL** (string) *(required)*:
  - **MISTRAL_USE_CHAT_COMPLETIONS** (boolean) *(required)*:

---
### <a id='model-azureopenaiconfigform'></a>Object: AzureOpenAIConfigForm

- **url** (string) *(required)*:
- **key** (string) *(required)*:
- **version** (string) *(required)*:

---
### <a id='model-bannermodel'></a>Object: BannerModel

- **id** (string) *(required)*:
- **type** (string) *(required)*:
- **title** (any) :
- **content** (string) *(required)*:
- **dismissible** (boolean) *(required)*:
- **timestamp** (integer) *(required)*:

---
### <a id='model-batchprocessfilesform'></a>Object: BatchProcessFilesForm

- **files** (array) *(required)*:
- **collection_name** (string) *(required)*:

---
### <a id='model-batchprocessfilesresponse'></a>Object: BatchProcessFilesResponse

- **results** (array) *(required)*:
- **errors** (array) *(required)*:

---
### <a id='model-batchprocessfilesresult'></a>Object: BatchProcessFilesResult

- **file_id** (string) *(required)*:
- **status** (string) *(required)*:
- **error** (any) :

---
### <a id='model-body_image_edits_api_v1_images_edit_post'></a>Object: Body_image_edits_api_v1_images_edit_post

- **form_data** ([EditImageForm](#model-editimageform)) *(required)*:
  - **image** (any) *(required)*:
  - **prompt** (string) *(required)*:
  - **model** (any) :
  - **size** (any) :
  - **n** (any) :
  - **negative_prompt** (any) :
- **metadata** (any) :

---
### <a id='model-body_transcription_api_v1_audio_transcriptions_post'></a>Object: Body_transcription_api_v1_audio_transcriptions_post

- **file** (string) *(required)*:
- **language** (any) :

---
### <a id='model-body_upload_file_api_v1_files__post'></a>Object: Body_upload_file_api_v1_files__post

- **file** (string) *(required)*:
- **metadata** (any) :

---
### <a id='model-body_upload_model_ollama_models_upload__url_idx__post'></a>Object: Body_upload_model_ollama_models_upload__url_idx__post

- **file** (string) *(required)*:

---
### <a id='model-body_upload_model_ollama_models_upload_post'></a>Object: Body_upload_model_ollama_models_upload_post

- **file** (string) *(required)*:

---
### <a id='model-body_upload_pipeline_api_v1_pipelines_upload_post'></a>Object: Body_upload_pipeline_api_v1_pipelines_upload_post

- **urlIdx** (integer) *(required)*:
- **file** (string) *(required)*:

---
### <a id='model-channelform'></a>Object: ChannelForm

- **name** (string) :
- **description** (any) :
- **is_private** (any) :
- **data** (any) :
- **meta** (any) :
- **access_control** (any) :
- **group_ids** (any) :
- **user_ids** (any) :

---
### <a id='model-channelfullresponse'></a>Object: ChannelFullResponse

- **id** (string) *(required)*:
- **user_id** (string) *(required)*:
- **type** (any) :
- **name** (string) *(required)*:
- **description** (any) :
- **is_private** (any) :
- **data** (any) :
- **meta** (any) :
- **access_control** (any) :
- **created_at** (integer) *(required)*:
- **updated_at** (integer) *(required)*:
- **updated_by** (any) :
- **archived_at** (any) :
- **archived_by** (any) :
- **deleted_at** (any) :
- **deleted_by** (any) :
- **is_manager** (boolean) :
- **write_access** (boolean) :
- **user_count** (any) :
- **user_ids** (any) :
- **users** (any) :
- **last_read_at** (any) :
- **unread_count** (integer) :

---
### <a id='model-channellistitemresponse'></a>Object: ChannelListItemResponse

- **id** (string) *(required)*:
- **user_id** (string) *(required)*:
- **type** (any) :
- **name** (string) *(required)*:
- **description** (any) :
- **is_private** (any) :
- **data** (any) :
- **meta** (any) :
- **access_control** (any) :
- **created_at** (integer) *(required)*:
- **updated_at** (integer) *(required)*:
- **updated_by** (any) :
- **archived_at** (any) :
- **archived_by** (any) :
- **deleted_at** (any) :
- **deleted_by** (any) :
- **user_ids** (any) :
- **users** (any) :
- **last_message_at** (any) :
- **unread_count** (integer) :

---
### <a id='model-channelmodel'></a>Object: ChannelModel

- **id** (string) *(required)*:
- **user_id** (string) *(required)*:
- **type** (any) :
- **name** (string) *(required)*:
- **description** (any) :
- **is_private** (any) :
- **data** (any) :
- **meta** (any) :
- **access_control** (any) :
- **created_at** (integer) *(required)*:
- **updated_at** (integer) *(required)*:
- **updated_by** (any) :
- **archived_at** (any) :
- **archived_by** (any) :
- **deleted_at** (any) :
- **deleted_by** (any) :

---
### <a id='model-channelwebhookform'></a>Object: ChannelWebhookForm

- **name** (string) *(required)*:
- **profile_image_url** (any) :

---
### <a id='model-channelwebhookmodel'></a>Object: ChannelWebhookModel

- **id** (string) *(required)*:
- **channel_id** (string) *(required)*:
- **user_id** (string) *(required)*:
- **name** (string) *(required)*:
- **profile_image_url** (any) :
- **token** (string) *(required)*:
- **last_used_at** (any) :
- **created_at** (integer) *(required)*:
- **updated_at** (integer) *(required)*:

---
### <a id='model-chatbody'></a>Object: ChatBody

- **history** ([ChatHistoryStats](#model-chathistorystats)) *(required)*:
  - **messages** (object) *(required)*:
  - **currentId** (any) :

---
### <a id='model-chatfolderidform'></a>Object: ChatFolderIdForm

- **folder_id** (any) :

---
### <a id='model-chatform'></a>Object: ChatForm

- **chat** (object) *(required)*:
- **folder_id** (any) :

---
### <a id='model-chathistorystats'></a>Object: ChatHistoryStats

- **messages** (object) *(required)*:
- **currentId** (any) :

---
### <a id='model-chatimportform'></a>Object: ChatImportForm

- **chat** (object) *(required)*:
- **folder_id** (any) :
- **meta** (any) :
- **pinned** (any) :
- **created_at** (any) :
- **updated_at** (any) :

---
### <a id='model-chatpermissions'></a>Object: ChatPermissions

- **controls** (boolean) :
- **valves** (boolean) :
- **system_prompt** (boolean) :
- **params** (boolean) :
- **file_upload** (boolean) :
- **delete** (boolean) :
- **delete_message** (boolean) :
- **continue_response** (boolean) :
- **regenerate_response** (boolean) :
- **rate_response** (boolean) :
- **edit** (boolean) :
- **share** (boolean) :
- **export** (boolean) :
- **stt** (boolean) :
- **tts** (boolean) :
- **call** (boolean) :
- **multiple_models** (boolean) :
- **temporary** (boolean) :
- **temporary_enforced** (boolean) :

---
### <a id='model-chatresponse'></a>Object: ChatResponse

- **id** (string) *(required)*:
- **user_id** (string) *(required)*:
- **title** (string) *(required)*:
- **chat** (object) *(required)*:
- **updated_at** (integer) *(required)*:
- **created_at** (integer) *(required)*:
- **share_id** (any) :
- **archived** (boolean) *(required)*:
- **pinned** (any) :
- **meta** (object) :
- **folder_id** (any) :

---
### <a id='model-chatstatsexport'></a>Object: ChatStatsExport

- **id** (string) *(required)*:
- **user_id** (string) *(required)*:
- **created_at** (integer) *(required)*:
- **updated_at** (integer) *(required)*:
- **tags** (array) :
- **stats** ([AggregateChatStats](#model-aggregatechatstats)) *(required)*:
  - **average_response_time** (number) *(required)*:
  - **average_user_message_content_length** (number) *(required)*:
  - **average_assistant_message_content_length** (number) *(required)*:
  - **models** (object) *(required)*:
  - **message_count** (integer) *(required)*:
  - **history_models** (object) *(required)*:
  - **history_message_count** (integer) *(required)*:
  - **history_user_message_count** (integer) *(required)*:
  - **history_assistant_message_count** (integer) *(required)*:
- **chat** ([ChatBody](#model-chatbody)) *(required)*:
  - **history** ([ChatHistoryStats](#model-chathistorystats)) *(required)*:
    - **messages** (object) *(required)*:
    - **currentId** (any) :

---
### <a id='model-chatstatsexportlist'></a>Object: ChatStatsExportList

- **type** (string) :
- **items** (array) *(required)*:
- **total** (integer) *(required)*:
- **page** (integer) *(required)*:

---
### <a id='model-chattitleidresponse'></a>Object: ChatTitleIdResponse

- **id** (string) *(required)*:
- **title** (string) *(required)*:
- **updated_at** (integer) *(required)*:
- **created_at** (integer) *(required)*:

---
### <a id='model-chattitlemessagesform'></a>Object: ChatTitleMessagesForm

- **title** (string) *(required)*:
- **messages** (array) *(required)*:

---
### <a id='model-chatusagestatslistresponse'></a>Object: ChatUsageStatsListResponse

- **items** (array) *(required)*:
- **total** (integer) *(required)*:

---
### <a id='model-chatusagestatsresponse'></a>Object: ChatUsageStatsResponse

- **id** (string) *(required)*:
- **models** (object) :
- **message_count** (integer) *(required)*:
- **history_models** (object) :
- **history_message_count** (integer) *(required)*:
- **history_user_message_count** (integer) *(required)*:
- **history_assistant_message_count** (integer) *(required)*:
- **average_response_time** (number) *(required)*:
- **average_user_message_content_length** (number) *(required)*:
- **average_assistant_message_content_length** (number) *(required)*:
- **tags** (array) :
- **last_message_at** (integer) *(required)*:
- **updated_at** (integer) *(required)*:
- **created_at** (integer) *(required)*:

---
### <a id='model-chatsimportform'></a>Object: ChatsImportForm

- **chats** (array) *(required)*:

---
### <a id='model-cloneform'></a>Object: CloneForm

- **title** (any) :

---
### <a id='model-codeform'></a>Object: CodeForm

- **code** (string) *(required)*:

---
### <a id='model-codeinterpreterconfigform'></a>Object: CodeInterpreterConfigForm

- **ENABLE_CODE_EXECUTION** (boolean) *(required)*:
- **CODE_EXECUTION_ENGINE** (string) *(required)*:
- **CODE_EXECUTION_JUPYTER_URL** (any) *(required)*:
- **CODE_EXECUTION_JUPYTER_AUTH** (any) *(required)*:
- **CODE_EXECUTION_JUPYTER_AUTH_TOKEN** (any) *(required)*:
- **CODE_EXECUTION_JUPYTER_AUTH_PASSWORD** (any) *(required)*:
- **CODE_EXECUTION_JUPYTER_TIMEOUT** (any) *(required)*:
- **ENABLE_CODE_INTERPRETER** (boolean) *(required)*:
- **CODE_INTERPRETER_ENGINE** (string) *(required)*:
- **CODE_INTERPRETER_PROMPT_TEMPLATE** (any) *(required)*:
- **CODE_INTERPRETER_JUPYTER_URL** (any) *(required)*:
- **CODE_INTERPRETER_JUPYTER_AUTH** (any) *(required)*:
- **CODE_INTERPRETER_JUPYTER_AUTH_TOKEN** (any) *(required)*:
- **CODE_INTERPRETER_JUPYTER_AUTH_PASSWORD** (any) *(required)*:
- **CODE_INTERPRETER_JUPYTER_TIMEOUT** (any) *(required)*:

---
### <a id='model-configform'></a>Object: ConfigForm

- **RAG_TEMPLATE** (any) :
- **TOP_K** (any) :
- **BYPASS_EMBEDDING_AND_RETRIEVAL** (any) :
- **RAG_FULL_CONTEXT** (any) :
- **ENABLE_RAG_HYBRID_SEARCH** (any) :
- **ENABLE_RAG_HYBRID_SEARCH_ENRICHED_TEXTS** (any) :
- **TOP_K_RERANKER** (any) :
- **RELEVANCE_THRESHOLD** (any) :
- **HYBRID_BM25_WEIGHT** (any) :
- **CONTENT_EXTRACTION_ENGINE** (any) :
- **PDF_EXTRACT_IMAGES** (any) :
- **DATALAB_MARKER_API_KEY** (any) :
- **DATALAB_MARKER_API_BASE_URL** (any) :
- **DATALAB_MARKER_ADDITIONAL_CONFIG** (any) :
- **DATALAB_MARKER_SKIP_CACHE** (any) :
- **DATALAB_MARKER_FORCE_OCR** (any) :
- **DATALAB_MARKER_PAGINATE** (any) :
- **DATALAB_MARKER_STRIP_EXISTING_OCR** (any) :
- **DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION** (any) :
- **DATALAB_MARKER_FORMAT_LINES** (any) :
- **DATALAB_MARKER_USE_LLM** (any) :
- **DATALAB_MARKER_OUTPUT_FORMAT** (any) :
- **EXTERNAL_DOCUMENT_LOADER_URL** (any) :
- **EXTERNAL_DOCUMENT_LOADER_API_KEY** (any) :
- **TIKA_SERVER_URL** (any) :
- **DOCLING_SERVER_URL** (any) :
- **DOCLING_API_KEY** (any) :
- **DOCLING_PARAMS** (any) :
- **DOCUMENT_INTELLIGENCE_ENDPOINT** (any) :
- **DOCUMENT_INTELLIGENCE_KEY** (any) :
- **DOCUMENT_INTELLIGENCE_MODEL** (any) :
- **MISTRAL_OCR_API_BASE_URL** (any) :
- **MISTRAL_OCR_API_KEY** (any) :
- **MINERU_API_MODE** (any) :
- **MINERU_API_URL** (any) :
- **MINERU_API_KEY** (any) :
- **MINERU_API_TIMEOUT** (any) :
- **MINERU_PARAMS** (any) :
- **RAG_RERANKING_MODEL** (any) :
- **RAG_RERANKING_ENGINE** (any) :
- **RAG_EXTERNAL_RERANKER_URL** (any) :
- **RAG_EXTERNAL_RERANKER_API_KEY** (any) :
- **RAG_EXTERNAL_RERANKER_TIMEOUT** (any) :
- **TEXT_SPLITTER** (any) :
- **ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER** (any) :
- **CHUNK_SIZE** (any) :
- **CHUNK_MIN_SIZE_TARGET** (any) :
- **CHUNK_OVERLAP** (any) :
- **FILE_MAX_SIZE** (any) :
- **FILE_MAX_COUNT** (any) :
- **FILE_IMAGE_COMPRESSION_WIDTH** (any) :
- **FILE_IMAGE_COMPRESSION_HEIGHT** (any) :
- **ALLOWED_FILE_EXTENSIONS** (any) :
- **ENABLE_GOOGLE_DRIVE_INTEGRATION** (any) :
- **ENABLE_ONEDRIVE_INTEGRATION** (any) :
- **web** (any) :

---
### <a id='model-connectionverificationform'></a>Object: ConnectionVerificationForm

- **url** (string) *(required)*:
- **key** (any) :

---
### <a id='model-connectionsconfigform'></a>Object: ConnectionsConfigForm

- **ENABLE_DIRECT_CONNECTIONS** (boolean) *(required)*:
- **ENABLE_BASE_MODELS_CACHE** (boolean) *(required)*:

---
### <a id='model-contentform'></a>Object: ContentForm

- **content** (string) *(required)*:

---
### <a id='model-copymodelform'></a>Object: CopyModelForm

- **source** (string) *(required)*:
- **destination** (string) *(required)*:

---
### <a id='model-createchannelform'></a>Object: CreateChannelForm

- **name** (string) :
- **description** (any) :
- **is_private** (any) :
- **data** (any) :
- **meta** (any) :
- **access_control** (any) :
- **group_ids** (any) :
- **user_ids** (any) :
- **type** (any) :

---
### <a id='model-createimageform'></a>Object: CreateImageForm

- **model** (any) :
- **prompt** (string) *(required)*:
- **size** (any) :
- **n** (integer) :
- **steps** (any) :
- **negative_prompt** (any) :

---
### <a id='model-createmodelform'></a>Object: CreateModelForm

- **model** (any) :
- **stream** (any) :
- **path** (any) :

---
### <a id='model-deleteform'></a>Object: DeleteForm

- **collection_name** (string) *(required)*:
- **file_id** (string) *(required)*:

---
### <a id='model-deletepipelineform'></a>Object: DeletePipelineForm

- **id** (string) *(required)*:
- **urlIdx** (integer) *(required)*:

---
### <a id='model-editimageform'></a>Object: EditImageForm

- **image** (any) *(required)*:
- **prompt** (string) *(required)*:
- **model** (any) :
- **size** (any) :
- **n** (any) :
- **negative_prompt** (any) :

---
### <a id='model-embeddingmodelupdateform'></a>Object: EmbeddingModelUpdateForm

- **openai_config** (any) :
- **ollama_config** (any) :
- **azure_openai_config** (any) :
- **RAG_EMBEDDING_ENGINE** (string) *(required)*:
- **RAG_EMBEDDING_MODEL** (string) *(required)*:
- **RAG_EMBEDDING_BATCH_SIZE** (any) :
- **ENABLE_ASYNC_EMBEDDING** (any) :

---
### <a id='model-eventform'></a>Object: EventForm

- **type** (string) *(required)*:
- **data** (object) *(required)*:

---
### <a id='model-featurespermissions'></a>Object: FeaturesPermissions

- **api_keys** (boolean) :
- **notes** (boolean) :
- **channels** (boolean) :
- **folders** (boolean) :
- **direct_tool_servers** (boolean) :
- **web_search** (boolean) :
- **image_generation** (boolean) :
- **code_interpreter** (boolean) :
- **memories** (boolean) :

---
### <a id='model-feedbackform'></a>Object: FeedbackForm

- **type** (string) *(required)*:
- **data** (any) :
- **meta** (any) :
- **snapshot** (any) :

---
### <a id='model-feedbackidresponse'></a>Object: FeedbackIdResponse

- **id** (string) *(required)*:
- **user_id** (string) *(required)*:
- **created_at** (integer) *(required)*:
- **updated_at** (integer) *(required)*:

---
### <a id='model-feedbacklistresponse'></a>Object: FeedbackListResponse

- **items** (array) *(required)*:
- **total** (integer) *(required)*:

---
### <a id='model-feedbackmodel'></a>Object: FeedbackModel

- **id** (string) *(required)*:
- **user_id** (string) *(required)*:
- **version** (integer) *(required)*:
- **type** (string) *(required)*:
- **data** (any) :
- **meta** (any) :
- **snapshot** (any) :
- **created_at** (integer) *(required)*:
- **updated_at** (integer) *(required)*:

---
### <a id='model-feedbackresponse'></a>Object: FeedbackResponse

- **id** (string) *(required)*:
- **user_id** (string) *(required)*:
- **version** (integer) *(required)*:
- **type** (string) *(required)*:
- **data** (any) :
- **meta** (any) :
- **created_at** (integer) *(required)*:
- **updated_at** (integer) *(required)*:

---
### <a id='model-feedbackuserresponse'></a>Object: FeedbackUserResponse

- **id** (string) *(required)*:
- **user_id** (string) *(required)*:
- **version** (integer) *(required)*:
- **type** (string) *(required)*:
- **data** (any) :
- **meta** (any) :
- **created_at** (integer) *(required)*:
- **updated_at** (integer) *(required)*:
- **user** (any) :

---
### <a id='model-filemeta'></a>Object: FileMeta

- **name** (any) :
- **content_type** (any) :
- **size** (any) :

---
### <a id='model-filemetadataresponse'></a>Object: FileMetadataResponse

- **id** (string) *(required)*:
- **hash** (any) :
- **meta** (any) :
- **created_at** (integer) *(required)*:
- **updated_at** (integer) *(required)*:

---
### <a id='model-filemodel'></a>Object: FileModel

- **id** (string) *(required)*:
- **user_id** (string) *(required)*:
- **hash** (any) :
- **filename** (string) *(required)*:
- **path** (any) :
- **data** (any) :
- **meta** (any) :
- **access_control** (any) :
- **created_at** (any) *(required)*:
- **updated_at** (any) *(required)*:

---
### <a id='model-filemodelresponse'></a>Object: FileModelResponse

- **id** (string) *(required)*:
- **user_id** (string) *(required)*:
- **hash** (any) :
- **filename** (string) *(required)*:
- **data** (any) :
- **meta** ([FileMeta](#model-filemeta)) *(required)*:
  - **name** (any) :
  - **content_type** (any) :
  - **size** (any) :
- **created_at** (integer) *(required)*:
- **updated_at** (integer) *(required)*:

---
### <a id='model-fileuserresponse'></a>Object: FileUserResponse

- **id** (string) *(required)*:
- **user_id** (string) *(required)*:
- **hash** (any) :
- **filename** (string) *(required)*:
- **data** (any) :
- **meta** ([FileMeta](#model-filemeta)) *(required)*:
  - **name** (any) :
  - **content_type** (any) :
  - **size** (any) :
- **created_at** (integer) *(required)*:
- **updated_at** (integer) *(required)*:
- **user** (any) :

---
### <a id='model-folderform'></a>Object: FolderForm

- **name** (string) *(required)*:
- **data** (any) :
- **meta** (any) :

---
### <a id='model-folderisexpandedform'></a>Object: FolderIsExpandedForm

- **is_expanded** (boolean) *(required)*:

---
### <a id='model-foldermetadataresponse'></a>Object: FolderMetadataResponse

- **icon** (any) :

---
### <a id='model-foldermodel'></a>Object: FolderModel

- **id** (string) *(required)*:
- **parent_id** (any) :
- **user_id** (string) *(required)*:
- **name** (string) *(required)*:
- **items** (any) :
- **meta** (any) :
- **data** (any) :
- **is_expanded** (boolean) :
- **created_at** (integer) *(required)*:
- **updated_at** (integer) *(required)*:

---
### <a id='model-foldernameidresponse'></a>Object: FolderNameIdResponse

- **id** (string) *(required)*:
- **name** (string) *(required)*:
- **meta** (any) :
- **parent_id** (any) :
- **is_expanded** (boolean) :
- **created_at** (integer) *(required)*:
- **updated_at** (integer) *(required)*:

---
### <a id='model-folderparentidform'></a>Object: FolderParentIdForm

- **parent_id** (any) :

---
### <a id='model-folderupdateform'></a>Object: FolderUpdateForm

- **name** (any) :
- **data** (any) :
- **meta** (any) :

---
### <a id='model-functionform'></a>Object: FunctionForm

- **id** (string) *(required)*:
- **name** (string) *(required)*:
- **content** (string) *(required)*:
- **meta** ([FunctionMeta](#model-functionmeta)) *(required)*:
  - **description** (any) :
  - **manifest** (any) :

---
### <a id='model-functionmeta'></a>Object: FunctionMeta

- **description** (any) :
- **manifest** (any) :

---
### <a id='model-functionmodel'></a>Object: FunctionModel

- **id** (string) *(required)*:
- **user_id** (string) *(required)*:
- **name** (string) *(required)*:
- **type** (string) *(required)*:
- **content** (string) *(required)*:
- **meta** ([FunctionMeta](#model-functionmeta)) *(required)*:
  - **description** (any) :
  - **manifest** (any) :
- **is_active** (boolean) :
- **is_global** (boolean) :
- **updated_at** (integer) *(required)*:
- **created_at** (integer) *(required)*:

---
### <a id='model-functionresponse'></a>Object: FunctionResponse

- **id** (string) *(required)*:
- **user_id** (string) *(required)*:
- **type** (string) *(required)*:
- **name** (string) *(required)*:
- **meta** ([FunctionMeta](#model-functionmeta)) *(required)*:
  - **description** (any) :
  - **manifest** (any) :
- **is_active** (boolean) *(required)*:
- **is_global** (boolean) *(required)*:
- **updated_at** (integer) *(required)*:
- **created_at** (integer) *(required)*:

---
### <a id='model-functionuserresponse'></a>Object: FunctionUserResponse

- **id** (string) *(required)*:
- **user_id** (string) *(required)*:
- **name** (string) *(required)*:
- **type** (string) *(required)*:
- **content** (string) *(required)*:
- **meta** ([FunctionMeta](#model-functionmeta)) *(required)*:
  - **description** (any) :
  - **manifest** (any) :
- **is_active** (boolean) :
- **is_global** (boolean) :
- **updated_at** (integer) *(required)*:
- **created_at** (integer) *(required)*:
- **user** (any) :

---
### <a id='model-functionwithvalvesmodel'></a>Object: FunctionWithValvesModel

- **id** (string) *(required)*:
- **user_id** (string) *(required)*:
- **name** (string) *(required)*:
- **type** (string) *(required)*:
- **content** (string) *(required)*:
- **meta** ([FunctionMeta](#model-functionmeta)) *(required)*:
  - **description** (any) :
  - **manifest** (any) :
- **valves** (any) :
- **is_active** (boolean) :
- **is_global** (boolean) :
- **updated_at** (integer) *(required)*:
- **created_at** (integer) *(required)*:

---
### <a id='model-generatecompletionform'></a>Object: GenerateCompletionForm

- **model** (string) *(required)*:
- **prompt** (string) *(required)*:
- **suffix** (any) :
- **images** (any) :
- **format** (any) :
- **options** (any) :
- **system** (any) :
- **template** (any) :
- **context** (any) :
- **stream** (any) :
- **raw** (any) :
- **keep_alive** (any) :

---
### <a id='model-generateembedform'></a>Object: GenerateEmbedForm

- **model** (string) *(required)*:
- **input** (any) *(required)*:
- **truncate** (any) :
- **options** (any) :
- **keep_alive** (any) :

---
### <a id='model-generateembeddingsform'></a>Object: GenerateEmbeddingsForm

- **model** (string) *(required)*:
- **prompt** (string) *(required)*:
- **options** (any) :
- **keep_alive** (any) :

---
### <a id='model-groupexportresponse'></a>Object: GroupExportResponse

- **id** (string) *(required)*:
- **user_id** (string) *(required)*:
- **name** (string) *(required)*:
- **description** (string) *(required)*:
- **data** (any) :
- **meta** (any) :
- **permissions** (any) :
- **created_at** (integer) *(required)*:
- **updated_at** (integer) *(required)*:
- **member_count** (any) :
- **user_ids** (array) :

---
### <a id='model-groupform'></a>Object: GroupForm

- **name** (string) *(required)*:
- **description** (string) *(required)*:
- **permissions** (any) :
- **data** (any) :

---
### <a id='model-groupresponse'></a>Object: GroupResponse

- **id** (string) *(required)*:
- **user_id** (string) *(required)*:
- **name** (string) *(required)*:
- **description** (string) *(required)*:
- **data** (any) :
- **meta** (any) :
- **permissions** (any) :
- **created_at** (integer) *(required)*:
- **updated_at** (integer) *(required)*:
- **member_count** (any) :

---
### <a id='model-groupupdateform'></a>Object: GroupUpdateForm

- **name** (string) *(required)*:
- **description** (string) *(required)*:
- **permissions** (any) :
- **data** (any) :

---
### <a id='model-httpvalidationerror'></a>Object: HTTPValidationError

- **detail** (array) :

---
### <a id='model-imagesconfig'></a>Object: ImagesConfig

- **ENABLE_IMAGE_GENERATION** (boolean) *(required)*:
- **ENABLE_IMAGE_PROMPT_GENERATION** (boolean) *(required)*:
- **IMAGE_GENERATION_ENGINE** (string) *(required)*:
- **IMAGE_GENERATION_MODEL** (string) *(required)*:
- **IMAGE_SIZE** (any) *(required)*:
- **IMAGE_STEPS** (any) *(required)*:
- **IMAGES_OPENAI_API_BASE_URL** (string) *(required)*:
- **IMAGES_OPENAI_API_KEY** (string) *(required)*:
- **IMAGES_OPENAI_API_VERSION** (string) *(required)*:
- **IMAGES_OPENAI_API_PARAMS** (any) *(required)*:
- **AUTOMATIC1111_BASE_URL** (string) *(required)*:
- **AUTOMATIC1111_API_AUTH** (any) *(required)*:
- **AUTOMATIC1111_PARAMS** (any) *(required)*:
- **COMFYUI_BASE_URL** (string) *(required)*:
- **COMFYUI_API_KEY** (string) *(required)*:
- **COMFYUI_WORKFLOW** (string) *(required)*:
- **COMFYUI_WORKFLOW_NODES** (array) *(required)*:
- **IMAGES_GEMINI_API_BASE_URL** (string) *(required)*:
- **IMAGES_GEMINI_API_KEY** (string) *(required)*:
- **IMAGES_GEMINI_ENDPOINT_METHOD** (string) *(required)*:
- **ENABLE_IMAGE_EDIT** (boolean) *(required)*:
- **IMAGE_EDIT_ENGINE** (string) *(required)*:
- **IMAGE_EDIT_MODEL** (string) *(required)*:
- **IMAGE_EDIT_SIZE** (any) *(required)*:
- **IMAGES_EDIT_OPENAI_API_BASE_URL** (string) *(required)*:
- **IMAGES_EDIT_OPENAI_API_KEY** (string) *(required)*:
- **IMAGES_EDIT_OPENAI_API_VERSION** (string) *(required)*:
- **IMAGES_EDIT_GEMINI_API_BASE_URL** (string) *(required)*:
- **IMAGES_EDIT_GEMINI_API_KEY** (string) *(required)*:
- **IMAGES_EDIT_COMFYUI_BASE_URL** (string) *(required)*:
- **IMAGES_EDIT_COMFYUI_API_KEY** (string) *(required)*:
- **IMAGES_EDIT_COMFYUI_WORKFLOW** (string) *(required)*:
- **IMAGES_EDIT_COMFYUI_WORKFLOW_NODES** (array) *(required)*:

---
### <a id='model-importconfigform'></a>Object: ImportConfigForm

- **config** (object) *(required)*:

---
### <a id='model-knowledgeaccesslistresponse'></a>Object: KnowledgeAccessListResponse

- **items** (array) *(required)*:
- **total** (integer) *(required)*:

---
### <a id='model-knowledgeaccessresponse'></a>Object: KnowledgeAccessResponse

- **id** (string) *(required)*:
- **user_id** (string) *(required)*:
- **name** (string) *(required)*:
- **description** (string) *(required)*:
- **meta** (any) :
- **access_control** (any) :
- **created_at** (integer) *(required)*:
- **updated_at** (integer) *(required)*:
- **user** (any) :
- **write_access** (any) :

---
### <a id='model-knowledgefileidform'></a>Object: KnowledgeFileIdForm

- **file_id** (string) *(required)*:

---
### <a id='model-knowledgefilelistresponse'></a>Object: KnowledgeFileListResponse

- **items** (array) *(required)*:
- **total** (integer) *(required)*:

---
### <a id='model-knowledgefilesresponse'></a>Object: KnowledgeFilesResponse

- **id** (string) *(required)*:
- **user_id** (string) *(required)*:
- **name** (string) *(required)*:
- **description** (string) *(required)*:
- **meta** (any) :
- **access_control** (any) :
- **created_at** (integer) *(required)*:
- **updated_at** (integer) *(required)*:
- **files** (any) :
- **write_access** (any) :

---
### <a id='model-knowledgeform'></a>Object: KnowledgeForm

- **name** (string) *(required)*:
- **description** (string) *(required)*:
- **access_control** (any) :

---
### <a id='model-knowledgeresponse'></a>Object: KnowledgeResponse

- **id** (string) *(required)*:
- **user_id** (string) *(required)*:
- **name** (string) *(required)*:
- **description** (string) *(required)*:
- **meta** (any) :
- **access_control** (any) :
- **created_at** (integer) *(required)*:
- **updated_at** (integer) *(required)*:
- **files** (any) :

---
### <a id='model-ldapconfigform'></a>Object: LdapConfigForm

- **enable_ldap** (any) :

---
### <a id='model-ldapform'></a>Object: LdapForm

- **user** (string) *(required)*:
- **password** (string) *(required)*:

---
### <a id='model-ldapserverconfig'></a>Object: LdapServerConfig

- **label** (string) *(required)*:
- **host** (string) *(required)*:
- **port** (any) :
- **attribute_for_mail** (string) :
- **attribute_for_username** (string) :
- **app_dn** (string) *(required)*:
- **app_dn_password** (string) *(required)*:
- **search_base** (string) *(required)*:
- **search_filters** (string) :
- **use_tls** (boolean) :
- **certificate_path** (any) :
- **validate_cert** (boolean) :
- **ciphers** (any) :

---
### <a id='model-leaderboardentry'></a>Object: LeaderboardEntry

- **model_id** (string) *(required)*:
- **rating** (integer) *(required)*:
- **won** (integer) *(required)*:
- **lost** (integer) *(required)*:
- **count** (integer) *(required)*:
- **top_tags** (array) *(required)*:

---
### <a id='model-leaderboardresponse'></a>Object: LeaderboardResponse

- **entries** (array) *(required)*:

---
### <a id='model-loadurlform'></a>Object: LoadUrlForm

- **url** (string) *(required)*:

---
### <a id='model-markdownform'></a>Object: MarkdownForm

- **md** (string) *(required)*:

---
### <a id='model-memorymodel'></a>Object: MemoryModel

- **id** (string) *(required)*:
- **user_id** (string) *(required)*:
- **content** (string) *(required)*:
- **updated_at** (integer) *(required)*:
- **created_at** (integer) *(required)*:

---
### <a id='model-memoryupdatemodel'></a>Object: MemoryUpdateModel

- **content** (any) :

---
### <a id='model-messageform'></a>Object: MessageForm

- **temp_id** (any) :
- **content** (string) *(required)*:
- **reply_to_id** (any) :
- **parent_id** (any) :
- **data** (any) :
- **meta** (any) :

---
### <a id='model-messagemodel'></a>Object: MessageModel

- **id** (string) *(required)*:
- **user_id** (string) *(required)*:
- **channel_id** (any) :
- **reply_to_id** (any) :
- **parent_id** (any) :
- **is_pinned** (boolean) :
- **pinned_by** (any) :
- **pinned_at** (any) :
- **content** (string) *(required)*:
- **data** (any) :
- **meta** (any) :
- **created_at** (integer) *(required)*:
- **updated_at** (integer) *(required)*:

---
### <a id='model-messageresponse'></a>Object: MessageResponse

- **id** (string) *(required)*:
- **user_id** (string) *(required)*:
- **channel_id** (any) :
- **reply_to_id** (any) :
- **parent_id** (any) :
- **is_pinned** (boolean) :
- **pinned_by** (any) :
- **pinned_at** (any) :
- **content** (string) *(required)*:
- **data** (any) :
- **meta** (any) :
- **created_at** (integer) *(required)*:
- **updated_at** (integer) *(required)*:
- **user** (any) :
- **reply_to_message** (any) :
- **latest_reply_at** (any) *(required)*:
- **reply_count** (integer) *(required)*:
- **reactions** (array) *(required)*:

---
### <a id='model-messagestats'></a>Object: MessageStats

- **id** (string) *(required)*:
- **role** (string) *(required)*:
- **model** (any) :
- **content_length** (integer) *(required)*:
- **token_count** (any) :
- **timestamp** (any) :
- **rating** (any) :
- **tags** (any) :

---
### <a id='model-messageuserresponse'></a>Object: MessageUserResponse

- **id** (string) *(required)*:
- **user_id** (string) *(required)*:
- **channel_id** (any) :
- **reply_to_id** (any) :
- **parent_id** (any) :
- **is_pinned** (boolean) :
- **pinned_by** (any) :
- **pinned_at** (any) :
- **content** (string) *(required)*:
- **data** (any) :
- **meta** (any) :
- **created_at** (integer) *(required)*:
- **updated_at** (integer) *(required)*:
- **user** (any) :
- **reply_to_message** (any) :
- **latest_reply_at** (any) *(required)*:
- **reply_count** (integer) *(required)*:
- **reactions** (array) *(required)*:

---
### <a id='model-messageuserslimresponse'></a>Object: MessageUserSlimResponse

- **id** (string) *(required)*:
- **user_id** (string) *(required)*:
- **channel_id** (any) :
- **reply_to_id** (any) :
- **parent_id** (any) :
- **is_pinned** (boolean) :
- **pinned_by** (any) :
- **pinned_at** (any) :
- **content** (string) *(required)*:
- **data** (any) :
- **meta** (any) :
- **created_at** (integer) *(required)*:
- **updated_at** (integer) *(required)*:
- **user** (any) :

---
### <a id='model-messagewithreactionsresponse'></a>Object: MessageWithReactionsResponse

- **id** (string) *(required)*:
- **user_id** (string) *(required)*:
- **channel_id** (any) :
- **reply_to_id** (any) :
- **parent_id** (any) :
- **is_pinned** (boolean) :
- **pinned_by** (any) :
- **pinned_at** (any) :
- **content** (string) *(required)*:
- **data** (any) :
- **meta** (any) :
- **created_at** (integer) *(required)*:
- **updated_at** (integer) *(required)*:
- **user** (any) :
- **reactions** (array) *(required)*:

---
### <a id='model-modelaccesslistresponse'></a>Object: ModelAccessListResponse

- **items** (array) *(required)*:
- **total** (integer) *(required)*:

---
### <a id='model-modelaccessresponse'></a>Object: ModelAccessResponse

- **id** (string) *(required)*:
- **user_id** (string) *(required)*:
- **base_model_id** (any) :
- **name** (string) *(required)*:
- **params** ([ModelParams](#model-modelparams)) *(required)*:
- **meta** ([ModelMeta](#model-modelmeta)) *(required)*:
  - **profile_image_url** (any) :
  - **description** (any) :
  - **capabilities** (any) :
- **access_control** (any) :
- **is_active** (boolean) *(required)*:
- **updated_at** (integer) *(required)*:
- **created_at** (integer) *(required)*:
- **user** (any) :
- **write_access** (any) :

---
### <a id='model-modelform'></a>Object: ModelForm

- **id** (string) *(required)*:
- **base_model_id** (any) :
- **name** (string) *(required)*:
- **meta** ([ModelMeta](#model-modelmeta)) *(required)*:
  - **profile_image_url** (any) :
  - **description** (any) :
  - **capabilities** (any) :
- **params** ([ModelParams](#model-modelparams)) *(required)*:
- **access_control** (any) :
- **is_active** (boolean) :

---
### <a id='model-modelhistoryentry'></a>Object: ModelHistoryEntry

- **date** (string) *(required)*:
- **won** (integer) *(required)*:
- **lost** (integer) *(required)*:

---
### <a id='model-modelhistoryresponse'></a>Object: ModelHistoryResponse

- **model_id** (string) *(required)*:
- **history** (array) *(required)*:

---
### <a id='model-modelidform'></a>Object: ModelIdForm

- **id** (string) *(required)*:

---
### <a id='model-modelmeta'></a>Object: ModelMeta

- **profile_image_url** (any) :
- **description** (any) :
- **capabilities** (any) :

---
### <a id='model-modelmodel'></a>Object: ModelModel

- **id** (string) *(required)*:
- **user_id** (string) *(required)*:
- **base_model_id** (any) :
- **name** (string) *(required)*:
- **params** ([ModelParams](#model-modelparams)) *(required)*:
- **meta** ([ModelMeta](#model-modelmeta)) *(required)*:
  - **profile_image_url** (any) :
  - **description** (any) :
  - **capabilities** (any) :
- **access_control** (any) :
- **is_active** (boolean) *(required)*:
- **updated_at** (integer) *(required)*:
- **created_at** (integer) *(required)*:

---
### <a id='model-modelnameform'></a>Object: ModelNameForm

- **model** (any) :

---
### <a id='model-modelparams'></a>Object: ModelParams


---
### <a id='model-modelresponse'></a>Object: ModelResponse

- **id** (string) *(required)*:
- **user_id** (string) *(required)*:
- **base_model_id** (any) :
- **name** (string) *(required)*:
- **params** ([ModelParams](#model-modelparams)) *(required)*:
- **meta** ([ModelMeta](#model-modelmeta)) *(required)*:
  - **profile_image_url** (any) :
  - **description** (any) :
  - **capabilities** (any) :
- **access_control** (any) :
- **is_active** (boolean) *(required)*:
- **updated_at** (integer) *(required)*:
- **created_at** (integer) *(required)*:

---
### <a id='model-modelsconfigform'></a>Object: ModelsConfigForm

- **DEFAULT_MODELS** (any) *(required)*:
- **DEFAULT_PINNED_MODELS** (any) *(required)*:
- **MODEL_ORDER_LIST** (any) *(required)*:

---
### <a id='model-modelsimportform'></a>Object: ModelsImportForm

- **models** (array) *(required)*:

---
### <a id='model-noteform'></a>Object: NoteForm

- **title** (string) *(required)*:
- **data** (any) :
- **meta** (any) :
- **access_control** (any) :

---
### <a id='model-noteitemresponse'></a>Object: NoteItemResponse

- **id** (string) *(required)*:
- **title** (string) *(required)*:
- **data** (any) *(required)*:
- **updated_at** (integer) *(required)*:
- **created_at** (integer) *(required)*:
- **user** (any) :

---
### <a id='model-notelistresponse'></a>Object: NoteListResponse

- **items** (array) *(required)*:
- **total** (integer) *(required)*:

---
### <a id='model-notemodel'></a>Object: NoteModel

- **id** (string) *(required)*:
- **user_id** (string) *(required)*:
- **title** (string) *(required)*:
- **data** (any) :
- **meta** (any) :
- **access_control** (any) :
- **created_at** (integer) *(required)*:
- **updated_at** (integer) *(required)*:

---
### <a id='model-noteresponse'></a>Object: NoteResponse

- **id** (string) *(required)*:
- **user_id** (string) *(required)*:
- **title** (string) *(required)*:
- **data** (any) :
- **meta** (any) :
- **access_control** (any) :
- **created_at** (integer) *(required)*:
- **updated_at** (integer) *(required)*:
- **write_access** (boolean) :

---
### <a id='model-noteuserresponse'></a>Object: NoteUserResponse

- **id** (string) *(required)*:
- **user_id** (string) *(required)*:
- **title** (string) *(required)*:
- **data** (any) :
- **meta** (any) :
- **access_control** (any) :
- **created_at** (integer) *(required)*:
- **updated_at** (integer) *(required)*:
- **user** (any) :

---
### <a id='model-oauthclientregistrationform'></a>Object: OAuthClientRegistrationForm

- **url** (string) *(required)*:
- **client_id** (string) *(required)*:
- **client_name** (any) :

---
### <a id='model-ollamaconfigform'></a>Object: OllamaConfigForm

- **url** (string) *(required)*:
- **key** (string) *(required)*:

---
### <a id='model-openaiconfigform'></a>Object: OpenAIConfigForm

- **url** (string) *(required)*:
- **key** (string) *(required)*:

---
### <a id='model-pinmessageform'></a>Object: PinMessageForm

- **is_pinned** (boolean) *(required)*:

---
### <a id='model-processfileform'></a>Object: ProcessFileForm

- **file_id** (string) *(required)*:
- **content** (any) :
- **collection_name** (any) :

---
### <a id='model-processtextform'></a>Object: ProcessTextForm

- **name** (string) *(required)*:
- **content** (string) *(required)*:
- **collection_name** (any) :

---
### <a id='model-processurlform'></a>Object: ProcessUrlForm

- **collection_name** (any) :
- **url** (string) *(required)*:

---
### <a id='model-promptaccessresponse'></a>Object: PromptAccessResponse

- **command** (string) *(required)*:
- **user_id** (string) *(required)*:
- **title** (string) *(required)*:
- **content** (string) *(required)*:
- **timestamp** (integer) *(required)*:
- **access_control** (any) :
- **user** (any) :
- **write_access** (any) :

---
### <a id='model-promptform'></a>Object: PromptForm

- **command** (string) *(required)*:
- **title** (string) *(required)*:
- **content** (string) *(required)*:
- **access_control** (any) :

---
### <a id='model-promptmodel'></a>Object: PromptModel

- **command** (string) *(required)*:
- **user_id** (string) *(required)*:
- **title** (string) *(required)*:
- **content** (string) *(required)*:
- **timestamp** (integer) *(required)*:
- **access_control** (any) :

---
### <a id='model-promptsuggestion'></a>Object: PromptSuggestion

- **title** (array) *(required)*:
- **content** (string) *(required)*:

---
### <a id='model-pushmodelform'></a>Object: PushModelForm

- **model** (string) *(required)*:
- **insecure** (any) :
- **stream** (any) :

---
### <a id='model-querycollectionsform'></a>Object: QueryCollectionsForm

- **collection_names** (array) *(required)*:
- **query** (string) *(required)*:
- **k** (any) :
- **k_reranker** (any) :
- **r** (any) :
- **hybrid** (any) :
- **hybrid_bm25_weight** (any) :
- **enable_enriched_texts** (any) :

---
### <a id='model-querydocform'></a>Object: QueryDocForm

- **collection_name** (string) *(required)*:
- **query** (string) *(required)*:
- **k** (any) :
- **k_reranker** (any) :
- **r** (any) :
- **hybrid** (any) :

---
### <a id='model-querymemoryform'></a>Object: QueryMemoryForm

- **content** (string) *(required)*:
- **k** (any) :

---
### <a id='model-ratingdata'></a>Object: RatingData

- **rating** (any) :
- **model_id** (any) :
- **sibling_model_ids** (any) :
- **reason** (any) :
- **comment** (any) :

---
### <a id='model-reactionform'></a>Object: ReactionForm

- **name** (string) *(required)*:

---
### <a id='model-reactions'></a>Object: Reactions

- **name** (string) *(required)*:
- **users** (array) *(required)*:
- **count** (integer) *(required)*:

---
### <a id='model-removemembersform'></a>Object: RemoveMembersForm

- **user_ids** (array) :

---
### <a id='model-sttconfigform'></a>Object: STTConfigForm

- **OPENAI_API_BASE_URL** (string) *(required)*:
- **OPENAI_API_KEY** (string) *(required)*:
- **ENGINE** (string) *(required)*:
- **MODEL** (string) *(required)*:
- **SUPPORTED_CONTENT_TYPES** (array) :
- **WHISPER_MODEL** (string) *(required)*:
- **DEEPGRAM_API_KEY** (string) *(required)*:
- **AZURE_API_KEY** (string) *(required)*:
- **AZURE_REGION** (string) *(required)*:
- **AZURE_LOCALES** (string) *(required)*:
- **AZURE_BASE_URL** (string) *(required)*:
- **AZURE_MAX_SPEAKERS** (string) *(required)*:
- **MISTRAL_API_KEY** (string) *(required)*:
- **MISTRAL_API_BASE_URL** (string) *(required)*:
- **MISTRAL_USE_CHAT_COMPLETIONS** (boolean) *(required)*:

---
### <a id='model-searchform'></a>Object: SearchForm

- **queries** (array) *(required)*:

---
### <a id='model-sessionuserinforesponse'></a>Object: SessionUserInfoResponse

- **status_emoji** (any) :
- **status_message** (any) :
- **status_expires_at** (any) :
- **id** (string) *(required)*:
- **name** (string) *(required)*:
- **role** (string) *(required)*:
- **email** (string) *(required)*:
- **profile_image_url** (string) *(required)*:
- **token** (string) *(required)*:
- **token_type** (string) *(required)*:
- **expires_at** (any) :
- **permissions** (any) :
- **bio** (any) :
- **gender** (any) :
- **date_of_birth** (any) :

---
### <a id='model-sessionuserresponse'></a>Object: SessionUserResponse

- **id** (string) *(required)*:
- **name** (string) *(required)*:
- **role** (string) *(required)*:
- **email** (string) *(required)*:
- **profile_image_url** (string) *(required)*:
- **token** (string) *(required)*:
- **token_type** (string) *(required)*:
- **expires_at** (any) :
- **permissions** (any) :

---
### <a id='model-setbannersform'></a>Object: SetBannersForm

- **banners** (array) *(required)*:

---
### <a id='model-setdefaultsuggestionsform'></a>Object: SetDefaultSuggestionsForm

- **suggestions** (array) *(required)*:

---
### <a id='model-settingspermissions'></a>Object: SettingsPermissions

- **interface** (boolean) :

---
### <a id='model-sharingpermissions'></a>Object: SharingPermissions

- **models** (boolean) :
- **public_models** (boolean) :
- **knowledge** (boolean) :
- **public_knowledge** (boolean) :
- **prompts** (boolean) :
- **public_prompts** (boolean) :
- **tools** (boolean) :
- **public_tools** (boolean) :
- **notes** (boolean) :
- **public_notes** (boolean) :

---
### <a id='model-signinform'></a>Object: SigninForm

- **email** (string) *(required)*:
- **password** (string) *(required)*:

---
### <a id='model-signinresponse'></a>Object: SigninResponse

- **id** (string) *(required)*:
- **name** (string) *(required)*:
- **role** (string) *(required)*:
- **email** (string) *(required)*:
- **profile_image_url** (string) *(required)*:
- **token** (string) *(required)*:
- **token_type** (string) *(required)*:

---
### <a id='model-signupform'></a>Object: SignupForm

- **name** (string) *(required)*:
- **email** (string) *(required)*:
- **password** (string) *(required)*:
- **profile_image_url** (any) :

---
### <a id='model-snapshotdata'></a>Object: SnapshotData

- **chat** (any) :

---
### <a id='model-syncfunctionsform'></a>Object: SyncFunctionsForm

- **functions** (array) :

---
### <a id='model-syncmodelsform'></a>Object: SyncModelsForm

- **models** (array) :

---
### <a id='model-ttsconfigform'></a>Object: TTSConfigForm

- **OPENAI_API_BASE_URL** (string) *(required)*:
- **OPENAI_API_KEY** (string) *(required)*:
- **OPENAI_PARAMS** (any) :
- **API_KEY** (string) *(required)*:
- **ENGINE** (string) *(required)*:
- **MODEL** (string) *(required)*:
- **VOICE** (string) *(required)*:
- **SPLIT_ON** (string) *(required)*:
- **AZURE_SPEECH_REGION** (string) *(required)*:
- **AZURE_SPEECH_BASE_URL** (string) *(required)*:
- **AZURE_SPEECH_OUTPUT_FORMAT** (string) *(required)*:

---
### <a id='model-tagfilterform'></a>Object: TagFilterForm

- **name** (string) *(required)*:
- **skip** (any) :
- **limit** (any) :

---
### <a id='model-tagform'></a>Object: TagForm

- **name** (string) *(required)*:

---
### <a id='model-tagmodel'></a>Object: TagModel

- **id** (string) *(required)*:
- **name** (string) *(required)*:
- **user_id** (string) *(required)*:
- **meta** (any) :

---
### <a id='model-taskconfigform'></a>Object: TaskConfigForm

- **TASK_MODEL** (any) *(required)*:
- **TASK_MODEL_EXTERNAL** (any) *(required)*:
- **ENABLE_TITLE_GENERATION** (boolean) *(required)*:
- **TITLE_GENERATION_PROMPT_TEMPLATE** (string) *(required)*:
- **IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE** (string) *(required)*:
- **ENABLE_AUTOCOMPLETE_GENERATION** (boolean) *(required)*:
- **AUTOCOMPLETE_GENERATION_INPUT_MAX_LENGTH** (integer) *(required)*:
- **TAGS_GENERATION_PROMPT_TEMPLATE** (string) *(required)*:
- **FOLLOW_UP_GENERATION_PROMPT_TEMPLATE** (string) *(required)*:
- **ENABLE_FOLLOW_UP_GENERATION** (boolean) *(required)*:
- **ENABLE_TAGS_GENERATION** (boolean) *(required)*:
- **ENABLE_SEARCH_QUERY_GENERATION** (boolean) *(required)*:
- **ENABLE_RETRIEVAL_QUERY_GENERATION** (boolean) *(required)*:
- **QUERY_GENERATION_PROMPT_TEMPLATE** (string) *(required)*:
- **TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE** (string) *(required)*:
- **VOICE_MODE_PROMPT_TEMPLATE** (any) *(required)*:

---
### <a id='model-toolaccessresponse'></a>Object: ToolAccessResponse

- **id** (string) *(required)*:
- **user_id** (string) *(required)*:
- **name** (string) *(required)*:
- **meta** ([ToolMeta](#model-toolmeta)) *(required)*:
  - **description** (any) :
  - **manifest** (any) :
- **access_control** (any) :
- **updated_at** (integer) *(required)*:
- **created_at** (integer) *(required)*:
- **user** (any) :
- **write_access** (any) :

---
### <a id='model-toolform'></a>Object: ToolForm

- **id** (string) *(required)*:
- **name** (string) *(required)*:
- **content** (string) *(required)*:
- **meta** ([ToolMeta](#model-toolmeta)) *(required)*:
  - **description** (any) :
  - **manifest** (any) :
- **access_control** (any) :

---
### <a id='model-toolmeta'></a>Object: ToolMeta

- **description** (any) :
- **manifest** (any) :

---
### <a id='model-toolmodel'></a>Object: ToolModel

- **id** (string) *(required)*:
- **user_id** (string) *(required)*:
- **name** (string) *(required)*:
- **content** (string) *(required)*:
- **specs** (array) *(required)*:
- **meta** ([ToolMeta](#model-toolmeta)) *(required)*:
  - **description** (any) :
  - **manifest** (any) :
- **access_control** (any) :
- **updated_at** (integer) *(required)*:
- **created_at** (integer) *(required)*:

---
### <a id='model-toolresponse'></a>Object: ToolResponse

- **id** (string) *(required)*:
- **user_id** (string) *(required)*:
- **name** (string) *(required)*:
- **meta** ([ToolMeta](#model-toolmeta)) *(required)*:
  - **description** (any) :
  - **manifest** (any) :
- **access_control** (any) :
- **updated_at** (integer) *(required)*:
- **created_at** (integer) *(required)*:

---
### <a id='model-toolserverconnection'></a>Object: ToolServerConnection

- **url** (string) *(required)*:
- **path** (string) *(required)*:
- **type** (any) :
- **auth_type** (any) *(required)*:
- **headers** (any) :
- **key** (any) *(required)*:
- **config** (any) *(required)*:

---
### <a id='model-toolserversconfigform'></a>Object: ToolServersConfigForm

- **TOOL_SERVER_CONNECTIONS** (array) *(required)*:

---
### <a id='model-tooluserresponse'></a>Object: ToolUserResponse

- **id** (string) *(required)*:
- **user_id** (string) *(required)*:
- **name** (string) *(required)*:
- **meta** ([ToolMeta](#model-toolmeta)) *(required)*:
  - **description** (any) :
  - **manifest** (any) :
- **access_control** (any) :
- **updated_at** (integer) *(required)*:
- **created_at** (integer) *(required)*:
- **user** (any) :

---
### <a id='model-updateactivememberform'></a>Object: UpdateActiveMemberForm

- **is_active** (boolean) *(required)*:

---
### <a id='model-updateconfigform'></a>Object: UpdateConfigForm

- **ENABLE_EVALUATION_ARENA_MODELS** (any) :
- **EVALUATION_ARENA_MODELS** (any) :

---
### <a id='model-updatemembersform'></a>Object: UpdateMembersForm

- **user_ids** (array) :
- **group_ids** (array) :

---
### <a id='model-updatepasswordform'></a>Object: UpdatePasswordForm

- **password** (string) *(required)*:
- **new_password** (string) *(required)*:

---
### <a id='model-updateprofileform'></a>Object: UpdateProfileForm

- **profile_image_url** (string) *(required)*:
- **name** (string) *(required)*:
- **bio** (any) :
- **gender** (any) :
- **date_of_birth** (any) :

---
### <a id='model-updatetimezoneform'></a>Object: UpdateTimezoneForm

- **timezone** (string) *(required)*:

---
### <a id='model-urlform'></a>Object: UrlForm

- **url** (string) *(required)*:

---
### <a id='model-useractiveresponse'></a>Object: UserActiveResponse

- **status_emoji** (any) :
- **status_message** (any) :
- **status_expires_at** (any) :
- **name** (string) *(required)*:
- **profile_image_url** (any) :
- **groups** (any) :
- **is_active** (boolean) *(required)*:

---
### <a id='model-usergroupidslistresponse'></a>Object: UserGroupIdsListResponse

- **users** (array) *(required)*:
- **total** (integer) *(required)*:

---
### <a id='model-usergroupidsmodel'></a>Object: UserGroupIdsModel

- **id** (string) *(required)*:
- **email** (string) *(required)*:
- **username** (any) :
- **role** (string) :
- **name** (string) *(required)*:
- **profile_image_url** (string) *(required)*:
- **profile_banner_image_url** (any) :
- **bio** (any) :
- **gender** (any) :
- **date_of_birth** (any) :
- **timezone** (any) :
- **presence_state** (any) :
- **status_emoji** (any) :
- **status_message** (any) :
- **status_expires_at** (any) :
- **info** (any) :
- **settings** (any) :
- **oauth** (any) :
- **last_active_at** (integer) *(required)*:
- **updated_at** (integer) *(required)*:
- **created_at** (integer) *(required)*:
- **group_ids** (array) :

---
### <a id='model-useridnamestatusresponse'></a>Object: UserIdNameStatusResponse

- **status_emoji** (any) :
- **status_message** (any) :
- **status_expires_at** (any) :
- **id** (string) *(required)*:
- **name** (string) *(required)*:
- **is_active** (any) :

---
### <a id='model-useridsform'></a>Object: UserIdsForm

- **user_ids** (any) :

---
### <a id='model-userinfolistresponse'></a>Object: UserInfoListResponse

- **users** (array) *(required)*:
- **total** (integer) *(required)*:

---
### <a id='model-userinforesponse'></a>Object: UserInfoResponse

- **status_emoji** (any) :
- **status_message** (any) :
- **status_expires_at** (any) :
- **id** (string) *(required)*:
- **name** (string) *(required)*:
- **email** (string) *(required)*:
- **role** (string) *(required)*:

---
### <a id='model-userlistresponse'></a>Object: UserListResponse

- **users** (array) *(required)*:
- **total** (integer) *(required)*:

---
### <a id='model-usermodel'></a>Object: UserModel

- **id** (string) *(required)*:
- **email** (string) *(required)*:
- **username** (any) :
- **role** (string) :
- **name** (string) *(required)*:
- **profile_image_url** (string) *(required)*:
- **profile_banner_image_url** (any) :
- **bio** (any) :
- **gender** (any) :
- **date_of_birth** (any) :
- **timezone** (any) :
- **presence_state** (any) :
- **status_emoji** (any) :
- **status_message** (any) :
- **status_expires_at** (any) :
- **info** (any) :
- **settings** (any) :
- **oauth** (any) :
- **last_active_at** (integer) *(required)*:
- **updated_at** (integer) *(required)*:
- **created_at** (integer) *(required)*:

---
### <a id='model-usermodelresponse'></a>Object: UserModelResponse

- **id** (string) *(required)*:
- **email** (string) *(required)*:
- **username** (any) :
- **role** (string) :
- **name** (string) *(required)*:
- **profile_image_url** (string) *(required)*:
- **profile_banner_image_url** (any) :
- **bio** (any) :
- **gender** (any) :
- **date_of_birth** (any) :
- **timezone** (any) :
- **presence_state** (any) :
- **status_emoji** (any) :
- **status_message** (any) :
- **status_expires_at** (any) :
- **info** (any) :
- **settings** (any) :
- **oauth** (any) :
- **last_active_at** (integer) *(required)*:
- **updated_at** (integer) *(required)*:
- **created_at** (integer) *(required)*:

---
### <a id='model-usernameresponse'></a>Object: UserNameResponse

- **id** (string) *(required)*:
- **name** (string) *(required)*:
- **role** (string) *(required)*:

---
### <a id='model-userpermissions'></a>Object: UserPermissions

- **workspace** ([WorkspacePermissions](#model-workspacepermissions)) *(required)*:
  - **models** (boolean) :
  - **knowledge** (boolean) :
  - **prompts** (boolean) :
  - **tools** (boolean) :
  - **models_import** (boolean) :
  - **models_export** (boolean) :
  - **prompts_import** (boolean) :
  - **prompts_export** (boolean) :
  - **tools_import** (boolean) :
  - **tools_export** (boolean) :
- **sharing** ([SharingPermissions](#model-sharingpermissions)) *(required)*:
  - **models** (boolean) :
  - **public_models** (boolean) :
  - **knowledge** (boolean) :
  - **public_knowledge** (boolean) :
  - **prompts** (boolean) :
  - **public_prompts** (boolean) :
  - **tools** (boolean) :
  - **public_tools** (boolean) :
  - **notes** (boolean) :
  - **public_notes** (boolean) :
- **chat** ([ChatPermissions](#model-chatpermissions)) *(required)*:
  - **controls** (boolean) :
  - **valves** (boolean) :
  - **system_prompt** (boolean) :
  - **params** (boolean) :
  - **file_upload** (boolean) :
  - **delete** (boolean) :
  - **delete_message** (boolean) :
  - **continue_response** (boolean) :
  - **regenerate_response** (boolean) :
  - **rate_response** (boolean) :
  - **edit** (boolean) :
  - **share** (boolean) :
  - **export** (boolean) :
  - **stt** (boolean) :
  - **tts** (boolean) :
  - **call** (boolean) :
  - **multiple_models** (boolean) :
  - **temporary** (boolean) :
  - **temporary_enforced** (boolean) :
- **features** ([FeaturesPermissions](#model-featurespermissions)) *(required)*:
  - **api_keys** (boolean) :
  - **notes** (boolean) :
  - **channels** (boolean) :
  - **folders** (boolean) :
  - **direct_tool_servers** (boolean) :
  - **web_search** (boolean) :
  - **image_generation** (boolean) :
  - **code_interpreter** (boolean) :
  - **memories** (boolean) :
- **settings** ([SettingsPermissions](#model-settingspermissions)) *(required)*:
  - **interface** (boolean) :

---
### <a id='model-userprofileimageresponse'></a>Object: UserProfileImageResponse

- **id** (string) *(required)*:
- **name** (string) *(required)*:
- **role** (string) *(required)*:
- **email** (string) *(required)*:
- **profile_image_url** (string) *(required)*:

---
### <a id='model-userresponse'></a>Object: UserResponse

- **id** (string) *(required)*:
- **name** (string) *(required)*:
- **role** (string) *(required)*:
- **email** (string) *(required)*:

---
### <a id='model-usersettings'></a>Object: UserSettings

- **ui** (any) :

---
### <a id='model-userstatus'></a>Object: UserStatus

- **status_emoji** (any) :
- **status_message** (any) :
- **status_expires_at** (any) :

---
### <a id='model-userupdateform'></a>Object: UserUpdateForm

- **role** (string) *(required)*:
- **name** (string) *(required)*:
- **email** (string) *(required)*:
- **profile_image_url** (string) *(required)*:
- **password** (any) :

---
### <a id='model-validationerror'></a>Object: ValidationError

- **loc** (array) *(required)*:
- **msg** (string) *(required)*:
- **type** (string) *(required)*:

---
### <a id='model-webconfig'></a>Object: WebConfig

- **ENABLE_WEB_SEARCH** (any) :
- **WEB_SEARCH_ENGINE** (any) :
- **WEB_SEARCH_TRUST_ENV** (any) :
- **WEB_SEARCH_RESULT_COUNT** (any) :
- **WEB_SEARCH_CONCURRENT_REQUESTS** (any) :
- **WEB_LOADER_CONCURRENT_REQUESTS** (any) :
- **WEB_SEARCH_DOMAIN_FILTER_LIST** (any) :
- **BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL** (any) :
- **BYPASS_WEB_SEARCH_WEB_LOADER** (any) :
- **OLLAMA_CLOUD_WEB_SEARCH_API_KEY** (any) :
- **SEARXNG_QUERY_URL** (any) :
- **SEARXNG_LANGUAGE** (any) :
- **YACY_QUERY_URL** (any) :
- **YACY_USERNAME** (any) :
- **YACY_PASSWORD** (any) :
- **GOOGLE_PSE_API_KEY** (any) :
- **GOOGLE_PSE_ENGINE_ID** (any) :
- **BRAVE_SEARCH_API_KEY** (any) :
- **KAGI_SEARCH_API_KEY** (any) :
- **MOJEEK_SEARCH_API_KEY** (any) :
- **BOCHA_SEARCH_API_KEY** (any) :
- **SERPSTACK_API_KEY** (any) :
- **SERPSTACK_HTTPS** (any) :
- **SERPER_API_KEY** (any) :
- **SERPLY_API_KEY** (any) :
- **DDGS_BACKEND** (any) :
- **TAVILY_API_KEY** (any) :
- **SEARCHAPI_API_KEY** (any) :
- **SEARCHAPI_ENGINE** (any) :
- **SERPAPI_API_KEY** (any) :
- **SERPAPI_ENGINE** (any) :
- **JINA_API_KEY** (any) :
- **JINA_API_BASE_URL** (any) :
- **BING_SEARCH_V7_ENDPOINT** (any) :
- **BING_SEARCH_V7_SUBSCRIPTION_KEY** (any) :
- **EXA_API_KEY** (any) :
- **PERPLEXITY_API_KEY** (any) :
- **PERPLEXITY_MODEL** (any) :
- **PERPLEXITY_SEARCH_CONTEXT_USAGE** (any) :
- **PERPLEXITY_SEARCH_API_URL** (any) :
- **SOUGOU_API_SID** (any) :
- **SOUGOU_API_SK** (any) :
- **WEB_LOADER_ENGINE** (any) :
- **WEB_LOADER_TIMEOUT** (any) :
- **ENABLE_WEB_LOADER_SSL_VERIFICATION** (any) :
- **PLAYWRIGHT_WS_URL** (any) :
- **PLAYWRIGHT_TIMEOUT** (any) :
- **FIRECRAWL_API_KEY** (any) :
- **FIRECRAWL_API_BASE_URL** (any) :
- **FIRECRAWL_TIMEOUT** (any) :
- **TAVILY_EXTRACT_DEPTH** (any) :
- **EXTERNAL_WEB_SEARCH_URL** (any) :
- **EXTERNAL_WEB_SEARCH_API_KEY** (any) :
- **EXTERNAL_WEB_LOADER_URL** (any) :
- **EXTERNAL_WEB_LOADER_API_KEY** (any) :
- **YOUTUBE_LOADER_LANGUAGE** (any) :
- **YOUTUBE_LOADER_PROXY_URL** (any) :
- **YOUTUBE_LOADER_TRANSLATION** (any) :

---
### <a id='model-webhookmessageform'></a>Object: WebhookMessageForm

- **content** (string) *(required)*:

---
### <a id='model-workspacepermissions'></a>Object: WorkspacePermissions

- **models** (boolean) :
- **knowledge** (boolean) :
- **prompts** (boolean) :
- **tools** (boolean) :
- **models_import** (boolean) :
- **models_export** (boolean) :
- **prompts_import** (boolean) :
- **prompts_export** (boolean) :
- **tools_import** (boolean) :
- **tools_export** (boolean) :

---
### <a id='model-open_webui__models__feedbacks__userresponse'></a>Object: open_webui__models__feedbacks__UserResponse

- **id** (string) *(required)*:
- **name** (string) *(required)*:
- **email** (string) *(required)*:
- **role** (string) :
- **last_active_at** (integer) *(required)*:
- **updated_at** (integer) *(required)*:
- **created_at** (integer) *(required)*:

---
### <a id='model-open_webui__routers__chats__messageform'></a>Object: open_webui__routers__chats__MessageForm

- **content** (string) *(required)*:

---
### <a id='model-open_webui__routers__ollama__ollamaconfigform'></a>Object: open_webui__routers__ollama__OllamaConfigForm

- **ENABLE_OLLAMA_API** (any) :
- **OLLAMA_BASE_URLS** (array) *(required)*:
- **OLLAMA_API_CONFIGS** (object) *(required)*:

---
### <a id='model-open_webui__routers__openai__connectionverificationform'></a>Object: open_webui__routers__openai__ConnectionVerificationForm

- **url** (string) *(required)*:
- **key** (string) *(required)*:
- **config** (any) :

---
### <a id='model-open_webui__routers__openai__openaiconfigform'></a>Object: open_webui__routers__openai__OpenAIConfigForm

- **ENABLE_OPENAI_API** (any) :
- **OPENAI_API_BASE_URLS** (array) *(required)*:
- **OPENAI_API_KEYS** (array) *(required)*:
- **OPENAI_API_CONFIGS** (object) *(required)*:

---
