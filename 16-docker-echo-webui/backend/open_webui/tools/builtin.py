"""
Built-in tools for Open WebUI (ECHO V6 Level-1 pruned subset).

# [ECHO-V6-HOOK] Pruned to ECHO-backed categories: chats, tasks, automations, calendar.
"""

import logging
import time
from typing import Literal, Optional

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from open_webui.models.chats import Chats, chat_search_content_query, chat_search_terms
from open_webui.models.config import Config
from open_webui.models.groups import Groups
from open_webui.utils.chat_id import is_saved_chat_id
from open_webui.utils.json_codec import JSONCodec

log = logging.getLogger(__name__)


# Chats tools


async def search_chats(
    query: str,
    count: int = 5,
    start_timestamp: Optional[int] = None,
    end_timestamp: Optional[int] = None,
    __request__: Request = None,
    __user__: dict = None,
    __chat_id__: str = None,
) -> str:
    """
    Search the user's previous chat conversations by title and message content,
    excluding the current chat. Helpful for finding details from earlier
    conversations when they are not already visible in the current context.
    Exact phrase matches are preferred, and descriptive keyword queries are
    supported.

    :param query: Exact phrase or descriptive keyword query to find matching previous chats
    :param count: Maximum number of results to return (default: 5)
    :param start_timestamp: Only include chats updated after this Unix timestamp (seconds)
    :param end_timestamp: Only include chats updated before this Unix timestamp (seconds)
    :return: JSON with matching chats containing id, title, updated_at, and content snippet
    """
    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        user_id = __user__.get('id')

        chats = await Chats.get_chats_by_user_id_and_search_text(
            user_id=user_id,
            search_text=query,
            include_archived=False,
            skip=0,
            limit=count * 3,  # Fetch more for filtering
        )

        matched = []
        for chat in chats:
            # Skip the current chat to avoid showing it in search results
            if __chat_id__ and chat.id == __chat_id__:
                continue

            # Apply date filters (updated_at is in seconds)
            if start_timestamp and chat.updated_at < start_timestamp:
                continue
            if end_timestamp and chat.updated_at > end_timestamp:
                continue

            matched.append(chat)
            if len(matched) >= count:
                break

        snippets = await Chats.get_chat_search_snippets([chat.id for chat in matched], user_id, query)
        lower_query = chat_search_content_query(query)
        needles = list(dict.fromkeys([lower_query, *chat_search_terms(lower_query)])) if lower_query else []
        results = []
        for chat in matched:
            snippet = snippets.get(chat.id) or ''
            title = chat.title or ''
            if not snippet and any(needle in title.lower() for needle in needles):
                snippet = f'Title match: {title}'

            results.append(
                {
                    'id': chat.id,
                    'title': chat.title,
                    'snippet': snippet,
                    'updated_at': chat.updated_at,
                }
            )

        return JSONCodec.dumps(results, ensure_ascii=False)
    except Exception as e:
        log.exception(f'search_chats error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def view_chat(
    chat_id: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Get the full conversation history of a chat by its ID after a relevant
    previous chat has been identified.

    :param chat_id: The ID of the chat to retrieve
    :return: JSON with the chat's id, title, and messages
    """
    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        user_id = __user__.get('id')

        chat = await Chats.get_chat_by_id_and_user_id(chat_id, user_id)

        if not chat:
            return JSONCodec.dumps({'error': 'Chat not found or access denied'})

        # Extract messages from history
        messages = []
        history = chat.chat.get('history', {})
        msg_dict = history.get('messages', {})

        # Follow parent links from currentId; conversations have family trees too.
        current_id = history.get('currentId')
        visited = set()

        while current_id and current_id not in visited:
            visited.add(current_id)
            msg = msg_dict.get(current_id)
            if msg:
                messages.append(
                    {
                        'role': msg.get('role', ''),
                        'content': msg.get('content', ''),
                    }
                )
            current_id = msg.get('parentId') if msg else None

        truncation = {}
        max_chars = await Config.get('chats.view_max_chars')
        if max_chars and sum(len(str(message['content'] or '')) for message in messages) > max_chars:
            remaining_chars = max_chars
            kept_messages = []
            for message in messages:
                if remaining_chars <= 0:
                    break
                content = str(message['content'] or '')[:remaining_chars]
                kept_messages.append({**message, 'content': content})
                remaining_chars -= len(content)
            truncation = {
                'truncated': True,
                'total_messages': len(messages),
                'returned_messages': len(kept_messages),
            }
            messages = kept_messages

        # Reverse to get chronological order
        messages.reverse()

        return JSONCodec.dumps(
            {
                'id': chat.id,
                'title': chat.title,
                'messages': messages,
                'updated_at': chat.updated_at,
                'created_at': chat.created_at,
                **truncation,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'view_chat error: {e}')
        return JSONCodec.dumps({'error': str(e)})


# Task management tools

VALID_TASK_STATUSES = {'pending', 'in_progress', 'completed', 'cancelled'}


class TaskItem(BaseModel):
    id: Optional[str] = Field(None, description='Unique identifier for the task. Auto-generated if omitted.')
    content: str = Field(..., description='Task description.')
    status: Literal['pending', 'in_progress', 'completed', 'cancelled'] = Field('pending', description='Task status.')


def _task_summary(all_tasks: list[dict]) -> dict:
    """Build summary counts for a task list."""
    pending = sum(1 for t in all_tasks if t['status'] == 'pending')
    in_progress = sum(1 for t in all_tasks if t['status'] == 'in_progress')
    completed = sum(1 for t in all_tasks if t['status'] == 'completed')
    cancelled = sum(1 for t in all_tasks if t['status'] == 'cancelled')
    return {
        'total': len(all_tasks),
        'pending': pending,
        'in_progress': in_progress,
        'completed': completed,
        'cancelled': cancelled,
    }


async def _emit_tasks(event_emitter, all_tasks: list[dict]):
    """Persist task state to the UI."""
    if event_emitter:
        await event_emitter(
            {
                'type': 'chat:message:tasks',
                'data': {
                    'tasks': all_tasks,
                },
            }
        )


async def create_tasks(
    tasks: list[TaskItem],
    __chat_id__: str = None,
    __message_id__: str = None,
    __event_emitter__: callable = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Create a visible task checklist for multi-step work so progress can be shown in chat.

    :param tasks: List of task items. Each item: content (string, required), status (pending|in_progress|completed|cancelled, default pending), id (optional, auto-generated).
    :return: JSON with the full task list and summary counts
    """
    if not is_saved_chat_id(__chat_id__):
        return JSONCodec.dumps({'error': 'Saved chat context not available'})

    try:
        all_tasks = []
        for idx, task in enumerate(tasks):
            if hasattr(task, 'model_dump'):
                d = task.model_dump(exclude_none=True)
            elif isinstance(task, dict):
                d = task
            else:
                d = dict(task)

            content = str(d.get('content', '')).strip()
            if not content:
                continue

            item_id = str(d.get('id', '') or '').strip() or str(idx + 1)
            status = str(d.get('status', 'pending')).strip().lower()
            if status not in VALID_TASK_STATUSES:
                status = 'pending'

            all_tasks.append({'id': item_id, 'content': content, 'status': status})

        await Chats.update_chat_tasks_by_id(__chat_id__, all_tasks)
        await _emit_tasks(__event_emitter__, all_tasks)

        return JSONCodec.dumps(
            {'tasks': all_tasks, 'summary': _task_summary(all_tasks)},
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'tasks error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def update_task(
    id: str,
    status: str = 'completed',
    __chat_id__: str = None,
    __message_id__: str = None,
    __event_emitter__: callable = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Mark a single visible task item as completed, in_progress, pending, or cancelled.

    :param id: The task ID to update
    :param status: New status: completed, in_progress, pending, or cancelled (default: completed)
    :return: JSON with the updated task list and summary counts
    """
    if not is_saved_chat_id(__chat_id__):
        return JSONCodec.dumps({'error': 'Saved chat context not available'})

    try:
        status = status.strip().lower()
        if status not in VALID_TASK_STATUSES:
            return JSONCodec.dumps(
                {'error': f'Invalid status: {status}. Must be one of: {", ".join(sorted(VALID_TASK_STATUSES))}'}
            )

        all_tasks = await Chats.get_chat_tasks_by_id(__chat_id__)

        found = False
        for task in all_tasks:
            if task['id'] == id:
                task['status'] = status
                found = True
                break

        if not found:
            return JSONCodec.dumps({'error': f'Task with id "{id}" not found'})

        await Chats.update_chat_tasks_by_id(__chat_id__, all_tasks)
        await _emit_tasks(__event_emitter__, all_tasks)

        return JSONCodec.dumps(
            {'tasks': all_tasks, 'summary': _task_summary(all_tasks)},
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'update_task_status error: {e}')
        return JSONCodec.dumps({'error': str(e)})


# Automation tools


async def _validate_owned_automation_folder(user_id: str, folder_id: Optional[str]) -> Optional[str]:
    if not folder_id:
        return None
    from open_webui.models.folders import Folders

    folder = await Folders.get_folder_by_id_and_user_id(folder_id, user_id)
    if not folder:
        raise ValueError('Folder not found')
    return folder.id


async def _validate_owned_automation_chat(user_id: str, chat_id: Optional[str]) -> str:
    cleaned_id = (chat_id or '').strip()
    if not cleaned_id:
        raise ValueError('Target chat ID is required')
    from open_webui.models.chats import Chats, is_internal_chat
    from open_webui.utils.chat_id import is_saved_chat_id

    if not is_saved_chat_id(cleaned_id):
        raise ValueError('Chat not found')
    chat = await Chats.get_chat_by_id_and_user_id(cleaned_id, user_id)
    if not chat or is_internal_chat(chat.meta):
        raise ValueError('Chat not found')
    return chat.id


async def create_automation(
    name: str,
    prompt: str,
    rrule: str,
    folder_id: Optional[str] = None,
    target_type: Optional[str] = None,
    target_chat_id: Optional[str] = None,
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
) -> str:
    """
    Create a scheduled automation that runs a prompt on a recurring or one-time schedule.
    Use this when the user wants to schedule a task to run automatically.
    The automation will use the current chat model.

    The rrule parameter must be a valid iCalendar RRULE string. Common examples:
    - Every day at 9am: "DTSTART:20250101T090000\nRRULE:FREQ=DAILY"
    - Every Monday at 8am: "DTSTART:20250106T080000\nRRULE:FREQ=WEEKLY;BYDAY=MO"
    - Every hour: "RRULE:FREQ=HOURLY;INTERVAL=1"
    - Every 30 minutes: "RRULE:FREQ=MINUTELY;INTERVAL=30"
    - Once at a specific time: "DTSTART:20250415T140000\nRRULE:FREQ=DAILY;COUNT=1"
    - First day of every month: "DTSTART:20250101T090000\nRRULE:FREQ=MONTHLY;BYMONTHDAY=1"

    The DTSTART time should reflect the desired execution time. Use COUNT=1 for one-time automations.

    :param name: A short descriptive name for the automation
    :param prompt: The prompt/instructions to execute on each run
    :param rrule: An iCalendar RRULE string defining the schedule
    :param folder_id: Optional owner-owned folder ID for generated chats
    :param target_type: Optional target mode ("chat", "existing_chat", "persistent_chat", or "channel")
    :param target_chat_id: Optional owner-owned saved chat ID for "existing_chat" or "persistent_chat"
    :return: JSON with the created automation details including id, next scheduled runs
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.automations import AutomationData, AutomationForm, Automations, AutomationTarget
        from open_webui.models.users import Users
        from open_webui.routers.automations import check_automation_limits
        from open_webui.utils.automations import next_n_runs_ns, next_run_ns, validate_rrule

        user_id = __user__.get('id')
        user = await Users.get_user_by_id(user_id)
        if not user:
            return JSONCodec.dumps({'error': 'User not found'})

        # Fall back to model dict ID since __metadata__ may predate model_id assignment
        metadata = __metadata__ or {}
        model_id = metadata.get('model_id') or (
            metadata.get('model', {}).get('id') if isinstance(metadata.get('model'), dict) else None
        )
        if not model_id:
            return JSONCodec.dumps({'error': 'Could not detect current model'})

        resolved_target_type = target_type.strip() if isinstance(target_type, str) and target_type.strip() else None
        cleaned_target_chat_id = (
            target_chat_id.strip() if isinstance(target_chat_id, str) and target_chat_id.strip() else None
        )
        if resolved_target_type is None and cleaned_target_chat_id:
            resolved_target_type = 'existing_chat'

        target_obj = None
        if resolved_target_type is None:
            if metadata.get('chat_id', '').startswith('channel:'):
                target_obj = AutomationTarget(
                    type='channel', channel_id=metadata.get('chat_id', '').removeprefix('channel:')
                )
        elif resolved_target_type == 'chat':
            target_obj = AutomationTarget(type='chat', chat_id=None)
        elif resolved_target_type == 'existing_chat':
            try:
                validated_chat_id = await _validate_owned_automation_chat(user_id, cleaned_target_chat_id)
            except ValueError as e:
                return JSONCodec.dumps({'error': str(e)})
            target_obj = AutomationTarget(type='existing_chat', chat_id=validated_chat_id)
            folder_id = None
        elif resolved_target_type == 'persistent_chat':
            if cleaned_target_chat_id:
                try:
                    validated_chat_id = await _validate_owned_automation_chat(user_id, cleaned_target_chat_id)
                except ValueError as e:
                    return JSONCodec.dumps({'error': str(e)})
                target_obj = AutomationTarget(type='persistent_chat', chat_id=validated_chat_id)
            else:
                target_obj = AutomationTarget(type='persistent_chat', chat_id=None)
        elif resolved_target_type == 'channel':
            if metadata.get('chat_id', '').startswith('channel:'):
                target_obj = AutomationTarget(
                    type='channel', channel_id=metadata.get('chat_id', '').removeprefix('channel:')
                )
            else:
                return JSONCodec.dumps({'error': 'Channel context not available'})
        else:
            return JSONCodec.dumps({'error': f'Invalid target_type: {resolved_target_type}'})

        try:
            folder_id = await _validate_owned_automation_folder(user_id, folder_id)
        except ValueError as e:
            return JSONCodec.dumps({'error': str(e)})

        # Validate the RRULE
        try:
            await validate_rrule(rrule, tz=user.timezone)
        except ValueError as e:
            return JSONCodec.dumps({'error': f'Invalid schedule: {e}'})

        try:
            await check_automation_limits(__request__, user, rrule, None, is_create=True)
        except HTTPException as e:
            return JSONCodec.dumps({'error': e.detail})

        tz = user.timezone
        form = AutomationForm(
            name=name,
            folder_id=folder_id,
            data=AutomationData(
                prompt=prompt,
                model_id=model_id,
                rrule=rrule,
                target=target_obj,
            ),
            is_active=True,
        )

        automation = await Automations.insert(user_id, form, await next_run_ns(rrule, tz=tz))

        return JSONCodec.dumps(
            {
                'status': 'success',
                'id': automation.id,
                'name': automation.name,
                'folder_id': automation.folder_id,
                'model_id': model_id,
                'target': automation.data.get('target'),
                'is_active': automation.is_active,
                'next_runs': await next_n_runs_ns(rrule, tz=tz),
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'create_automation error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def update_automation(
    automation_id: str,
    name: Optional[str] = None,
    prompt: Optional[str] = None,
    rrule: Optional[str] = None,
    model_id: Optional[str] = None,
    folder_id: Optional[str] = '',
    target_type: Optional[str] = None,
    target_chat_id: Optional[str] = '',
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Update an existing automation. Only the provided fields are changed; omitted fields stay the same.

    :param automation_id: The ID of the automation to update
    :param name: New name for the automation (optional)
    :param prompt: New prompt/instructions (optional)
    :param rrule: New iCalendar RRULE schedule string (optional). See create_automation for format examples.
    :param model_id: New model ID to use (optional); blank values are ignored
    :param folder_id: New owner-owned folder ID (optional); omit or pass blank to keep unchanged, pass null to clear
    :param target_type: New target mode ("chat", "existing_chat", "persistent_chat", or "channel") (optional)
    :param target_chat_id: New owner-owned chat ID (optional); omit or pass blank to keep unchanged, pass null to clear
    :return: JSON with the updated automation details
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.automations import AutomationData, AutomationForm, Automations, AutomationTarget
        from open_webui.models.users import Users
        from open_webui.routers.automations import check_automation_limits
        from open_webui.utils.automations import next_n_runs_ns, next_run_ns, validate_rrule

        user_id = __user__.get('id')
        user = await Users.get_user_by_id(user_id)
        if not user:
            return JSONCodec.dumps({'error': 'User not found'})

        automation = await Automations.get_by_id(automation_id)
        if not automation:
            return JSONCodec.dumps({'error': 'Automation not found'})
        if automation.user_id != user_id:
            return JSONCodec.dumps({'error': 'Access denied'})

        # Merge provided fields with existing values
        new_name = name if name is not None else automation.name
        new_prompt = prompt if prompt is not None else automation.data.get('prompt', '')
        new_model_id = model_id.strip() if model_id and model_id.strip() else automation.data.get('model_id', '')
        new_rrule = rrule if rrule is not None else automation.data.get('rrule', '')
        if folder_id is None:
            new_folder_id = None
        elif not folder_id.strip():
            new_folder_id = automation.folder_id
        else:
            try:
                new_folder_id = await _validate_owned_automation_folder(user_id, folder_id.strip())
            except ValueError as e:
                return JSONCodec.dumps({'error': str(e)})

        existing_target_dict = automation.data.get('target')
        if target_type is None and target_chat_id == '':
            new_target = AutomationTarget(**existing_target_dict) if existing_target_dict else None
        else:
            existing_target = dict(existing_target_dict or {})
            new_target_type = (
                target_type.strip()
                if isinstance(target_type, str) and target_type.strip()
                else (existing_target.get('type') or 'chat')
            )
            if target_chat_id is None:
                new_target_chat_id = None
            elif isinstance(target_chat_id, str) and not target_chat_id.strip():
                new_target_chat_id = existing_target.get('chat_id')
            else:
                new_target_chat_id = target_chat_id.strip()

            if new_target_type == 'chat':
                new_target = AutomationTarget(type='chat', chat_id=None)
            elif new_target_type == 'existing_chat':
                try:
                    validated_chat_id = await _validate_owned_automation_chat(user_id, new_target_chat_id)
                except ValueError as e:
                    return JSONCodec.dumps({'error': str(e)})
                new_target = AutomationTarget(type='existing_chat', chat_id=validated_chat_id)
                new_folder_id = None
            elif new_target_type == 'persistent_chat':
                if new_target_chat_id:
                    try:
                        validated_chat_id = await _validate_owned_automation_chat(user_id, new_target_chat_id)
                    except ValueError as e:
                        return JSONCodec.dumps({'error': str(e)})
                    new_target = AutomationTarget(type='persistent_chat', chat_id=validated_chat_id)
                else:
                    new_target = AutomationTarget(type='persistent_chat', chat_id=None)
            elif new_target_type == 'channel':
                channel_id = existing_target.get('channel_id')
                if not channel_id:
                    return JSONCodec.dumps({'error': 'Channel ID is missing for channel target'})
                new_target = AutomationTarget(type='channel', channel_id=channel_id)
            else:
                return JSONCodec.dumps({'error': f'Invalid target_type: {new_target_type}'})

        # Validate RRULE if changed
        if rrule is not None:
            try:
                await validate_rrule(new_rrule, tz=user.timezone)
            except ValueError as e:
                return JSONCodec.dumps({'error': f'Invalid schedule: {e}'})

        try:
            await check_automation_limits(__request__, user, new_rrule, None)
        except HTTPException as e:
            return JSONCodec.dumps({'error': e.detail})

        tz = user.timezone
        form = AutomationForm(
            name=new_name,
            folder_id=new_folder_id,
            data=AutomationData(
                prompt=new_prompt,
                model_id=new_model_id,
                rrule=new_rrule,
                target=new_target,
            ),
            is_active=automation.is_active,
        )

        updated = await Automations.update_by_id(automation_id, form, await next_run_ns(new_rrule, tz=tz))

        return JSONCodec.dumps(
            {
                'status': 'success',
                'id': updated.id,
                'name': updated.name,
                'folder_id': updated.folder_id,
                'model_id': new_model_id,
                'target': updated.data.get('target'),
                'is_active': updated.is_active,
                'next_runs': await next_n_runs_ns(new_rrule, tz=tz),
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'update_automation error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def list_automations(
    status: Optional[str] = None,
    folder_id: Optional[str] = None,
    count: int = 10,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    List the user's scheduled automations.

    :param status: Filter by status: "active", "paused", or omit for all
    :param folder_id: Optional owner-owned folder ID filter; pass an empty string to clear the folder filter
    :param count: Maximum number of automations to return (default: 10)
    :return: JSON list of automations with id, name, prompt snippet, schedule, status, and next runs
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.automations import Automations
        from open_webui.models.users import Users
        from open_webui.utils.automations import next_n_runs_ns

        user_id = __user__.get('id')
        user = await Users.get_user_by_id(user_id)
        if folder_id:
            try:
                folder_id = await _validate_owned_automation_folder(user_id, folder_id)
            except ValueError as e:
                return JSONCodec.dumps({'error': str(e)})

        result = await Automations.search_automations(
            user_id=user_id,
            status=status,
            folder_id=folder_id,
            skip=0,
            limit=count,
        )

        automations = []
        for item in result.items:
            rrule = item.data.get('rrule', '')
            prompt_text = item.data.get('prompt', '')
            snippet = prompt_text[:100] + ('...' if len(prompt_text) > 100 else '')

            automations.append(
                {
                    'id': item.id,
                    'name': item.name,
                    'folder_id': item.folder_id,
                    'prompt_snippet': snippet,
                    'model_id': item.data.get('model_id', ''),
                    'target': item.data.get('target'),
                    'rrule': rrule,
                    'is_active': item.is_active,
                    'last_run_at': item.last_run_at,
                    'next_runs': await next_n_runs_ns(rrule, tz=user.timezone if user else None),
                }
            )

        return JSONCodec.dumps(
            {'automations': automations, 'total': result.total},
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'list_automations error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def toggle_automation(
    automation_id: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Pause or resume a scheduled automation. If active, it will be paused. If paused, it will be resumed.

    :param automation_id: The ID of the automation to toggle
    :return: JSON with the updated automation status
    """
    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.automations import Automations
        from open_webui.models.users import Users
        from open_webui.utils.automations import next_run_ns

        user_id = __user__.get('id')
        user = await Users.get_user_by_id(user_id)

        automation = await Automations.get_by_id(automation_id)
        if not automation:
            return JSONCodec.dumps({'error': 'Automation not found'})
        if automation.user_id != user_id:
            return JSONCodec.dumps({'error': 'Access denied'})

        rrule = automation.data.get('rrule', '')
        toggled = await Automations.toggle(
            automation_id,
            await next_run_ns(rrule, tz=user.timezone if user else None),
        )

        return JSONCodec.dumps(
            {
                'status': 'success',
                'id': toggled.id,
                'name': toggled.name,
                'is_active': toggled.is_active,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'toggle_automation error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def delete_automation(
    automation_id: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Delete a scheduled automation and all its run history.

    :param automation_id: The ID of the automation to delete
    :return: JSON confirming the automation was deleted
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.automations import AutomationRuns, Automations

        user_id = __user__.get('id')

        automation = await Automations.get_by_id(automation_id)
        if not automation:
            return JSONCodec.dumps({'error': 'Automation not found'})
        if automation.user_id != user_id:
            return JSONCodec.dumps({'error': 'Access denied'})

        name = automation.name
        await AutomationRuns.delete_by_automation(automation_id)
        await Automations.delete(automation_id)

        return JSONCodec.dumps(
            {
                'status': 'success',
                'message': f'Automation "{name}" deleted',
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'delete_automation error: {e}')
        return JSONCodec.dumps({'error': str(e)})


# Calendar tools


MAX_CALENDAR_RANGE_END_NS = 2**63 - 1


def _get_user_tz(user_dict: dict):
    """Get the user's timezone as a ZoneInfo, falling back to UTC."""
    from zoneinfo import ZoneInfo

    tz_name = None
    if user_dict:
        tz_name = user_dict.get('timezone')
    if tz_name:
        try:
            return ZoneInfo(tz_name)
        except Exception:
            pass
    return ZoneInfo('UTC')


def _dt_to_ns(dt_str: str, tz) -> int:
    """Convert a datetime string to nanoseconds since epoch, interpreting in the given timezone."""
    from datetime import datetime

    dt = datetime.fromisoformat(dt_str)
    # If naive (no timezone info), localize to user's timezone
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return int(dt.timestamp() * 1_000) * 1_000_000


def _ns_to_dt(ns: int, tz) -> str:
    """Convert nanoseconds since epoch to a datetime string in the given timezone."""
    from datetime import datetime

    seconds = ns / 1_000_000_000
    dt = datetime.fromtimestamp(seconds, tz=tz)
    return dt.strftime('%Y-%m-%d %H:%M')


def _event_to_dict(event, tz) -> dict:
    """Convert a calendar event model to a human-friendly dict with local timestamps."""
    alert_minutes = None
    if event.meta and 'alert_minutes' in event.meta:
        alert_minutes = event.meta['alert_minutes']
    return {
        'id': event.id,
        'calendar_id': event.calendar_id,
        'title': event.title,
        'description': event.description or '',
        'start': _ns_to_dt(event.start_at, tz),
        'end': _ns_to_dt(event.end_at, tz) if event.end_at else None,
        'all_day': event.all_day,
        'location': event.location or '',
        'reminder_minutes': alert_minutes if alert_minutes is not None else 10,
        'color': event.color,
        'is_cancelled': event.is_cancelled,
    }


async def search_calendar_events(
    query: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    count: int = 10,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Search calendar events, reminders, and scheduled items by text and/or date range.
    Helpful for finding upcoming events, reminders, or schedule items.

    :param query: Search text to match against event title, description, or location (optional)
    :param start: Only return events starting at or after this datetime, e.g. "2026-04-20 00:00" (optional)
    :param end: Only return events starting before this datetime, e.g. "2026-04-27 00:00" (optional)
    :param count: Maximum number of events to return (default: 10)
    :return: JSON list of matching events with id, title, description, start, end, calendar_id, location
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.calendar import CalendarEvents

        user_id = __user__.get('id')
        tz = _get_user_tz(__user__)

        if isinstance(count, str):
            try:
                count = int(count)
            except ValueError:
                count = 10

        if start or end:
            # Date range query — use get_events_by_range
            try:
                start_ns = _dt_to_ns(start, tz) if start else 0
            except (ValueError, TypeError) as e:
                return JSONCodec.dumps({'error': f'Invalid start datetime: {e}'})

            try:
                end_ns = _dt_to_ns(end, tz) if end else MAX_CALENDAR_RANGE_END_NS
            except (ValueError, TypeError) as e:
                return JSONCodec.dumps({'error': f'Invalid end datetime: {e}'})

            items = await CalendarEvents.get_events_by_range(
                user_id=user_id,
                start=start_ns,
                end=end_ns,
            )

            # Apply text filter if query is also provided
            if query:
                q = query.lower()
                items = [
                    e
                    for e in items
                    if q in (e.title or '').lower()
                    or q in (e.description or '').lower()
                    or q in (e.location or '').lower()
                ]

            events = [_event_to_dict(item, tz) for item in items[:count]]
            return JSONCodec.dumps(
                {'events': events, 'total': len(items)},
                ensure_ascii=False,
            )
        else:
            # Text-only search
            result = await CalendarEvents.search_events(
                user_id=user_id,
                query=query,
                skip=0,
                limit=count,
            )

            events = [_event_to_dict(item, tz) for item in result.items]
            return JSONCodec.dumps(
                {'events': events, 'total': result.total},
                ensure_ascii=False,
            )
    except Exception as e:
        log.exception(f'search_calendar_events error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def create_calendar_event(
    title: str,
    start: str,
    end: Optional[str] = None,
    description: Optional[str] = None,
    calendar_id: Optional[str] = None,
    all_day: bool = False,
    location: Optional[str] = None,
    reminder_minutes: Optional[int] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Create a calendar event, reminder, or alarm. Use this when the user wants to
    schedule an event, set a reminder, create an alarm, or says things like
    "remind me", "don't let me forget", "notify me at", or "add to my calendar".
    For simple reminders, omit end/location/all_day and set reminder_minutes to 0.

    :param title: Event or reminder title (e.g. "Team standup", "Take medicine", "Call mom")
    :param start: Start datetime in the user's local time (e.g. "2026-04-20 09:00")
    :param end: End datetime in the user's local time (optional — omit for reminders or point-in-time events)
    :param description: Event description or notes (optional)
    :param calendar_id: Target calendar ID (optional, uses default calendar if omitted)
    :param all_day: Whether this is an all-day event (default: false)
    :param location: Event location (optional)
    :param reminder_minutes: Minutes before the event to send a notification (optional, default: 10). Use 0 for "at time of event", -1 for no notification.
    :return: JSON with the created event details including id
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.calendar import CalendarEventForm, CalendarEvents, Calendars

        user_id = __user__.get('id')

        # Resolve calendar_id: use provided, or fall back to default
        if not calendar_id:
            calendars = await Calendars.get_calendars_by_user(user_id)
            default_cal = next((c for c in calendars if c.is_default), None)
            if not default_cal and calendars:
                default_cal = calendars[0]
            if not default_cal:
                return JSONCodec.dumps({'error': 'No calendars found. Cannot create event.'})
            calendar_id = default_cal.id

        # Verify access
        cal = await Calendars.get_calendar_by_id(calendar_id)
        if not cal:
            return JSONCodec.dumps({'error': 'Calendar not found'})
        if cal.user_id != user_id and __user__.get('role') != 'admin':
            from open_webui.models.access_grants import AccessGrants
            from open_webui.models.groups import Groups

            user_group_ids = [g.id for g in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]
            if not await AccessGrants.has_access(
                user_id=user_id,
                resource_type='calendar',
                resource_id=cal.id,
                permission='write',
                user_group_ids=set(user_group_ids),
            ):
                return JSONCodec.dumps({'error': 'Access denied to this calendar'})

        # Coerce boolean from LLM
        if isinstance(all_day, str):
            all_day = all_day.lower() in ('true', '1', 'yes')

        # Convert datetime strings to nanoseconds using user's timezone
        tz = _get_user_tz(__user__)
        try:
            start_ns = _dt_to_ns(start, tz)
        except (ValueError, TypeError) as e:
            return JSONCodec.dumps({'error': f'Invalid start datetime: {e}. Use format like "2026-04-20 09:00"'})

        end_ns = None
        if end:
            try:
                end_ns = _dt_to_ns(end, tz)
            except (ValueError, TypeError) as e:
                return JSONCodec.dumps({'error': f'Invalid end datetime: {e}. Use format like "2026-04-20 10:00"'})
        elif not all_day:
            # Default to 1 hour duration
            end_ns = start_ns + 3_600_000_000_000

        # Build meta with reminder setting
        meta = {}
        if reminder_minutes is not None:
            if isinstance(reminder_minutes, str):
                try:
                    reminder_minutes = int(reminder_minutes)
                except ValueError:
                    reminder_minutes = 10
            meta['alert_minutes'] = reminder_minutes
        else:
            meta['alert_minutes'] = 10

        form = CalendarEventForm(
            calendar_id=calendar_id,
            title=title,
            description=description,
            start_at=start_ns,
            end_at=end_ns,
            all_day=all_day,
            location=location,
            meta=meta,
        )

        event = await CalendarEvents.insert_new_event(user_id, form)
        if not event:
            return JSONCodec.dumps({'error': 'Failed to create event'})

        return JSONCodec.dumps(
            {
                'status': 'success',
                **_event_to_dict(event, tz),
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'create_calendar_event error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def update_calendar_event(
    event_id: str,
    title: Optional[str] = None,
    description: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    all_day: Optional[bool] = None,
    location: Optional[str] = None,
    is_cancelled: Optional[bool] = None,
    reminder_minutes: Optional[int] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Update an existing calendar event. Only provided fields are changed;
    omitted fields stay the same.

    :param event_id: The ID of the event to update
    :param title: New event title (optional)
    :param description: New event description (optional)
    :param start: New start datetime string in your local time, e.g. "2026-04-20 09:00" (optional)
    :param end: New end datetime string in your local time (optional)
    :param all_day: Whether this is an all-day event (optional)
    :param location: New event location (optional)
    :param is_cancelled: Set to true to cancel the event (optional)
    :param reminder_minutes: Minutes before the event to send a reminder notification (optional). Use 0 for "at time of event", -1 for no reminder. Accepts any positive integer for custom timing (e.g. 120 for 2 hours before).
    :return: JSON with the updated event details
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.access_grants import AccessGrants
        from open_webui.models.calendar import CalendarEvents, CalendarEventUpdateForm, Calendars
        from open_webui.models.groups import Groups

        user_id = __user__.get('id')

        event = await CalendarEvents.get_event_by_id(event_id)
        if not event:
            return JSONCodec.dumps({'error': 'Event not found'})

        # Check write access to the event's calendar
        cal = await Calendars.get_calendar_by_id(event.calendar_id)
        if not cal:
            return JSONCodec.dumps({'error': 'Access denied'})
        if cal.user_id != user_id and __user__.get('role') != 'admin':
            user_group_ids = [g.id for g in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]
            if not await AccessGrants.has_access(
                user_id=user_id,
                resource_type='calendar',
                resource_id=cal.id,
                permission='write',
                user_group_ids=set(user_group_ids),
            ):
                return JSONCodec.dumps({'error': 'Access denied'})

        # Coerce boolean strings from LLM
        if isinstance(all_day, str):
            all_day = all_day.lower() in ('true', '1', 'yes')
        if isinstance(is_cancelled, str):
            is_cancelled = is_cancelled.lower() in ('true', '1', 'yes')

        # Convert datetime strings to nanoseconds using user's timezone
        tz = _get_user_tz(__user__)
        start_ns = None
        if start is not None:
            try:
                start_ns = _dt_to_ns(start, tz)
            except (ValueError, TypeError) as e:
                return JSONCodec.dumps({'error': f'Invalid start datetime: {e}'})

        end_ns = None
        if end is not None:
            try:
                end_ns = _dt_to_ns(end, tz)
            except (ValueError, TypeError) as e:
                return JSONCodec.dumps({'error': f'Invalid end datetime: {e}'})

        # Build meta update with reminder setting if provided
        meta = None
        if reminder_minutes is not None:
            if isinstance(reminder_minutes, str):
                try:
                    reminder_minutes = int(reminder_minutes)
                except ValueError:
                    reminder_minutes = None
            if reminder_minutes is not None:
                meta = {'alert_minutes': reminder_minutes}

        update_fields = {
            'title': title,
            'description': description,
            'start_at': start_ns,
            'end_at': end_ns,
            'all_day': all_day,
            'location': location,
            'is_cancelled': is_cancelled,
            'meta': meta,
        }
        form = CalendarEventUpdateForm(**{k: v for k, v in update_fields.items() if v is not None})

        updated = await CalendarEvents.update_event_by_id(event_id, form)
        if not updated:
            return JSONCodec.dumps({'error': 'Failed to update event'})

        return JSONCodec.dumps(
            {
                'status': 'success',
                **_event_to_dict(updated, tz),
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'update_calendar_event error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def delete_calendar_event(
    event_id: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Delete a calendar event permanently.

    :param event_id: The ID of the event to delete
    :return: JSON confirming the event was deleted
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.access_grants import AccessGrants
        from open_webui.models.calendar import CalendarEvents, Calendars
        from open_webui.models.groups import Groups

        user_id = __user__.get('id')

        event = await CalendarEvents.get_event_by_id(event_id)
        if not event:
            return JSONCodec.dumps({'error': 'Event not found'})

        # Check write access
        cal = await Calendars.get_calendar_by_id(event.calendar_id)
        if not cal:
            return JSONCodec.dumps({'error': 'Access denied'})
        if cal.user_id != user_id and __user__.get('role') != 'admin':
            user_group_ids = [g.id for g in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]
            if not await AccessGrants.has_access(
                user_id=user_id,
                resource_type='calendar',
                resource_id=cal.id,
                permission='write',
                user_group_ids=set(user_group_ids),
            ):
                return JSONCodec.dumps({'error': 'Access denied'})

        title = event.title
        result = await CalendarEvents.delete_event_by_id(event_id)
        if not result:
            return JSONCodec.dumps({'error': 'Failed to delete event'})

        return JSONCodec.dumps(
            {
                'status': 'success',
                'message': f'Event "{title}" deleted',
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'delete_calendar_event error: {e}')
        return JSONCodec.dumps({'error': str(e)})
