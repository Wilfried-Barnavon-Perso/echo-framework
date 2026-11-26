import { describe, expect, it } from 'vitest';
import {
	appendChildToWindowedHistory,
	convertMessagesToHistory,
	createMessageSyncTracker,
	getBranchSiblingIds,
	getDeepestChildId,
	getDeepestTreeChildId,
	sanitizeHistory,
	syncClonedMessage
} from './index';
import {
	buildOutputDisplayItems,
	normalizeOutputParts
} from '$lib/components/chat/Messages/structuredOutput';

describe('sanitizeHistory with treeIndex (windowed chats)', () => {
	it('preserves childrenIds referencing unloaded siblings in treeIndex and prunes deleted ones', () => {
		const history = {
			currentId: 'm3',
			messages: {
				m2: {
					id: 'm2',
					parentId: 'm1',
					childrenIds: ['m3', 'm3_alt', 'deleted_node'],
					role: 'user',
					content: 'Question'
				},
				m3: {
					id: 'm3',
					parentId: 'm2',
					childrenIds: [],
					role: 'assistant',
					content: 'Branch 1'
				}
			}
		};

		const treeIndex = {
			m1: { parentId: null, childrenIds: ['m2'], role: 'assistant', timestamp: 100 },
			m2: { parentId: 'm1', childrenIds: ['m3', 'm3_alt'], role: 'user', timestamp: 101 },
			m3: { parentId: 'm2', childrenIds: [], role: 'assistant', timestamp: 102 },
			m3_alt: { parentId: 'm2', childrenIds: [], role: 'assistant', timestamp: 103 }
		};

		sanitizeHistory(history, treeIndex);

		// m3_alt is outside the loaded window (in treeIndex only) -> must be preserved for < 1/2 >
		// deleted_node is in neither history.messages nor treeIndex -> must be pruned
		expect(history.messages.m2.childrenIds).toEqual(['m3', 'm3_alt']);
		// m2.parentId points to m1 (in treeIndex only) -> must NOT be overwritten
		expect(history.messages.m2.parentId).toBe('m1');
	});

	it('reconstructs missing parentId and role from treeIndex for window boundary nodes', () => {
		const history: any = {
			currentId: 'm3',
			messages: {
				m2: {
					id: 'm2',
					childrenIds: ['m3'],
					content: 'Missing parentId & role'
				},
				m3: {
					id: 'm3',
					parentId: 'm2',
					childrenIds: [],
					role: 'assistant',
					content: 'Reply'
				}
			}
		};

		const treeIndex = {
			m1: { parentId: null, childrenIds: ['m2'], role: 'assistant', timestamp: 10 },
			m2: { parentId: 'm1', childrenIds: ['m3'], role: 'user', timestamp: 20 },
			m3: { parentId: 'm2', childrenIds: [], role: 'assistant', timestamp: 30 }
		};

		sanitizeHistory(history, treeIndex);

		expect(history.messages.m2.parentId).toBe('m1');
		expect(history.messages.m2.role).toBe('user');
	});

	it('resolves branch siblings including unloaded treeIndex roots and children', () => {
		const history = {
			currentId: 'm3',
			messages: {
				m2: {
					id: 'm2',
					parentId: null,
					childrenIds: ['m3'],
					role: 'user',
					timestamp: 200
				},
				m3: {
					id: 'm3',
					parentId: 'm2',
					childrenIds: [],
					role: 'assistant',
					timestamp: 210
				}
			}
		};

		const treeIndex = {
			m1: { parentId: null, childrenIds: [], role: 'user', timestamp: 100 },
			m2: { parentId: null, childrenIds: ['m3', 'm3_b'], role: 'user', timestamp: 200 },
			m3: { parentId: 'm2', childrenIds: [], role: 'assistant', timestamp: 210 },
			m3_b: { parentId: 'm2', childrenIds: [], role: 'assistant', timestamp: 220 }
		};

		expect(getBranchSiblingIds(history, treeIndex, history.messages.m2)).toEqual(['m1', 'm2']);
		expect(getBranchSiblingIds(history, treeIndex, history.messages.m3)).toEqual(['m3', 'm3_b']);
	});
});

describe('syncClonedMessage & createMessageSyncTracker', () => {
	it('is immediately idempotent on arrays (models, output, files) without re-cloning loops', () => {
		const source = {
			id: 'msg-1',
			role: 'assistant',
			content: 'Hello',
			done: true,
			models: ['echo_orchestrator'],
			files: [{ type: 'file', url: 'f1' }],
			output: [{ type: 'message', content: [{ type: 'output_text', text: 'Hello' }] }]
		};

		const tracker = createMessageSyncTracker(source);
		expect(syncClonedMessage(source, tracker)).toBeNull();
		expect(syncClonedMessage(source, tracker)).toBeNull();

		// O(1) field mutation triggers exactly one clone, then returns to idempotence
		source.content = 'Hello world';
		const updated = syncClonedMessage(source, tracker);
		expect(updated).not.toBeNull();
		expect(updated?.content).toBe('Hello world');
		expect(updated).not.toBe(source);
		expect(syncClonedMessage(source, tracker)).toBeNull();
	});

	it('detects nested in-place mutations while streaming (done: false) and clears streamingClone once done', () => {
		const source: any = {
			id: 'msg-stream',
			role: 'assistant',
			content: '',
			done: false,
			output: [{ type: 'message', content: [{ type: 'output_text', text: 'Chunk 1' }] }]
		};

		const tracker = createMessageSyncTracker(source);
		expect(syncClonedMessage(source, tracker)).toBeNull();

		// Mutate nested output text in-place without changing top-level references or lengths
		source.output[0].content[0].text = 'Chunk 1 + Chunk 2';
		const streamed = syncClonedMessage(source, tracker);
		expect(streamed).not.toBeNull();
		expect(streamed?.output[0].content[0].text).toBe('Chunk 1 + Chunk 2');
		expect(syncClonedMessage(source, tracker)).toBeNull();

		// Flip done -> true: releases streamingClone
		source.done = true;
		const finished = syncClonedMessage(source, tracker);
		expect(finished?.done).toBe(true);
		expect(tracker.streamingClone).toBeNull();
		expect(syncClonedMessage(source, tracker)).toBeNull();
	});
});

describe('normalizeOutputParts & buildOutputDisplayItems with lazy placeholders', () => {
	it('normalizes string lazy placeholders and renders tool/reasoning items without crashing', () => {
		expect(normalizeOutputParts('<!-- echo-lazy -->', 'output_text')).toEqual([
			{ type: 'output_text', text: '<!-- echo-lazy -->' }
		]);
		expect(normalizeOutputParts('', 'output_text')).toEqual([]);
		expect(normalizeOutputParts(null)).toEqual([]);

		const lazyOutput: any[] = [
			{
				type: 'reasoning',
				id: 'r1',
				status: 'completed',
				summary: '<!-- echo-lazy -->',
				content: '<!-- echo-lazy -->'
			},
			{
				type: 'function_call',
				id: 'fc1',
				call_id: 'call-1',
				name: 'delegate_task',
				status: 'completed',
				arguments: '<!-- echo-lazy -->'
			},
			{
				type: 'function_call_output',
				id: 'fco1',
				call_id: 'call-1',
				output: '<!-- echo-lazy -->'
			},
			{
				type: 'message',
				id: 'm1',
				content: [{ type: 'output_text', text: 'Synthèse finale France Travail' }]
			}
		];

		const displayItems = buildOutputDisplayItems(lazyOutput);
		expect(displayItems).toHaveLength(2);
		expect(displayItems[0].type).toBe('detail_group');
		if (displayItems[0].type === 'detail_group') {
			expect(displayItems[0].tokens).toHaveLength(2);
			expect(displayItems[0].tokens[1].text).toBe('<!-- echo-lazy -->');
		}
		expect(displayItems[1]).toEqual({
			type: 'message',
			id: 'm1',
			text: 'Synthèse finale France Travail'
		});
	});
});

describe('windowed tree navigation & mutation helpers', () => {
	it('keeps getDeepestChildId inside loaded history.messages while getDeepestTreeChildId reaches unloaded leaves', () => {
		const history = {
			currentId: 'm2',
			messages: {
				m1: { id: 'm1', parentId: null, childrenIds: ['m2', 'm2_unloaded'], role: 'user' },
				m2: { id: 'm2', parentId: 'm1', childrenIds: [], role: 'assistant' }
			}
		};
		const treeIndex = {
			m1: { parentId: null, childrenIds: ['m2', 'm2_unloaded'], role: 'user', timestamp: 1 },
			m2: { parentId: 'm1', childrenIds: [], role: 'assistant', timestamp: 2 },
			m2_unloaded: {
				parentId: 'm1',
				childrenIds: ['m3_unloaded_leaf'],
				role: 'assistant',
				timestamp: 3
			},
			m3_unloaded_leaf: { parentId: 'm2_unloaded', childrenIds: [], role: 'user', timestamp: 4 }
		};

		// Starting from m1 (present in history.messages), getDeepestChildId must NOT return unloaded m2_unloaded
		expect(getDeepestChildId(history, 'm1')).toBe('m2');
		// getDeepestTreeChildId traverses treeIndex down to the true unloaded leaf
		expect(getDeepestTreeChildId(history, treeIndex, 'm2_unloaded')).toBe('m3_unloaded_leaf');
		expect(getDeepestTreeChildId(history, treeIndex, 'm1')).toBe('m3_unloaded_leaf');
	});

	it('safely appends child IDs when parent is outside the loaded window (in treeIndex only)', () => {
		const history: any = {
			currentId: 'm2',
			messages: {
				m2: { id: 'm2', parentId: 'm1_outside', childrenIds: [], role: 'user' }
			}
		};
		const treeIndex: Record<string, any> = {
			m1_outside: { parentId: null, childrenIds: ['m2'], role: 'assistant', timestamp: 10 },
			m2: { parentId: 'm1_outside', childrenIds: [], role: 'user', timestamp: 20 }
		};

		// Must not throw when history.messages['m1_outside'] is undefined, and must update treeIndex without duplicates
		appendChildToWindowedHistory(history, treeIndex, 'm1_outside', 'm2_edited');
		appendChildToWindowedHistory(history, treeIndex, 'm1_outside', 'm2_edited');
		expect(treeIndex.m1_outside.childrenIds).toEqual(['m2', 'm2_edited']);

		// Also updates both when parent is loaded
		appendChildToWindowedHistory(history, treeIndex, 'm2', 'm3_reply');
		expect(history.messages.m2.childrenIds).toEqual(['m3_reply']);
		expect(treeIndex.m2.childrenIds).toEqual(['m3_reply']);

		// convertMessagesToHistory tolerates undefined/non-array safely
		expect(convertMessagesToHistory(undefined as any)).toEqual({ messages: {}, currentId: null });
	});
});
