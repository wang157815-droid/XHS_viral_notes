"use client";

import Image from "next/image";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
  type KeyboardEvent,
  type ReactNode,
} from "react";

import { motion, useReducedMotion } from "motion/react";

import type { AdvancedConfig, KnowledgeRefPayload } from "@/lib/contracts";
import {
  AdvancedParamsMenuSection,
  advancedOverridesDefault,
  changedAdvancedLabels,
  SlidersIcon,
} from "./composer-shared";
import {
  getActiveKbMention,
  KnowledgeMentionList,
  useKbDocuments,
  type KbDocRow,
} from "./knowledge-mention";
import { SUGGESTION_TAGS } from "./mock-canvas-data";
import {
  ComposerDraftThumbnailsRow,
  isAllowedComposerAttachmentFile,
  type ComposerFileDraft,
} from "./composer-draft-thumbnails";

export type LocalMediaDraft = ComposerFileDraft;

interface PromptComposerProps {
  value: string;
  advanced: AdvancedConfig;
  disabled?: boolean;
  busy?: boolean;
  notice?: ReactNode;
  onValueChange: (value: string) => void;
  onAdvancedChange: (value: AdvancedConfig) => void;
  onSubmit: () => void | Promise<void>;
  onPickSuggestion: (text: string) => void | Promise<void>;
  knowledgeRefs?: KnowledgeRefPayload[];
  localMediaDrafts?: LocalMediaDraft[];
  onRemoveKnowledgeRef?: (docId: string) => void;
  onRemoveLocalMediaDraft?: (id: string) => void;
  onResetAdvancedField?: (key: keyof AdvancedConfig) => void;
  canReadKnowledge?: boolean;
  /** 从 @ 提及列表选择文档时切换引用（与 ChatPanel `toggleKbDoc` 一致） */
  onKbDocToggle?: (doc: KbDocRow) => void;
  onTriggerLocalMedia?: () => void;
  /** 拖入文件或外部追加草稿（与 ChatPanel 共用白名单过滤） */
  onPickLocalFiles?: (files: File[]) => void;
}

export function PromptComposer({
  value,
  advanced,
  disabled,
  busy,
  notice,
  onValueChange,
  onAdvancedChange,
  onSubmit,
  onPickSuggestion,
  knowledgeRefs = [],
  localMediaDrafts = [],
  onRemoveKnowledgeRef,
  onRemoveLocalMediaDraft,
  onResetAdvancedField,
  canReadKnowledge = false,
  onKbDocToggle,
  onTriggerLocalMedia,
  onPickLocalFiles,
}: PromptComposerProps) {
  const reduceMotion = useReducedMotion();
  const canSend = (Boolean(value.trim()) || localMediaDrafts.length > 0) && !disabled && !busy;
  const [plusOpen, setPlusOpen] = useState(false);
  const plusWrapRef = useRef<HTMLDivElement | null>(null);
  const textInputRef = useRef<HTMLInputElement | null>(null);
  const [inputCaret, setInputCaret] = useState(0);
  const [shellDragActive, setShellDragActive] = useState(false);
  const shellDragDepth = useRef(0);

  const onShellDragEnter = useCallback(
    (e: DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      if (!onPickLocalFiles || disabled || busy) return;
      shellDragDepth.current += 1;
      setShellDragActive(true);
    },
    [onPickLocalFiles, disabled, busy],
  );

  const onShellDragLeave = useCallback((e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    shellDragDepth.current -= 1;
    if (shellDragDepth.current <= 0) {
      shellDragDepth.current = 0;
      setShellDragActive(false);
    }
  }, []);

  const onShellDragOver = useCallback(
    (e: DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      if (onPickLocalFiles && !disabled && !busy) e.dataTransfer.dropEffect = "copy";
    },
    [onPickLocalFiles, disabled, busy],
  );

  const onShellDrop = useCallback(
    (e: DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      shellDragDepth.current = 0;
      setShellDragActive(false);
      if (!onPickLocalFiles || disabled || busy) return;
      const picked = Array.from(e.dataTransfer.files ?? []).filter(isAllowedComposerAttachmentFile);
      if (picked.length) onPickLocalFiles(picked);
    },
    [onPickLocalFiles, disabled, busy],
  );

  const hasExtras =
    knowledgeRefs.length > 0 || localMediaDrafts.length > 0 || advancedOverridesDefault(advanced);

  const kbMention = useMemo(() => getActiveKbMention(value, inputCaret), [value, inputCaret]);
  const kbMentionLoad = kbMention !== null && canReadKnowledge;
  const { docs: kbDocRows, loading: kbDocsLoading } = useKbDocuments(kbMentionLoad);
  const kbSelected = useMemo(() => new Set(knowledgeRefs.map((r) => r.doc_id)), [knowledgeRefs]);

  useEffect(() => {
    if (value !== "") return;
    let cancelled = false;
    queueMicrotask(() => {
      if (!cancelled) setInputCaret(0);
    });
    return () => {
      cancelled = true;
    };
  }, [value]);

  useEffect(() => {
    if (!plusOpen) return;
    const onDoc = (e: MouseEvent) => {
      const el = plusWrapRef.current;
      if (el && !el.contains(e.target as Node)) setPlusOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [plusOpen]);

  const insertKbMention = () => {
    if (!canReadKnowledge || disabled || busy) return;
    setPlusOpen(false);
    const el = textInputRef.current;
    const caret = el?.selectionStart ?? value.length;
    const next = value.slice(0, caret) + "@" + value.slice(caret);
    onValueChange(next);
    const pos = caret + 1;
    setInputCaret(pos);
    requestAnimationFrame(() => {
      el?.focus();
      el?.setSelectionRange(pos, pos);
    });
  };

  const handleKbMentionPick = (doc: KbDocRow) => {
    const m = getActiveKbMention(value, inputCaret);
    if (!m || !onKbDocToggle) return;
    const end = inputCaret;
    onKbDocToggle(doc);
    const next = value.slice(0, m.start) + value.slice(end);
    onValueChange(next);
    const pos = m.start;
    setInputCaret(pos);
    requestAnimationFrame(() => {
      const inp = textInputRef.current;
      inp?.focus();
      inp?.setSelectionRange(pos, pos);
    });
  };

  const handleMainInputChange = (e: ChangeEvent<HTMLInputElement>) => {
    onValueChange(e.target.value);
    setInputCaret(e.target.selectionStart ?? e.target.value.length);
  };

  const plusBlock = (
    <div className="relative flex-shrink-0" ref={plusWrapRef}>
      <button
        type="button"
        onClick={() => setPlusOpen((v) => !v)}
        disabled={disabled || busy}
        className={`flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-full transition ${plusOpen ? "bg-obsidian text-papyrus" : "bg-transparent text-obsidian/40 hover:bg-black/[0.04] hover:text-obsidian/60"} disabled:cursor-not-allowed disabled:opacity-50`}
        title="高级配置、知识库或上传照片和文件"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M12 5v14M5 12h14" />
        </svg>
      </button>
      {plusOpen ? (
        <div className="absolute bottom-full left-0 z-30 mb-2 w-[min(calc(100vw-32px),288px)] overflow-visible rounded-[10px] border border-black/[0.08] bg-white py-0 text-[13px] shadow-lg">
          <AdvancedParamsMenuSection advanced={advanced} onChange={onAdvancedChange} />
          <button
            type="button"
            disabled={!canReadKnowledge}
            title={!canReadKnowledge ? "无知识库读取权限" : undefined}
            className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left text-obsidian/80 hover:bg-moss/40 disabled:cursor-not-allowed disabled:text-obsidian/25"
            onClick={() => insertKbMention()}
          >
            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded border border-black/[0.1] text-[11px] font-bold text-obsidian/50">
              @
            </span>
            知识库
          </button>
          <button
            type="button"
            disabled={disabled || busy}
            className="flex w-full items-center gap-2.5 border-t border-black/[0.05] px-3 py-2.5 text-left text-obsidian/80 hover:bg-moss/40 disabled:opacity-50"
            onClick={() => {
              setPlusOpen(false);
              onTriggerLocalMedia?.();
            }}
          >
            <svg
              className="h-[18px] w-[18px] shrink-0 text-obsidian/45"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              aria-hidden
            >
              <path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19A4 4 0 0116.85 3h0a4 4 0 013.54 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48" />
            </svg>
            上传照片和文件
          </button>
        </div>
      ) : null}
    </div>
  );

  const sendButton = (
    <button
      type="button"
      onClick={() => void onSubmit()}
      disabled={!canSend}
      className={`flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-full transition disabled:cursor-not-allowed disabled:opacity-100 ${canSend ? "bg-[#B9CED1] text-white hover:bg-[#9CBBC0]" : "bg-[#F0F0F0] text-obsidian/25"}`}
      title="开始分析"
    >
      {busy ? (
        <span
          className="h-[18px] w-[18px] shrink-0 animate-spin rounded-full border-2 border-obsidian/15 border-t-obsidian/50"
          aria-hidden
        />
      ) : (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M13 5l7 7-7 7M5 12h14" />
        </svg>
      )}
    </button>
  );

  const shellTransition = reduceMotion ? { duration: 0 } : { duration: 0.5, ease: [0.4, 0, 0.2, 1] as const };

  return (
    <div className="relative flex min-h-full flex-1 flex-col items-center overflow-hidden bg-[#F7F7F7] px-5 sm:px-8">
      {/* 左上角品牌 logo */}
      <div className="absolute left-3 top-3 flex items-center gap-2 sm:left-4">
        <Image src="/logo.png" alt="RedMuse" width={44} height={44} className="object-contain" priority />
        <span className="font-serif text-[15px] font-medium italic leading-none tracking-[0.02em] text-obsidian">
          Red Muse
        </span>
      </div>

      {/* 标题区：高度固定 346px，标题底部对齐底边，与输入框保持 40px 间距 */}
      <div className="flex h-[346px] w-full max-w-[720px] flex-col items-center justify-end pb-10 text-center">
        <h1 className="font-serif text-[28px] font-light leading-snug tracking-[0.12em] text-obsidian sm:text-[34px] md:text-[40px]">
          洞见爆文规律，开启灵动创作
        </h1>
      </div>

      <div className="relative z-20 w-full max-w-[755px]">
        <motion.div
          layout
          initial={false}
          animate={{
            borderRadius: hasExtras ? 24 : 9999,
          }}
          transition={shellTransition}
          onDragEnter={onShellDragEnter}
          onDragLeave={onShellDragLeave}
          onDragOver={onShellDragOver}
          onDrop={onShellDrop}
          className={`relative z-10 flex min-h-[55px] w-full flex-col overflow-visible border border-black/[0.04] bg-white px-4 py-2 shadow-[0_20px_60px_rgba(0,0,0,0.08)] ${shellDragActive && onPickLocalFiles ? "ring-2 ring-dew ring-offset-2 ring-offset-[#F7F7F7]" : ""}`}
        >
          <ComposerDraftThumbnailsRow
            drafts={localMediaDrafts}
            disabled={disabled}
            onRemove={(id) => onRemoveLocalMediaDraft?.(id)}
            showDropHint={Boolean(onPickLocalFiles) && !disabled}
          />
          {hasExtras ? (
            <>
              <div className="relative min-h-10 w-full min-w-0 px-0.5 pt-0.5">
                <KnowledgeMentionList
                  open={kbMention !== null}
                  placement="below"
                  loading={kbDocsLoading}
                  docs={kbDocRows}
                  query={kbMention?.query ?? ""}
                  selectedIds={kbSelected}
                  onPick={handleKbMentionPick}
                  canRead={canReadKnowledge}
                />
                <input
                  ref={textInputRef}
                  type="text"
                  value={value}
                  onChange={handleMainInputChange}
                  onSelect={(e) => setInputCaret(e.currentTarget.selectionStart ?? value.length)}
                  onClick={(e) => setInputCaret(e.currentTarget.selectionStart ?? value.length)}
                  onKeyUp={(e) => setInputCaret(e.currentTarget.selectionStart ?? value.length)}
                  disabled={disabled || busy}
                  placeholder="请输入分析目标，例如：分析近半年防脱精华的视频类爆款笔记…"
                  className="h-10 w-full min-w-0 bg-transparent text-[15px] text-obsidian outline-none placeholder:text-obsidian/35 disabled:cursor-not-allowed disabled:opacity-60"
                  onKeyDown={(e: KeyboardEvent<HTMLInputElement>) => {
                    const el = e.currentTarget;
                    const c = el.selectionStart ?? value.length;
                    if (e.key === "Escape") {
                      const m = getActiveKbMention(value, c);
                      if (m) {
                        e.preventDefault();
                        const next = value.slice(0, m.start) + value.slice(c);
                        onValueChange(next);
                        setInputCaret(m.start);
                        requestAnimationFrame(() => {
                          el.focus();
                          el.setSelectionRange(m.start, m.start);
                        });
                        return;
                      }
                    }
                    if (e.key === "Enter") {
                      e.preventDefault();
                      if (canSend) void onSubmit();
                    }
                  }}
                />
              </div>
              <div className="mt-1.5 flex w-full items-end justify-between gap-2 pt-1">
                <div className="composer-chip-strip flex min-w-0 flex-1 flex-wrap items-center gap-1.5">
                  <div className="flex h-10 shrink-0 items-center">{plusBlock}</div>
                  {knowledgeRefs.map((r) => (
                    <span
                      key={r.doc_id}
                      className="inline-flex max-w-[200px] shrink-0 items-center gap-1 rounded-full bg-dew/15 px-2.5 py-1 text-[11px] font-medium text-obsidian/75"
                    >
                      <span className="truncate">@{r.title || r.doc_id.slice(0, 8)}</span>
                      <button
                        type="button"
                        className="shrink-0 rounded-full px-0.5 text-obsidian/40 hover:text-obsidian"
                        onClick={() => onRemoveKnowledgeRef?.(r.doc_id)}
                        aria-label="移除"
                      >
                        ×
                      </button>
                    </span>
                  ))}
                  {changedAdvancedLabels(advanced).map((row) => (
                    <span
                      key={row.key}
                      className="inline-flex max-w-[200px] shrink-0 items-center gap-1 rounded-full bg-obsidian/[0.06] px-2.5 py-1 text-[11px] text-obsidian/70"
                    >
                      <SlidersIcon className="h-3.5 w-3.5 shrink-0 text-obsidian/40" />
                      <span className="truncate">
                        {row.label}：{row.value}
                      </span>
                      <button
                        type="button"
                        className="shrink-0 text-obsidian/40 hover:text-obsidian"
                        onClick={() => onResetAdvancedField?.(row.key as keyof AdvancedConfig)}
                        aria-label={`恢复${row.label}默认`}
                      >
                        ×
                      </button>
                    </span>
                  ))}
                </div>
                <div className="flex h-10 shrink-0 items-center pb-px">{sendButton}</div>
              </div>
            </>
          ) : (
            <div className="flex w-full items-center gap-2 py-1">
              <div className="flex h-10 shrink-0 items-center">{plusBlock}</div>
              <div className="relative min-h-10 min-w-0 flex-1">
                <KnowledgeMentionList
                  open={kbMention !== null}
                  placement="below"
                  loading={kbDocsLoading}
                  docs={kbDocRows}
                  query={kbMention?.query ?? ""}
                  selectedIds={kbSelected}
                  onPick={handleKbMentionPick}
                  canRead={canReadKnowledge}
                />
                <input
                  ref={textInputRef}
                  type="text"
                  value={value}
                  onChange={handleMainInputChange}
                  onSelect={(e) => setInputCaret(e.currentTarget.selectionStart ?? value.length)}
                  onClick={(e) => setInputCaret(e.currentTarget.selectionStart ?? value.length)}
                  onKeyUp={(e) => setInputCaret(e.currentTarget.selectionStart ?? value.length)}
                  disabled={disabled || busy}
                  placeholder="请输入分析目标，例如：分析近半年防脱精华的视频类爆款笔记…"
                  className="h-10 w-full min-w-0 bg-transparent text-[15px] text-obsidian outline-none placeholder:text-obsidian/35 disabled:cursor-not-allowed disabled:opacity-60 text-sm sm:text-[15px]"
                  onKeyDown={(e: KeyboardEvent<HTMLInputElement>) => {
                    const el = e.currentTarget;
                    const c = el.selectionStart ?? value.length;
                    if (e.key === "Escape") {
                      const m = getActiveKbMention(value, c);
                      if (m) {
                        e.preventDefault();
                        const next = value.slice(0, m.start) + value.slice(c);
                        onValueChange(next);
                        setInputCaret(m.start);
                        requestAnimationFrame(() => {
                          el.focus();
                          el.setSelectionRange(m.start, m.start);
                        });
                        return;
                      }
                    }
                    if (e.key === "Enter") {
                      e.preventDefault();
                      if (canSend) void onSubmit();
                    }
                  }}
                />
              </div>
              <div className="flex h-10 shrink-0 items-center">{sendButton}</div>
            </div>
          )}
        </motion.div>

        {notice ? <div className="mt-4">{notice}</div> : null}

        <div className="mt-[25px] flex flex-wrap justify-center gap-2.5 sm:gap-3">
          {SUGGESTION_TAGS.slice(0, 3).map((text) => (
            <button
              key={text}
              type="button"
              onClick={() => void onPickSuggestion(text)}
              disabled={disabled || busy}
              className="rounded-full border border-black/[0.06] bg-white px-5 py-2 text-[13px] text-obsidian/45 shadow-[0_1px_3px_rgba(0,0,0,0.04)] transition hover:border-black/[0.1] hover:bg-white hover:text-obsidian/70 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {text}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
