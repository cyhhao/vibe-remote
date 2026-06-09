import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  type Ref,
} from 'react';
import { LexicalComposer } from '@lexical/react/LexicalComposer';
import { PlainTextPlugin } from '@lexical/react/LexicalPlainTextPlugin';
import { ContentEditable } from '@lexical/react/LexicalContentEditable';
import { LexicalErrorBoundary } from '@lexical/react/LexicalErrorBoundary';
import { OnChangePlugin } from '@lexical/react/LexicalOnChangePlugin';
import { HistoryPlugin } from '@lexical/react/LexicalHistoryPlugin';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import {
  $createParagraphNode,
  $getRoot,
  $isElementNode,
  $isLineBreakNode,
  $isTextNode,
  COMMAND_PRIORITY_HIGH,
  KEY_ENTER_COMMAND,
  PASTE_COMMAND,
  type EditorState,
  type LexicalNode,
} from 'lexical';
import {
  BeautifulMentionsPlugin,
  BeautifulMentionNode,
  $isBeautifulMentionNode,
  type BeautifulMentionsItem,
  type BeautifulMentionsMenuProps,
  type BeautifulMentionsMenuItemProps,
} from 'lexical-beautiful-mentions';

import { filesFromClipboard } from '../../lib/clipboardFiles';
import { isSoftKeyboardOpen, isTouchCapableDevice } from '../../lib/softKeyboard';
import { cn } from '../../lib/utils';
import { dedupeReferences, type MentionReference } from '../../lib/mentions';

export type AgentSearchResult = {
  name: string;
  agent_id?: string | null;
  backend?: string | null;
  description?: string | null;
};
export type SessionSearchResult = { session_id: string; title?: string | null };

export interface MentionEditorHandle {
  focus: () => void;
  clear: () => void;
  /** Append free text at the end (voice transcript) without disturbing chips. */
  append: (text: string) => void;
  /** Replace the whole editor with plain text (restore on a failed send). */
  setText: (text: string) => void;
}

export interface MentionEditorProps {
  placeholder?: string;
  disabled?: boolean;
  autoFocus?: boolean;
  /** Seed once (saved draft). Markers in the seed restore as plain text in v1. */
  initialText?: string | null;
  className?: string;
  onChange: (text: string, references: MentionReference[]) => void;
  onSubmit: () => void;
  onSearchAgents: (query: string) => Promise<AgentSearchResult[]>;
  onSearchSessions: (query: string) => Promise<SessionSearchResult[]>;
  /** Pasting a file into the editor (clipboard screenshot or OS-copied file)
   *  hands it here instead of pasting as text. Unset → paste behaves as plain
   *  text (e.g. when the composer has no upload target). */
  onPasteFiles?: (files: File[]) => void;
}

// Per-trigger chip classes — styled in index.css to read like a Badge
// (success/mint for agents, info/cyan for sessions).
// Chip styling for the mention nodes inside the editor — Tailwind utilities that
// mirror Badge's success (agent) / info (session) variants.
const MENTION_THEME = {
  '@': 'rounded-full border border-mint/40 bg-mint-soft px-1.5 py-px font-medium text-mint',
  '@Focused': 'ring-1 ring-mint/60',
  '#': 'rounded-full border border-cyan/40 bg-cyan-soft px-1.5 py-px font-medium text-cyan',
  '#Focused': 'ring-1 ring-cyan/60',
};

// Walk a Lexical node into our marker text, collecting references as it goes.
function nodeToMarkerText(node: LexicalNode, refs: MentionReference[]): string {
  if ($isBeautifulMentionNode(node)) {
    const trigger = node.getTrigger();
    const value = node.getValue();
    const data = (node.getData() ?? {}) as Record<string, string | number | boolean | null>;
    if (trigger === '@') {
      // The marker terminates at the first `>`; a name containing `>` (or a
      // newline) would serialize to an ambiguous `@<a>b>`. Such names can't be
      // round-tripped, so fall back to plain text rather than a broken marker.
      // (searchAgents also filters these out — this is defense in depth.)
      if (/[>\n]/.test(value)) return `@${value}`;
      refs.push({
        kind: 'agent',
        name: value,
        agent_id: data.agentId != null ? String(data.agentId) : undefined,
        backend: data.backend != null ? String(data.backend) : undefined,
      });
      return `@<${value}>`;
    }
    if (trigger === '#') {
      const sessionId = data.sessionId != null ? String(data.sessionId) : value;
      refs.push({ kind: 'session', session_id: sessionId, title: value });
      return `#<${sessionId}>`;
    }
    return `${trigger}${value}`;
  }
  if ($isLineBreakNode(node)) return '\n';
  if ($isTextNode(node)) return node.getTextContent();
  if ($isElementNode(node)) {
    return node
      .getChildren()
      .map((child) => nodeToMarkerText(child, refs))
      .join('');
  }
  return '';
}

function serializeEditorState(state: EditorState): { text: string; references: MentionReference[] } {
  return state.read(() => {
    const refs: MentionReference[] = [];
    const blocks = $getRoot()
      .getChildren()
      .map((block) => nodeToMarkerText(block, refs));
    return { text: blocks.join('\n'), references: dedupeReferences(refs) };
  });
}

// Enter submits — except Shift+Enter (newline), mid-IME composition (CJK), while
// the on-screen keyboard is open (mobile: Enter = newline, send via button), or
// while the mention menu is open (Enter picks the highlighted suggestion).
function EnterSubmitPlugin({
  onSubmit,
  menuOpenRef,
}: {
  onSubmit: () => void;
  menuOpenRef: React.MutableRefObject<boolean>;
}) {
  const [editor] = useLexicalComposerContext();
  useEffect(
    () =>
      editor.registerCommand(
        KEY_ENTER_COMMAND,
        (event: KeyboardEvent | null) => {
          if (!event || event.shiftKey) return false;
          if (event.isComposing || event.keyCode === 229) return false;
          if (menuOpenRef.current || isSoftKeyboardOpen()) return false;
          event.preventDefault();
          onSubmit();
          return true;
        },
        COMMAND_PRIORITY_HIGH,
      ),
    [editor, onSubmit, menuOpenRef],
  );
  return null;
}

function EditablePlugin({ disabled }: { disabled: boolean }) {
  const [editor] = useLexicalComposerContext();
  useEffect(() => {
    editor.setEditable(!disabled);
  }, [editor, disabled]);
  return null;
}

// Intercept a paste that carries files (a clipboard screenshot, or a file copied
// in the OS file manager) and hand it to the composer's uploader instead of
// letting Lexical insert it as text — the editor sibling of the `+` picker and
// chat-page drag-drop. Registered at HIGH priority so it runs before Lexical's
// own (LOW/EDITOR) paste handling: a files paste is consumed here (return true),
// while a plain text / rich-text paste carries no files and falls through
// (return false) to normal text pasting. Reads the callback through a ref so the
// command registers once per editor and never churns on the composer's renders.
function PasteFilesPlugin({ onPasteFiles }: { onPasteFiles?: (files: File[]) => void }) {
  const [editor] = useLexicalComposerContext();
  const handlerRef = useRef(onPasteFiles);
  handlerRef.current = onPasteFiles;
  useEffect(
    () =>
      editor.registerCommand(
        PASTE_COMMAND,
        (event) => {
          const handler = handlerRef.current;
          if (!handler) return false;
          // PASTE_COMMAND can also fire for non-clipboard (input/keyboard) paste
          // triggers; only a ClipboardEvent carries files.
          const clipboardData = event instanceof ClipboardEvent ? event.clipboardData : null;
          const files = filesFromClipboard(clipboardData);
          if (files.length === 0) return false;
          event.preventDefault();
          handler(files);
          return true;
        },
        COMMAND_PRIORITY_HIGH,
      ),
    [editor],
  );
  return null;
}

function BootstrapPlugin({
  autoFocus,
  initialText,
  bridgeRef,
}: {
  autoFocus: boolean;
  initialText?: string | null;
  bridgeRef: Ref<MentionEditorHandle>;
}) {
  const [editor] = useLexicalComposerContext();
  const seeded = useRef(false);

  useImperativeHandle(
    bridgeRef,
    () => ({
      focus: () => editor.focus(),
      clear: () =>
        editor.update(() => {
          const root = $getRoot();
          root.clear();
          root.append($createParagraphNode());
        }),
      append: (text: string) =>
        editor.update(() => {
          const root = $getRoot();
          const selection = root.selectEnd();
          const prefix = root.getTextContent().length > 0 ? ' ' : '';
          selection.insertText(`${prefix}${text}`);
        }),
      setText: (text: string) =>
        editor.update(() => {
          const root = $getRoot();
          root.clear();
          const paragraph = $createParagraphNode();
          root.append(paragraph);
          if (text) paragraph.selectStart().insertText(text);
        }),
    }),
    [editor],
  );

  useEffect(() => {
    if (seeded.current) return;
    seeded.current = true;
    const raw = initialText ?? '';
    if (!raw.trim()) {
      if (autoFocus && !isTouchCapableDevice()) editor.focus();
      return;
    }
    editor.update(() => {
      const root = $getRoot();
      root.clear();
      const paragraph = $createParagraphNode();
      root.append(paragraph);
      // v1: a restored draft seeds as plain text (markers render raw until
      // re-picked); the content is lossless for sending. Insert the raw draft so
      // intentional leading/trailing whitespace survives the round-trip.
      paragraph.selectStart().insertText(raw);
    });
    if (autoFocus && !isTouchCapableDevice()) editor.focus();
    // Only on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return null;
}

const MentionMenu = forwardRef<HTMLUListElement, BeautifulMentionsMenuProps>(
  ({ loading: _loading, children, ...props }, ref) => (
    <ul
      ref={ref}
      className="z-50 m-0 max-h-64 min-w-[15rem] list-none overflow-y-auto overflow-x-hidden rounded-md border border-border bg-panel p-1 text-text shadow-md"
      {...props}
    >
      {children}
    </ul>
  ),
);
MentionMenu.displayName = 'MentionMenu';

const MentionMenuItem = forwardRef<HTMLLIElement, BeautifulMentionsMenuItemProps>(
  ({ selected, item, itemValue: _itemValue, label: _label, ...props }, ref) => {
    const data = (item.data ?? {}) as Record<string, string | number | boolean | null>;
    // Agents show their backend as a secondary hint; sessions show only the title.
    const secondary = item.trigger === '@' && data.backend != null ? String(data.backend) : '';
    return (
      <li
        ref={ref}
        className={cn(
          'flex cursor-pointer select-none items-center gap-2 rounded-sm px-2 py-1.5 text-sm outline-none',
          selected ? 'bg-accent/10 text-accent' : 'text-text',
        )}
        {...props}
      >
        <span className="shrink-0 text-muted">{item.trigger}</span>
        <span className="truncate">{item.value}</span>
        {secondary ? <span className="ml-auto shrink-0 text-xs text-muted">{secondary}</span> : null}
      </li>
    );
  },
);
MentionMenuItem.displayName = 'MentionMenuItem';

// A Lexical-backed text input with `@` (agent) / `#` (session) inline-chip
// mentions. Owns only the editor; the surrounding composer shell (send button,
// attachment chips, voice) stays in Composer.
export const MentionEditor = forwardRef<MentionEditorHandle, MentionEditorProps>(function MentionEditor(
  {
    placeholder,
    disabled = false,
    autoFocus = false,
    initialText = null,
    className,
    onChange,
    onSubmit,
    onSearchAgents,
    onSearchSessions,
    onPasteFiles,
  },
  ref,
) {
  const menuOpenRef = useRef(false);

  const handleChange = useCallback(
    (state: EditorState) => {
      const { text, references } = serializeEditorState(state);
      onChange(text, references);
    },
    [onChange],
  );

  const onSearch = useCallback(
    async (trigger: string, queryString?: string | null): Promise<BeautifulMentionsItem[]> => {
      const query = (queryString ?? '').trim();
      if (trigger === '@') {
        const agents = await onSearchAgents(query);
        return agents.map((a) => ({
          value: a.name,
          agentId: a.agent_id ?? null,
          backend: a.backend ?? null,
        }));
      }
      if (trigger === '#') {
        const sessions = await onSearchSessions(query);
        return sessions.map((s) => ({
          value: s.title && s.title.trim() ? s.title : s.session_id,
          sessionId: s.session_id,
        }));
      }
      return [];
    },
    [onSearchAgents, onSearchSessions],
  );

  const initialConfig = useRef({
    namespace: 'avibe-mention-composer',
    theme: { beautifulMentions: MENTION_THEME },
    nodes: [BeautifulMentionNode],
    editable: !disabled,
    onError: (error: Error) => {
      // Surface in dev; never throw out of the editor and wipe the box.
      console.error('[MentionEditor]', error);
    },
  }).current;

  return (
    <div className={cn('relative', className)}>
      <LexicalComposer initialConfig={initialConfig}>
        <PlainTextPlugin
          contentEditable={
            <ContentEditable
              className="max-h-40 min-h-9 w-full overflow-y-auto whitespace-pre-wrap break-words bg-transparent py-2 text-[13px] leading-5 text-foreground outline-none"
              aria-label={placeholder}
              spellCheck
            />
          }
          placeholder={
            <div className="pointer-events-none absolute left-0 top-2 select-none text-[13px] leading-5 text-muted">
              {placeholder}
            </div>
          }
          ErrorBoundary={LexicalErrorBoundary}
        />
        <HistoryPlugin />
        <OnChangePlugin onChange={handleChange} ignoreSelectionChange />
        <BeautifulMentionsPlugin
          triggers={['@', '#']}
          onSearch={onSearch}
          searchDelay={150}
          menuItemLimit={8}
          // Only Agents/Sessions returned by onSearch may become chips — no
          // user-created (unresolved) mentions (the picker-selected-only contract).
          creatable={false}
          insertOnBlur={false}
          menuComponent={MentionMenu}
          menuItemComponent={MentionMenuItem}
          menuAnchorClassName="z-50"
          onMenuOpen={() => {
            menuOpenRef.current = true;
          }}
          onMenuClose={() => {
            menuOpenRef.current = false;
          }}
        />
        <EnterSubmitPlugin onSubmit={onSubmit} menuOpenRef={menuOpenRef} />
        <EditablePlugin disabled={disabled} />
        <PasteFilesPlugin onPasteFiles={onPasteFiles} />
        <BootstrapPlugin autoFocus={autoFocus} initialText={initialText} bridgeRef={ref} />
      </LexicalComposer>
    </div>
  );
});
