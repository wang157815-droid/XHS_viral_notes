"use client";

import { useCallback, useEffect, useState, type CSSProperties } from "react";

import { apiPost, generateIdempotencyKey } from "@/lib/api-client";
import type { ApiResponse, CanvasModule } from "@/lib/contracts";

import type {
  CaseCardSection,
  ChecklistSection,
  DataTableCell,
  DataTableSection,
  EditableGroup,
  ModuleSection,
  PrototypeAction,
  PrototypeBadge,
  PrototypeModule,
  StaticParagraph,
  TagRowSection,
  ViralMatrixElementCategory,
  ViralMatrixModel,
  ViralMatrixSection,
} from "./mock-canvas-data";

export interface CanvasParagraphEnv {
  taskId?: string;
  realtime?: boolean;
  onToast?: (type: "ok" | "err", message: string) => void;
  registerRegenerateAnchor?: (moduleId: string, paragraphId: string) => void;
  onModulePatched?: (module: CanvasModule) => void;
  refreshCanvas?: () => Promise<unknown>;
}

interface ModuleCardProps {
  module: PrototypeModule;
  busy?: boolean;
  onActionClick?: (module: PrototypeModule, action: PrototypeAction) => void;
  paragraphEnv?: CanvasParagraphEnv;
}

const BADGE_STYLE: Record<PrototypeBadge["color"], CSSProperties> = {
  red: { background: "#FFF0EE", color: "#FF4757" },
  green: { background: "#F0FAF0", color: "#3D8C40" },
  amber: { background: "#FFF8E6", color: "#8B6914" },
  gray: { background: "#F5F3F0", color: "#8A8580" },
};

// ---- 爆文模型矩阵专用配置 ----
const ELEMENT_LABEL_MAP: Record<string, string> = {
  A_cover: "封面",
  B_cover_text: "封面压字",
  C_title: "标题",
  D_opening: "切入点",
  E_product_intro: "产品引出",
  F_product_placement: "产品植入",
};

const ELEMENT_ORDER = [
  "A_cover",
  "B_cover_text",
  "C_title",
  "D_opening",
  "E_product_intro",
  "F_product_placement",
];

type AnnotationState = "none" | "liked" | "disliked";

type ParagraphFeedbackAction = "like" | "dislike" | "delete" | "edit" | "reset";

interface ParagraphFeedbackResponse {
  task_id: string;
  module_id: string;
  paragraph_id: string;
  module: CanvasModule;
}

function actionToAnnotation(action?: string): AnnotationState {
  if (action === "like") return "liked";
  if (action === "dislike") return "disliked";
  return "none";
}

async function postParagraphFeedback(opts: {
  taskId: string;
  moduleId: string;
  paragraphId: string;
  version: number;
  action: ParagraphFeedbackAction;
  editedText?: string;
}): Promise<ApiResponse<ParagraphFeedbackResponse>> {
  const path = `/tasks/${encodeURIComponent(opts.taskId)}/modules/${encodeURIComponent(
    opts.moduleId,
  )}/paragraphs/${encodeURIComponent(opts.paragraphId)}/feedback`;
  return apiPost<ParagraphFeedbackResponse>(
    path,
    { action: opts.action, edited_text: opts.editedText },
    {
      withAuth: true,
      idempotencyKey: generateIdempotencyKey(),
      ifMatch: opts.version,
    },
  );
}

function feedbackSuccessMessage(action: ParagraphFeedbackAction): string {
  switch (action) {
    case "like":
      return "已点赞";
    case "dislike":
      return "已标记待修正";
    case "delete":
      return "已删除该段";
    case "reset":
      return "已取消反馈";
    case "edit":
      return "已保存反馈";
    default:
      return "已保存反馈";
  }
}

async function persistParagraphFeedback(opts: {
  env?: CanvasParagraphEnv;
  module: PrototypeModule;
  paragraphId: string;
  action: ParagraphFeedbackAction;
  editedText?: string;
}): Promise<boolean> {
  const { env, module, paragraphId, action, editedText } = opts;
  if (!env?.taskId || !env.realtime) {
    env?.onToast?.("ok", "演示模式 · 反馈未同步服务器");
    return true;
  }
  const res = await postParagraphFeedback({
    taskId: env.taskId,
    moduleId: module.moduleId,
    paragraphId,
    version: module.version,
    action,
    editedText,
  });
  if (!res.ok) {
    if (res.error.code === "INPUT_MODULE_VERSION_MISMATCH") {
      await env.refreshCanvas?.();
      env.onToast?.("err", "模块已更新，已自动刷新画布，请重试");
      return false;
    }
    env.onToast?.("err", `反馈失败：${res.error.message}`);
    return false;
  }
  env.onModulePatched?.(res.data.module);
  env.onToast?.("ok", feedbackSuccessMessage(action));
  return true;
}

export function ModuleCard({ module, busy, onActionClick, paragraphEnv }: ModuleCardProps) {
  const [open, setOpen] = useState<boolean>(module.defaultExpanded ?? false);

  return (
    <div
      className={`mb-3 overflow-hidden rounded-[14px] border bg-white ${
        module.highlighted ? "border-[#FFD6CC] shadow-[0_0_0_2px_rgba(255,71,87,0.06)]" : "border-[#F0EEEB]"
      }`}
    >
      <div
        className="flex cursor-pointer items-center justify-between px-[18px] py-[14px] hover:bg-[#FAFAF8]"
        onClick={() => setOpen((v) => !v)}
      >
        <div className="flex items-center gap-[10px]">
          <div
            className="flex h-[26px] w-[26px] items-center justify-center rounded-[7px] text-[12px] font-bold"
            style={{ background: module.icon.bg, color: module.icon.color }}
          >
            {module.icon.character}
          </div>
          <div className="text-[13px] font-bold">{module.title}</div>
          {module.badges.map((badge, idx) => (
            <span
              key={idx}
              className="ml-1.5 rounded px-2 py-0.5 text-[10px] font-semibold"
              style={BADGE_STYLE[badge.color]}
            >
              {badge.text}
            </span>
          ))}
        </div>
        <div className="flex items-center gap-1.5">
          {module.actions.map((action) => {
            const isDelete = action.id === "delete";
            return (
              <button
                key={action.id}
                type="button"
                disabled={busy}
                onClick={(e) => {
                  e.stopPropagation();
                  onActionClick?.(module, action);
                }}
                className={`rounded-[5px] border px-2.5 py-[3px] text-[10px] transition ${
                  isDelete
                    ? "border-[#E8E5E0] bg-transparent text-[#A8A4A0] hover:border-[#FFD6CC] hover:bg-[#FFF5F3] hover:text-[#E04040]"
                    : "border-[#E8E5E0] bg-transparent text-[#8A8580] hover:bg-[#F5F3F0] hover:text-[#5A5550]"
                } disabled:cursor-not-allowed disabled:opacity-60`}
              >
                {action.label}
              </button>
            );
          })}
          <span
            className={`ml-1 text-[16px] text-[#C5C0BA] transition ${
              open ? "rotate-90" : ""
            }`}
          >
            ▸
          </span>
        </div>
      </div>

      {!open && module.summary ? (
        <div className="px-[18px] pb-[10px] pl-[54px] text-[11px] leading-[1.5] text-[#8A8580]">
          {module.summary}
        </div>
      ) : null}

      {open ? (
        <div className="px-[18px] pb-[18px] text-[13px] leading-[1.8] text-[#3E3A36]">
          {module.sections.map((section) => (
            <SectionRenderer key={section.id} section={section} module={module} env={paragraphEnv} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

function SectionRenderer({
  section,
  module,
  env,
}: {
  section: ModuleSection;
  module: PrototypeModule;
  env?: CanvasParagraphEnv;
}) {
  switch (section.kind) {
    case "editable-group":
      return <EditableGroupView group={section} module={module} env={env} />;
    case "static":
      return <StaticParagraphView section={section} />;
    case "tag-row":
      return <TagRowView section={section} />;
    case "case":
      return <CaseCardView section={section} />;
    case "data-table":
      return <DataTableView section={section} module={module} env={env} />;
    case "checklist":
      return <ChecklistView section={section} />;
    case "viral-matrix":
      return <ViralMatrixView section={section} module={module} env={env} />;
    default:
      return null;
  }
}

function StaticParagraphView({ section }: { section: StaticParagraph }) {
  return (
    <p
      className={`mt-[6px] first:mt-0 ${section.extraClass ?? ""}`}
      dangerouslySetInnerHTML={{ __html: section.html }}
    />
  );
}

interface ParagraphRuntimeState {
  edited: boolean;
  annotation: AnnotationState;
  deleted: boolean;
  html: string;
  original: string;
}

function useParagraphState(
  initialHtml: string,
): [ParagraphRuntimeState, (patch: Partial<ParagraphRuntimeState>) => void] {
  const [state, setState] = useState<ParagraphRuntimeState>({
    edited: false,
    annotation: "none",
    deleted: false,
    html: initialHtml,
    original: initialHtml,
  });
  const patch = useCallback((p: Partial<ParagraphRuntimeState>) => {
    setState((prev) => ({ ...prev, ...p }));
  }, []);
  return [state, patch];
}

function EditableGroupView({
  group,
  module,
  env,
}: {
  group: EditableGroup;
  module: PrototypeModule;
  env?: CanvasParagraphEnv;
}) {
  return (
    <div className="mt-[10px] space-y-2">
      {group.items.map((item) => (
        <EditableParagraph
          key={item.id}
          paragraphId={item.id}
          html={item.html}
          module={module}
          env={env}
        />
      ))}
    </div>
  );
}

function EditableParagraph({
  paragraphId,
  html,
  module,
  env,
}: {
  paragraphId: string;
  html: string;
  module: PrototypeModule;
  env?: CanvasParagraphEnv;
}) {
  const serverAction = module.paragraphFeedback?.[paragraphId];
  const [state, patch] = useParagraphState(html);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    patch({ annotation: actionToAnnotation(serverAction) });
  }, [serverAction, patch]);

  if (state.deleted) return null;

  const persist = async (action: ParagraphFeedbackAction, editedText?: string) => {
    setPending(true);
    const ok = await persistParagraphFeedback({
      env,
      module,
      paragraphId,
      action,
      editedText,
    });
    setPending(false);
    return ok;
  };

  const annotationClass =
    state.annotation === "liked"
      ? "border-l-2 border-[#3D8C40] bg-[#F0FAF0] -ml-2 pl-2 rounded"
      : state.annotation === "disliked"
        ? "border-l-2 border-[#E8A84C] bg-[#FFF8E6] -ml-2 pl-2 rounded"
        : "";

  const editedClass = state.edited ? "border-l-2 border-[#E8A84C] -ml-2 pl-2" : "";

  return (
    <div
      className={`group relative rounded-md px-1 py-0.5 transition hover:bg-[#FFF8F5] focus-within:bg-white focus-within:shadow-[0_0_0_2px_rgba(255,71,87,0.15)] ${annotationClass} ${editedClass}`}
      data-paragraph-id={paragraphId}
    >
      <span className="pointer-events-none absolute right-0 -top-5 hidden rounded border border-[#F0EEEB] bg-white px-1.5 py-px text-[9px] text-[#A8A4A0] group-hover:block group-focus-within:block group-focus-within:border-[#FFD6CC] group-focus-within:text-[#FF4757]">
        双击编辑
      </span>
      <div
        contentEditable
        suppressContentEditableWarning
        onFocus={(e) => {
          patch({ original: e.currentTarget.innerHTML });
          env?.registerRegenerateAnchor?.(module.moduleId, paragraphId);
        }}
        onBlur={(e) => {
          const el = e.currentTarget;
          const newHtml = el.innerHTML;
          if (newHtml !== state.original) patch({ edited: true, html: newHtml });
          if (env?.taskId && env.realtime && newHtml !== state.original) {
            const text = el.innerText?.trim() ?? "";
            if (text) void persist("edit", text);
          }
        }}
        dangerouslySetInnerHTML={{ __html: state.html }}
        className="outline-none"
      />

      <div className="absolute top-1/2 -right-[70px] z-10 hidden -translate-y-1/2 flex-col gap-[3px] group-hover:flex group-focus-within:flex">
        <ParaIconButton
          title="标记为优质"
          active={state.annotation === "liked"}
          disabled={pending}
          activeClass="text-[#3D8C40] bg-[#F0FAF0] border-[#3D8C40]"
          hoverClass="hover:text-[#3D8C40] hover:bg-[#F0FAF0] hover:border-[#3D8C40]"
          onClick={() => {
            env?.registerRegenerateAnchor?.(module.moduleId, paragraphId);
            const prev = state.annotation;
            const nextAction: ParagraphFeedbackAction = state.annotation === "liked" ? "reset" : "like";
            patch({ annotation: nextAction === "reset" ? "none" : "liked" });
            void (async () => {
              const ok = await persist(nextAction);
              if (!ok) patch({ annotation: prev });
            })();
          }}
        >
          ✓
        </ParaIconButton>
        <ParaIconButton
          title="标记待修正"
          active={state.annotation === "disliked"}
          disabled={pending}
          activeClass="text-[#E04040] bg-[#FFF0EE] border-[#E04040]"
          hoverClass="hover:text-[#E04040] hover:bg-[#FFF0EE] hover:border-[#E04040]"
          onClick={() => {
            env?.registerRegenerateAnchor?.(module.moduleId, paragraphId);
            const prev = state.annotation;
            const nextAction: ParagraphFeedbackAction = state.annotation === "disliked" ? "reset" : "dislike";
            patch({ annotation: nextAction === "reset" ? "none" : "disliked" });
            void (async () => {
              const ok = await persist(nextAction);
              if (!ok) patch({ annotation: prev });
            })();
          }}
        >
          ✗
        </ParaIconButton>
        <ParaIconButton
          title="删除此段"
          active={false}
          disabled={pending}
          activeClass=""
          hoverClass="hover:text-[#E04040] hover:bg-[#FFF0EE] hover:border-[#E04040]"
          onClick={() => {
            env?.registerRegenerateAnchor?.(module.moduleId, paragraphId);
            if (typeof window !== "undefined" && window.confirm("确定删除这段内容？")) {
              patch({ deleted: true });
              void (async () => {
                const ok = await persist("delete");
                if (!ok) patch({ deleted: false });
              })();
            }
          }}
        >
          −
        </ParaIconButton>
      </div>
    </div>
  );
}

function ParaIconButton({
  title,
  active,
  activeClass,
  hoverClass,
  disabled,
  onClick,
  children,
}: {
  title: string;
  active: boolean;
  activeClass: string;
  hoverClass: string;
  disabled?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      title={title}
      disabled={disabled}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      className={`flex h-6 w-6 items-center justify-center rounded-md border border-[#E8E5E0] bg-white text-[12px] text-[#A8A4A0] transition ${
        active ? activeClass : hoverClass
      } disabled:cursor-not-allowed disabled:opacity-50`}
    >
      {children}
    </button>
  );
}

function ParagraphFeedbackMini({
  paragraphId,
  module,
  env,
}: {
  paragraphId: string;
  module: PrototypeModule;
  env?: CanvasParagraphEnv;
}) {
  const serverAction = module.paragraphFeedback?.[paragraphId];
  const [annotation, setAnnotation] = useState<AnnotationState>(() => actionToAnnotation(serverAction));
  const [pending, setPending] = useState(false);

  useEffect(() => {
    setAnnotation(actionToAnnotation(serverAction));
  }, [serverAction]);

  const persist = async (action: ParagraphFeedbackAction) => {
    setPending(true);
    const ok = await persistParagraphFeedback({
      env,
      module,
      paragraphId,
      action,
    });
    setPending(false);
    return ok;
  };

  return (
    <div
      className="ml-1 flex gap-0.5"
      onClick={(e) => e.stopPropagation()}
      data-paragraph-id={paragraphId}
    >
      <ParaIconButton
        title="赞"
        active={annotation === "liked"}
        disabled={pending}
        activeClass="text-[#3D8C40] bg-[#F0FAF0] border-[#3D8C40]"
        hoverClass="hover:text-[#3D8C40] hover:bg-[#F0FAF0]"
        onClick={() => {
          env?.registerRegenerateAnchor?.(module.moduleId, paragraphId);
          const prev = annotation;
          const nextAction: ParagraphFeedbackAction = annotation === "liked" ? "reset" : "like";
          setAnnotation(nextAction === "reset" ? "none" : "liked");
          void (async () => {
            const ok = await persist(nextAction);
            if (!ok) setAnnotation(prev);
          })();
        }}
      >
        ✓
      </ParaIconButton>
      <ParaIconButton
        title="踩"
        active={annotation === "disliked"}
        disabled={pending}
        activeClass="text-[#E04040] bg-[#FFF0EE] border-[#E04040]"
        hoverClass="hover:text-[#E04040] hover:bg-[#FFF0EE]"
        onClick={() => {
          env?.registerRegenerateAnchor?.(module.moduleId, paragraphId);
          const prev = annotation;
          const nextAction: ParagraphFeedbackAction = annotation === "disliked" ? "reset" : "dislike";
          setAnnotation(nextAction === "reset" ? "none" : "disliked");
          void (async () => {
            const ok = await persist(nextAction);
            if (!ok) setAnnotation(prev);
          })();
        }}
      >
        ✗
      </ParaIconButton>
    </div>
  );
}

function TagRowView({ section }: { section: TagRowSection }) {
  return (
    <div className="mt-[12px]">
      <p className="mb-[6px]">
        <strong>{section.label}</strong>
      </p>
      <div className="flex flex-wrap gap-1.5">
        {section.tags.map((tag) => (
          <span
            key={tag}
            className="rounded-[5px] border border-[#F5E6CC] bg-[#FFF8F0] px-2.5 py-[3px] text-[11px] text-[#8B6914]"
          >
            {tag}
          </span>
        ))}
      </div>
    </div>
  );
}

function CaseCardView({ section }: { section: CaseCardSection }) {
  const [deleted, setDeleted] = useState(false);
  if (deleted) return null;

  return (
    <div className="relative mt-2 rounded-lg border border-[#F0EEEB] px-[14px] py-[12px]">
      <button
        type="button"
        title="删除此案例"
        onClick={(e) => {
          e.stopPropagation();
          if (typeof window !== "undefined" && window.confirm("确定删除此案例？")) {
            setDeleted(true);
          }
        }}
        className="absolute right-2 top-2 flex h-6 w-6 items-center justify-center rounded-md border border-[#E8E5E0] bg-white text-[12px] text-[#A8A4A0] transition hover:border-[#E04040] hover:bg-[#FFF0EE] hover:text-[#E04040]"
      >
        ✗
      </button>
      <div className="text-[12px] font-semibold leading-tight">{section.title}</div>
      {section.meta.length ? (
        <div className="mt-0.5 flex gap-2.5 text-[10px] text-[#A8A4A0]">
          {section.meta.map((m) => (
            <span key={m}>{m}</span>
          ))}
        </div>
      ) : null}
      {section.description ? (
        <div className="mt-1 text-[11px] leading-[1.5] text-[#5A5550]">{section.description}</div>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------
// 公用封面缩略图组件(4.3pre.4)
// ---------------------------------------------------------------
type CoverThumbSize = 32 | 40 | 48 | 64;

function CoverThumb({
  url,
  noteUrl,
  size = 32,
  alt,
}: {
  url?: string;
  noteUrl?: string;
  size?: CoverThumbSize;
  alt?: string;
}) {
  const [failed, setFailed] = useState(false);
  const safeUrl = url?.trim() ?? "";
  const hasUrl = safeUrl.length > 0;

  const boxStyle: CSSProperties = {
    width: size,
    height: size,
  };

  if (!hasUrl || failed) {
    return (
      <div
        className="flex items-center justify-center rounded border border-[#F0EEEB] bg-[#F5F3F0] text-[9px] text-[#A8A4A0]"
        style={boxStyle}
      >
        无图
      </div>
    );
  }

  const img = (
    <img
      src={safeUrl}
      alt={alt ?? ""}
      referrerPolicy="no-referrer"
      loading="lazy"
      onError={() => setFailed(true)}
      className="rounded border border-[#F0EEEB] object-cover"
      style={boxStyle}
    />
  );

  if (noteUrl) {
    return (
      <a
        href={noteUrl}
        target="_blank"
        rel="noreferrer noopener"
        onClick={(e) => e.stopPropagation()}
        className="inline-block"
      >
        {img}
      </a>
    );
  }
  return img;
}

// ---------------------------------------------------------------
// DataTableView(4.3pre.4 扩展 cover cell)
// ---------------------------------------------------------------
function rowParagraphId(row: DataTableCell[]): string | undefined {
  for (const c of row) {
    if (c.paragraphId) return c.paragraphId;
  }
  return undefined;
}

function DataTableView({
  section,
  module,
  env,
}: {
  section: DataTableSection;
  module: PrototypeModule;
  env?: CanvasParagraphEnv;
}) {
  return (
    <div>
      {section.disclaimer ? (
        <div
          className="mb-2.5 rounded border-l-[3px] border-[#E8E5E0] bg-[#FAFAF8] px-3 py-2 text-[11px] text-[#A8A4A0]"
        >
          {section.disclaimer}
        </div>
      ) : null}
      <div className="overflow-x-auto">
        <table className="mt-2 w-full border-collapse text-[12px]">
          <thead>
            <tr>
              {section.headers.map((h) => (
                <th
                  key={h}
                  className="border-b border-[#F0EEEB] px-2.5 py-[7px] text-left text-[10px] font-semibold text-[#8A8580]"
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {section.rows.map((row, idx) => {
              const pid = rowParagraphId(row);
              return (
                <tr key={idx} data-paragraph-id={pid ?? undefined}>
                  {row.map((cell, cidx) => {
                    const isLast = cidx === row.length - 1;
                    const showFeedback = isLast && pid && env?.taskId && env.realtime;
                    return (
                      <td key={cidx} className="border-b border-[#F5F3F0] px-2.5 py-[9px] align-middle">
                        {showFeedback ? (
                          <div className="flex w-full min-w-0 items-center justify-between gap-2">
                            <div className="min-w-0 flex-1">
                              <DataTableCellView cell={cell} />
                            </div>
                            <div className="flex shrink-0">
                              <ParagraphFeedbackMini paragraphId={pid} module={module} env={env} />
                            </div>
                          </div>
                        ) : (
                          <div className="flex items-center gap-1">
                            <DataTableCellView cell={cell} />
                          </div>
                        )}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function DataTableCellView({ cell }: { cell: DataTableCell }) {
  if (cell.kind === "cover") {
    return <CoverThumb url={cell.coverUrl} noteUrl={cell.noteUrl} size={32} />;
  }
  if (cell.tagColor) {
    const style =
      cell.tagColor === "red"
        ? { background: "#FFF0EE", color: "#FF4757" }
        : cell.tagColor === "green"
          ? { background: "#F0FAF0", color: "#3D8C40" }
          : { background: "#FFF8E6", color: "#8B6914" };
    return (
      <span className="inline-block rounded px-2 py-0.5 text-[10px] font-semibold" style={style}>
        {cell.text ?? ""}
      </span>
    );
  }
  return <>{cell.text ?? ""}</>;
}

function ChecklistView({ section }: { section: ChecklistSection }) {
  return (
    <ul className="mt-1 list-disc pl-[18px] leading-[2]">
      {section.items.map((item, idx) => (
        <li key={idx}>{item}</li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------
// ViralMatrixView(4.3pre.4 新增:爆文模型矩阵手风琴)
// ---------------------------------------------------------------
function ViralMatrixView({
  section,
  module,
  env,
}: {
  section: ViralMatrixSection;
  module: PrototypeModule;
  env?: CanvasParagraphEnv;
}) {
  if (!section.models.length && !section.unusedDirections.length) {
    return (
      <p className="mt-2 text-[12px] text-[#A8A4A0]">
        <em>暂无爆文模型矩阵(等待样本聚类完成)。</em>
      </p>
    );
  }

  return (
    <div className="mt-2 space-y-3">
      <div className="text-[11px] text-[#8A8580]">
        总样本 {section.totalSampleCount} 条 · 统计轴
        <span className="ml-1 rounded bg-[#F5F3F0] px-1.5 py-px text-[#5A5550]">
          {section.statsAxisLabel}
        </span>
        {section.taxonomyVersion ? (
          <span className="ml-2 text-[#C5C0BA]">taxonomy v{section.taxonomyVersion}</span>
        ) : null}
      </div>

      {section.models.map((model, idx) => (
        <ViralModelCard
          key={model.model_id || `m-${idx}`}
          model={model}
          module={module}
          env={env}
          defaultOpen={idx === 0}
        />
      ))}

      {section.unusedDirections.length ? (
        <UnusedDirectionsPanel directions={section.unusedDirections} />
      ) : null}
    </div>
  );
}

function ViralModelCard({
  model,
  module,
  env,
  defaultOpen,
}: {
  model: ViralMatrixModel;
  module: PrototypeModule;
  env?: CanvasParagraphEnv;
  defaultOpen: boolean;
}) {
  const [open, setOpen] = useState<boolean>(defaultOpen);
  const coveragePct = Math.round(model.coverage * 100);

  return (
    <div
      className="rounded-[10px] border border-[#F0EEEB] bg-white"
      data-paragraph-id={model.paragraph_id ?? undefined}
    >
      <div
        className="flex cursor-pointer items-center justify-between px-3 py-2 hover:bg-[#FAFAF8]"
        onClick={() => setOpen((v) => !v)}
      >
        <div className="flex items-center gap-2">
          <span className="rounded bg-[#FFF0EE] px-2 py-0.5 text-[11px] font-bold text-[#FF4757]">
            {model.model_id || "M?"}
          </span>
          <span className="text-[13px] font-semibold text-[#3E3A36]">{model.name || "(未命名)"}</span>
          <span className="text-[10px] text-[#A8A4A0]">
            coverage {coveragePct}% · 均互动 {Math.trunc(model.avg_interaction).toLocaleString("zh-CN")}
          </span>
          {model.paragraph_id && env?.taskId && env.realtime ? (
            <ParagraphFeedbackMini paragraphId={model.paragraph_id} module={module} env={env} />
          ) : null}
        </div>
        <span className={`text-[14px] text-[#C5C0BA] transition ${open ? "rotate-90" : ""}`}>▸</span>
      </div>

      {model.description ? (
        <div className="px-3 pb-1 text-[11px] leading-[1.5] text-[#8A8580]">{model.description}</div>
      ) : null}

      {open ? (
        <div className="border-t border-[#F5F3F0] px-3 py-2">
          {ELEMENT_ORDER.map((code) => {
            const cats = model.elements[code] ?? [];
            if (!cats.length) return null;
            return <ElementRow key={code} code={code} categories={cats} module={module} env={env} />;
          })}
          {ELEMENT_ORDER.every((c) => !(model.elements[c] ?? []).length) ? (
            <p className="text-[11px] text-[#A8A4A0]">
              <em>该模型暂无 6 要素分布数据。</em>
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function ElementRow({
  code,
  categories,
  module,
  env,
}: {
  code: string;
  categories: ViralMatrixElementCategory[];
  module: PrototypeModule;
  env?: CanvasParagraphEnv;
}) {
  const label = ELEMENT_LABEL_MAP[code] ?? code;
  return (
    <div className="mt-2 flex gap-3 first:mt-0">
      <div className="w-[68px] flex-shrink-0 pt-[2px] text-[11px] font-semibold text-[#5A5550]">
        {label}
      </div>
      <div className="flex flex-1 flex-col gap-1.5">
        {categories.map((cat, idx) => (
          <CategoryBar
            key={cat.paragraph_id ?? `${code}-${idx}`}
            category={cat}
            module={module}
            env={env}
          />
        ))}
      </div>
    </div>
  );
}

function CategoryBar({
  category,
  module,
  env,
}: {
  category: ViralMatrixElementCategory;
  module: PrototypeModule;
  env?: CanvasParagraphEnv;
}) {
  const ratioPct = Math.max(0, Math.min(100, Math.round(category.ratio * 100)));

  return (
    <div
      className="rounded-md border border-[#F5F3F0] bg-[#FAFAF8] px-2 py-1.5"
      data-paragraph-id={category.paragraph_id ?? undefined}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-[12px] text-[#3E3A36]">{category.type || "(未分类)"}</span>
        <span className="flex flex-shrink-0 items-center gap-1 text-[10px] text-[#8A8580]">
          <span>
            {ratioPct}% · {category.count} 条
          </span>
          {category.paragraph_id && env?.taskId && env.realtime ? (
            <ParagraphFeedbackMini paragraphId={category.paragraph_id} module={module} env={env} />
          ) : null}
        </span>
      </div>
      <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-[#F0EEEB]">
        <div
          className="h-full rounded-full bg-[#FFD6CC]"
          style={{ width: `${ratioPct}%` }}
        />
      </div>
      {category.examples.length ? (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {category.examples.slice(0, 3).map((ex) => (
            <div
              key={ex.note_id || ex.cover_url}
              title={ex.title}
              className="flex items-center gap-1"
            >
              <CoverThumb url={ex.cover_url} size={40} alt={ex.title} />
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function UnusedDirectionsPanel({
  directions,
}: {
  directions: ViralMatrixSection["unusedDirections"];
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-[10px] border border-[#F5E6CC] bg-[#FFF8F0]">
      <div
        className="flex cursor-pointer items-center justify-between px-3 py-2 text-[11px] font-semibold text-[#8B6914] hover:bg-[#FFF5E0]"
        onClick={() => setOpen((v) => !v)}
      >
        <span>不做的方向(互动量高但链路差 · {directions.length} 条)</span>
        <span className={`text-[14px] text-[#C5C0BA] transition ${open ? "rotate-90" : ""}`}>▸</span>
      </div>
      {open ? (
        <ul className="space-y-1 px-3 pb-2 text-[11px] text-[#5A5550]">
          {directions.map((d, idx) => (
            <li key={`${d.direction}-${idx}`}>
              <span className="font-semibold">{d.direction}</span>
              <span className="text-[#A8A4A0]"> · 占比 {Math.round(d.ratio * 100)}% · 均互动 {d.avg_interaction.toLocaleString("zh-CN")}</span>
              {d.reason ? <span className="text-[#A8A4A0]"> · {d.reason}</span> : null}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
