<!-- Copyright (c) 2025-2026 Wilfried BARNAVON | SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import { basicSetup, EditorView } from "codemirror";
  import { keymap, GutterMarker, gutterLineClass } from "@codemirror/view";
  import {
    Compartment,
    EditorState,
    RangeSet,
    StateEffect,
    StateField,
    type Range,
    type Text,
  } from "@codemirror/state";
  import { Chunk } from "@codemirror/merge";
  import { indentWithTab } from "@codemirror/commands";
  import { acceptCompletion } from "@codemirror/autocomplete";
  import { oneDark } from "@codemirror/theme-one-dark";
  import { loadCodeMirrorLanguage } from "./codexLanguages";

  export let value = "";
  export let filePath = "";
  export let language = "plaintext";
  export let readOnly = false;
  export let wordWrap = true;
  export let gitBaseContent: string | null = null;
  export let ariaLabel = "Éditeur Codex";
  export let onSave: (() => void) | null = null;
  export let onSelectionChange: ((selectedText: string) => void) | null = null;
  export let onCursorChange:
    | ((info: {
        line: number;
        col: number;
        lineCount: number;
        selectionChars: number;
        selectionLines: number;
      }) => void)
    | null = null;

  class GitLineMarker extends GutterMarker {
    readonly elementClass: string;
    constructor(elementClass: string) {
      super();
      this.elementClass = elementClass;
    }
  }

  const addedMarker = new GitLineMarker("cm-git-line-added");
  const modifiedMarker = new GitLineMarker("cm-git-line-modified");
  const diffConfig = { timeout: 50 };
  const setGitBase = StateEffect.define<string | null>();

  function computeGitMarkers(base: Text, doc: Text, chunks: readonly Chunk[]) {
    const ranges: Range<GutterMarker>[] = [];
    for (const chunk of chunks) {
      if (chunk.fromB === chunk.toB) continue;
      const removed =
        chunk.fromA === chunk.toA || !base.length
          ? 0
          : base.lineAt(chunk.endA).number -
            base.lineAt(chunk.fromA).number +
            1 -
            Number(base.lineAt(chunk.endA).from === base.length);
      const start = doc.lineAt(chunk.fromB).number;
      const end = doc.lineAt(chunk.endB).number;
      for (let number = start; number <= end; number++) {
        const line = doc.line(number);
        if (line.from === doc.length && !line.length) continue;
        ranges.push(
          (number - start < removed ? modifiedMarker : addedMarker).range(
            line.from,
          ),
        );
      }
    }
    return RangeSet.of(ranges);
  }

  const gitLineChanges = StateField.define<{
    base: Text | null;
    chunks: readonly Chunk[];
    markers: RangeSet<GutterMarker>;
  }>({
    create: () => ({ base: null, chunks: [], markers: RangeSet.empty }),
    update(stateVal, transaction) {
      const effect = transaction.effects.find((eff) => eff.is(setGitBase));
      if (!effect && (!transaction.docChanged || stateVal.base === null))
        return stateVal;
      const base = effect
        ? effect.value === null
          ? null
          : EditorState.create({ doc: effect.value }).doc
        : stateVal.base;
      if (base === null) return { base, chunks: [], markers: RangeSet.empty };
      const doc = transaction.state.doc;
      const chunks = effect
        ? Chunk.build(base, doc, diffConfig)
        : Chunk.updateB(
            stateVal.chunks,
            base,
            doc,
            transaction.changes,
            diffConfig,
          );
      return { base, chunks, markers: computeGitMarkers(base, doc, chunks) };
    },
    provide: (field) => gutterLineClass.from(field, (val) => val.markers),
  });

  let container: HTMLDivElement;
  let editor: EditorView | null = null;
  const editorTheme = new Compartment();
  const editorLanguage = new Compartment();
  const editorReadOnly = new Compartment();
  const editorWrap = new Compartment();
  let internalValue = "";
  let languageGeneration = 0;

  export const getSelection = (): string => {
    if (!editor || editor.state.selection.main.empty) return "";
    const { from, to } = editor.state.selection.main;
    return editor.state.sliceDoc(from, to);
  };

  export const getValue = (): string => {
    return editor?.state.doc.toString() ?? value;
  };

  export const focus = (): void => {
    editor?.focus();
  };

  const emitCursorAndSelection = (state: EditorState) => {
    const main = state.selection.main;
    const headLine = state.doc.lineAt(main.head);
    const selectedText = main.empty ? "" : state.sliceDoc(main.from, main.to);
    const selectionLines = main.empty
      ? 0
      : state.doc.lineAt(main.to).number -
        state.doc.lineAt(main.from).number +
        1;

    onSelectionChange?.(selectedText);
    onCursorChange?.({
      line: headLine.number,
      col: main.head - headLine.from + 1,
      lineCount: state.doc.lines,
      selectionChars: selectedText.length,
      selectionLines,
    });
  };

  const applyLanguage = async (langId: string, path: string) => {
    const version = ++languageGeneration;
    editor?.dispatch({ effects: editorLanguage.reconfigure([]) });
    const langSupport = await loadCodeMirrorLanguage(langId, path);
    if (version === languageGeneration && editor && langSupport) {
      editor.dispatch({ effects: editorLanguage.reconfigure(langSupport) });
    }
  };

  $: if (editor) {
    editor.dispatch({
      effects: editorReadOnly.reconfigure([
        EditorState.readOnly.of(readOnly),
        EditorView.editable.of(!readOnly),
        EditorView.contentAttributes.of(
          ariaLabel ? { "aria-label": ariaLabel } : {},
        ),
      ]),
    });
  }

  $: if (editor) {
    editor.dispatch({
      effects: editorWrap.reconfigure(wordWrap ? EditorView.lineWrapping : []),
    });
  }

  $: if (editor && value !== internalValue) {
    internalValue = value;
    editor.dispatch({
      changes: { from: 0, to: editor.state.doc.length, insert: value },
    });
  }

  $: if (editor) {
    void applyLanguage(language, filePath);
  }

  $: if (editor) {
    editor.dispatch({ effects: setGitBase.of(gitBaseContent) });
  }

  onMount(() => {
    const isDark = document.documentElement.classList.contains("dark");
    internalValue = value;

    const extensions = [
      basicSetup,
      gitLineChanges,
      keymap.of([
        { key: "Tab", run: acceptCompletion },
        indentWithTab,
        {
          key: "Mod-s",
          run: () => {
            if (onSave && !readOnly) {
              onSave();
            }
            return true;
          },
        },
      ]),
      EditorView.updateListener.of((update) => {
        if (update.docChanged) {
          internalValue = update.state.doc.toString();
          value = internalValue;
        }
        if (update.docChanged || update.selectionSet) {
          emitCursorAndSelection(update.state);
        }
      }),
      editorTheme.of(isDark ? oneDark : []),
      editorLanguage.of([]),
      editorWrap.of(wordWrap ? EditorView.lineWrapping : []),
      editorReadOnly.of([
        EditorState.readOnly.of(readOnly),
        EditorView.editable.of(!readOnly),
        EditorView.contentAttributes.of(
          ariaLabel ? { "aria-label": ariaLabel } : {},
        ),
      ]),
      EditorView.theme({
        "&": { fontSize: "0.8125rem", height: "100%" },
        ".cm-content": { padding: "0.5rem 0" },
        "&.cm-focused": { outline: "none" },
      }),
    ];

    editor = new EditorView({
      state: EditorState.create({ doc: value, extensions }),
      parent: container,
    });

    emitCursorAndSelection(editor.state);
    void applyLanguage(language, filePath);
    if (gitBaseContent !== null) {
      editor.dispatch({ effects: setGitBase.of(gitBaseContent) });
    }

    const observer = new MutationObserver(() => {
      const dark = document.documentElement.classList.contains("dark");
      editor?.dispatch({
        effects: editorTheme.reconfigure(dark ? oneDark : []),
      });
    });
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });

    return () => {
      observer.disconnect();
      editor?.destroy();
      editor = null;
    };
  });

  onDestroy(() => {
    editor?.destroy();
    editor = null;
  });
</script>

<div bind:this={container} class="echo-codex-editor"></div>

<style>
  .echo-codex-editor {
    --editor-bg: #f9fafb;
    --editor-fg: #1f2937;
    --gutter-fg: #6b7280;
    --gutter-border: #e5e7eb;
    width: 100%;
    height: 100%;
  }
  :global(.dark) .echo-codex-editor {
    --editor-bg: #030712;
    --editor-fg: #e5e7eb;
    --gutter-fg: #9ca3af;
    --gutter-border: #1f2937;
  }
  .echo-codex-editor :global(.cm-editor) {
    height: 100%;
    background-color: var(--editor-bg);
    color: var(--editor-fg);
  }
  .echo-codex-editor :global(.cm-scroller) {
    overflow: auto;
    font-family:
      ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  }
  .echo-codex-editor :global(.cm-gutters) {
    background-color: var(--editor-bg) !important;
    color: var(--gutter-fg);
    border-right: 1px solid var(--gutter-border);
  }
  .echo-codex-editor :global(.cm-lineNumbers .cm-gutterElement) {
    padding: 0 8px 0 10px;
    min-width: 2.5rem;
  }
  .echo-codex-editor :global(.cm-activeLine),
  .echo-codex-editor :global(.cm-activeLineGutter) {
    background-color: color-mix(in oklab, var(--editor-fg) 6%, transparent);
  }
  .echo-codex-editor :global(.cm-lineNumbers .cm-git-line-added) {
    box-shadow: inset 3px 0 0 rgba(5, 150, 105, 0.7);
  }
  .echo-codex-editor :global(.cm-lineNumbers .cm-git-line-modified) {
    box-shadow: inset 3px 0 0 rgba(217, 119, 6, 0.7);
  }
  :global(.dark)
    .echo-codex-editor
    :global(.cm-lineNumbers .cm-git-line-added) {
    box-shadow: inset 3px 0 0 rgba(52, 211, 153, 0.8);
  }
  :global(.dark)
    .echo-codex-editor
    :global(.cm-lineNumbers .cm-git-line-modified) {
    box-shadow: inset 3px 0 0 rgba(251, 191, 36, 0.75);
  }
</style>
