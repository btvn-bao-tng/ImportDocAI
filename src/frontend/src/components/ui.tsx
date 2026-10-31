import type {
  ButtonHTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
} from "react";
import { Link } from "react-router-dom";

export type Tone = "gray" | "amber" | "green" | "red" | "blue";

/* -------------------------------------------------------------------------- */
/* LocalizedStatus — the single status pill primitive                          */
/* -------------------------------------------------------------------------- */

const statusToneStyles: Record<Tone, { pill: string; dot: string }> = {
  gray: { pill: "border-hairline bg-parchment text-ink-80", dot: "bg-ink-48" },
  amber: { pill: "border-[#efdfba] bg-[#fff7e6] text-warning", dot: "bg-warning" },
  green: { pill: "border-[#c9e4d4] bg-[#eaf6ef] text-success", dot: "bg-success" },
  red: { pill: "border-[#f1ccc8] bg-[#fdeceb] text-critical", dot: "bg-critical" },
  blue: { pill: "border-[#c8dbf5] bg-[#eaf2fd] text-action", dot: "bg-action" },
};

export type LocalizedTone = Tone | "critical" | "warning" | "success" | "positive" | "neutral" | "info";

const toneAliases: Record<string, Tone> = {
  critical: "red",
  warning: "amber",
  success: "green",
  positive: "green",
  neutral: "gray",
  info: "blue",
};

export function LocalizedStatus({
  tone = "gray",
  className = "",
  children,
}: {
  tone: LocalizedTone;
  className?: string;
  children: ReactNode;
}) {
  const style = statusToneStyles[toneAliases[tone] ?? (tone as Tone)] ?? statusToneStyles.gray;
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-pill border px-2.5 py-[3px] text-fine-print leading-[1.4] whitespace-nowrap ${style.pill} ${className}`}
    >
      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${style.dot}`} aria-hidden="true" />
      {children}
    </span>
  );
}

const shipmentStatus: Record<string, { label: string; tone: Tone }> = {
  DRAFT: { label: "Bản nháp", tone: "gray" },
  PROCESSING: { label: "Đang xử lý", tone: "amber" },
  NEEDS_REVIEW: { label: "Cần xem lại", tone: "red" },
  READY_TO_EXPORT: { label: "Sẵn sàng xuất", tone: "green" },
  EXPORTED: { label: "Đã xuất", tone: "green" },
  PENDING: { label: "Chờ xử lý", tone: "gray" },
  UPLOADED: { label: "Đã tải lên", tone: "gray" },
  QUEUED: { label: "Trong hàng đợi", tone: "amber" },
  EXTRACTED: { label: "Đã trích xuất", tone: "green" },
  DUPLICATE: { label: "Trùng lặp", tone: "amber" },
  FAILED: { label: "Lỗi", tone: "red" },
  ERROR: { label: "Lỗi", tone: "red" },
  RUNNING: { label: "Đang chạy", tone: "amber" },
  SUCCESS: { label: "Hoàn tất", tone: "green" },
  DONE: { label: "Hoàn tất", tone: "green" },
};

function formatStatus(status: string): string {
  const words = status.toLowerCase().split("_").filter(Boolean);
  return words.map((word) => word.charAt(0).toUpperCase() + word.slice(1)).join(" ");
}

export function StatusBadge({ status }: { status: string }) {
  const known = shipmentStatus[status.toUpperCase()];
  return (
    <LocalizedStatus tone={known?.tone ?? "gray"}>{known?.label ?? formatStatus(status)}</LocalizedStatus>
  );
}

const provenanceLabels: Record<"TEXT_LAYER" | "IMAGE" | "CELL", string> = {
  TEXT_LAYER: "Văn bản PDF",
  IMAGE: "Ảnh quét",
  CELL: "Ô bảng tính",
};

export function ProvenanceBadge({ type }: { type: "TEXT_LAYER" | "IMAGE" | "CELL" }) {
  return (
    <span className="inline-flex items-center rounded-xs border border-hairline bg-pearl px-1.5 py-[2px] text-fine-print leading-[1.4] text-ink-48 whitespace-nowrap">
      {provenanceLabels[type]}
    </span>
  );
}

const reviewLabels: Record<string, string> = {
  UNREVIEWED: "Chưa xem",
  CONFIRMED: "Đã xác nhận",
  EDITED: "Đã sửa",
  NEEDS_REVIEW: "Cần xem lại",
};

function reviewTone(status: string, critical: boolean): Tone {
  if (status === "NEEDS_REVIEW") return "red";
  if (status === "CONFIRMED") return "green";
  if (status === "EDITED") return "blue";
  return critical ? "red" : "gray";
}

export function ReviewBadge({ status, critical }: { status: string; critical: boolean }) {
  const label = reviewLabels[status] ?? formatStatus(status);
  return (
    <LocalizedStatus tone={reviewTone(status, critical)}>
      {label}
      {critical && status === "UNREVIEWED" ? <span className="opacity-80">· quan trọng</span> : null}
    </LocalizedStatus>
  );
}

/* -------------------------------------------------------------------------- */
/* Button                                                                      */
/* -------------------------------------------------------------------------- */

const BASE_BUTTON =
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-pill font-sans font-normal " +
  "outline-offset-2 transition-colors duration-150 active:scale-95 " +
  "disabled:pointer-events-none disabled:opacity-40";

const buttonVariants: Record<"primary" | "secondary" | "ghost", string> = {
  primary: "bg-action text-canvas",
  secondary: "border border-action bg-transparent text-action",
  ghost: "border border-transparent bg-transparent px-1 text-action underline-offset-4",
};

const buttonSizes: Record<"sm" | "md", string> = {
  sm: "min-h-9 px-4 text-caption",
  md: "min-h-11 px-[22px] text-body",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "ghost";
  size?: "sm" | "md";
}

export function Button({
  variant = "primary",
  size = "md",
  className = "",
  type = "button",
  ...rest
}: ButtonProps) {
  const sizing = variant === "ghost" ? "min-h-11 py-1 text-body" : buttonSizes[size];
  return (
    <button
      type={type}
      className={`${BASE_BUTTON} ${buttonVariants[variant]} ${sizing} ${className}`}
      {...rest}
    />
  );
}

/* -------------------------------------------------------------------------- */
/* Link-as-CTA (secondary pill / text link that navigates)                     */
/* -------------------------------------------------------------------------- */

const linkVariants: Record<"primary" | "secondary" | "text", string> = {
  primary: "bg-action text-canvas",
  secondary: "border border-action bg-transparent text-action",
  text: "bg-transparent px-1 text-action underline-offset-4",
};

export function ButtonLink({
  to,
  href,
  variant = "primary",
  size = "md",
  className = "",
  children,
  ...rest
}: {
  to?: string;
  href?: string;
  variant?: "primary" | "secondary" | "text";
  size?: "sm" | "md";
  className?: string;
  children: ReactNode;
} & Omit<React.AnchorHTMLAttributes<HTMLAnchorElement>, "href" | "children" | "className">) {
  const sizing = variant === "text" ? "min-h-11 py-1 text-body" : buttonSizes[size];
  const classes = `${BASE_BUTTON} ${linkVariants[variant]} ${sizing} ${className}`;
  if (to) {
    return (
      <Link to={to} className={classes} {...rest}>
        {children}
      </Link>
    );
  }
  return (
    <a href={href} className={classes} {...rest}>
      {children}
    </a>
  );
}

/* -------------------------------------------------------------------------- */
/* Callout                                                                     */
/* -------------------------------------------------------------------------- */

const calloutTones: Record<"info" | "warning" | "critical", { box: string; title: string }> = {
  info: { box: "border-hairline bg-pearl", title: "text-ink" },
  warning: { box: "border-[#efdfba] bg-[#fff7e6]", title: "text-warning" },
  critical: { box: "border-[#f1ccc8] bg-[#fdeceb]", title: "text-critical" },
};

export function Callout({
  tone,
  title,
  children,
}: {
  tone: "info" | "warning" | "critical";
  title?: string;
  children: ReactNode;
}) {
  const styles = calloutTones[tone];
  return (
    <div role={tone === "info" ? "note" : "alert"} className={`rounded-md border px-4 py-3 ${styles.box}`}>
      {title ? (
        <p className={`type-caption-strong mb-1 ${styles.title}`}>{title}</p>
      ) : null}
      <div className="type-body text-ink-80 [&_a]:text-action [&_a]:underline">{children}</div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Feedback                                                                    */
/* -------------------------------------------------------------------------- */

export function Spinner({ label = "Đang tải…" }: { label?: string }) {
  return (
    <span role="status" aria-live="polite" className="inline-flex items-center gap-2 text-ink-48">
      <span
        className="h-4 w-4 animate-spin rounded-full border-2 border-hairline border-t-action"
        aria-hidden="true"
      />
      <span className="type-caption">{label}</span>
    </span>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-lg border border-hairline bg-canvas px-6 py-12 text-center">
      <h3 className="type-tagline text-ink">{title}</h3>
      {description ? (
        <p className="type-body max-w-[52ch] text-ink-48">{description}</p>
      ) : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Forms                                                                       */
/* -------------------------------------------------------------------------- */

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="type-caption-strong text-ink">{label}</span>
      {children}
    </label>
  );
}

const FIELD_CONTROL =
  "w-full border border-hairline bg-canvas text-ink placeholder:text-ink-48 " +
  "outline-offset-2 transition-colors duration-150 focus:border-action " +
  "disabled:opacity-40";

export interface TextInputProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
}

export function TextInput({ label, className = "", id, ...rest }: TextInputProps) {
  const inputId = id ?? `field-${label.replace(/\s+/g, "-").toLowerCase()}`;
  return (
    <label htmlFor={inputId} className="flex w-full flex-col gap-1.5">
      <span className="type-caption-strong text-ink">{label}</span>
      <input
        id={inputId}
        className={`${FIELD_CONTROL} type-body h-11 rounded-pill px-5 ${className}`}
        {...rest}
      />
    </label>
  );
}

export interface SelectFieldProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label: string;
}

export function SelectField({ label, className = "", id, children, ...rest }: SelectFieldProps) {
  const selectId = id ?? `field-${label.replace(/\s+/g, "-").toLowerCase()}`;
  return (
    <label htmlFor={selectId} className="flex w-full flex-col gap-1.5">
      <span className="type-caption-strong text-ink">{label}</span>
      <select
        id={selectId}
        className={`${FIELD_CONTROL} type-body h-11 rounded-md px-3 focus:outline-none ${className}`}
        {...rest}
      >
        {children}
      </select>
    </label>
  );
}

/* -------------------------------------------------------------------------- */
/* Surface & layout primitives                                                 */
/* -------------------------------------------------------------------------- */

const sectionTones: Record<"light" | "parchment" | "dark", string> = {
  light: "bg-canvas text-ink",
  parchment: "bg-parchment text-ink",
  dark: "bg-tile text-body-on-dark",
};

export function Section({
  id,
  tone = "light",
  className = "",
  children,
}: {
  id?: string;
  tone?: "light" | "parchment" | "dark";
  className?: string;
  children: ReactNode;
}) {
  return (
    <section
      id={id}
      className={`px-4 py-12 sm:px-6 sm:py-20 ${sectionTones[tone]} ${className}`}
    >
      {children}
    </section>
  );
}

const containerWidths: Record<"text" | "content" | "wide", string> = {
  text: "max-w-[980px]",
  content: "max-w-[1440px]",
  wide: "max-w-none",
};

export function Container({
  width = "content",
  className = "",
  children,
}: {
  width?: "text" | "content" | "wide";
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={`mx-auto w-full px-4 sm:px-6 ${containerWidths[width]} ${className}`}>
      {children}
    </div>
  );
}

export function Card({ className = "", children }: { className?: string; children: ReactNode }) {
  return (
    <div className={`rounded-lg border border-hairline bg-canvas p-6 ${className}`}>{children}</div>
  );
}

export function ProductImage({
  src,
  alt = "",
  className = "",
  ...rest
}: {
  src: string;
  alt?: string;
  className?: string;
} & Omit<React.ImgHTMLAttributes<HTMLImageElement>, "src" | "alt" | "className">) {
  return (
    <img
      src={src}
      alt={alt}
      className={`block rounded-sm shadow-product ${className}`}
      {...rest}
    />
  );
}

/* -------------------------------------------------------------------------- */
/* PageHeader                                                                  */
/* -------------------------------------------------------------------------- */

export function PageHeader({
  eyebrow,
  title,
  subtitle,
  actions,
}: {
  eyebrow?: ReactNode;
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="flex flex-col gap-6 pb-8 sm:flex-row sm:items-end sm:justify-between">
      <div className="flex min-w-0 flex-col gap-2">
        {eyebrow ? (
          <p className="type-caption text-ink-48 uppercase tracking-[0.06em]">{eyebrow}</p>
        ) : null}
        <h1 className="type-display-md text-ink">{title}</h1>
        {subtitle ? <p className="type-body max-w-[68ch] text-ink-48">{subtitle}</p> : null}
      </div>
      {actions ? (
        <div className="flex flex-wrap items-center gap-3 sm:shrink-0 sm:justify-end">{actions}</div>
      ) : null}
    </header>
  );
}

/* -------------------------------------------------------------------------- */
/* Chip                                                                        */
/* -------------------------------------------------------------------------- */

export function Chip({
  active = false,
  onClick,
  children,
  role,
  className = "",
  title,
  disabled,
}: {
  active?: boolean;
  onClick?: () => void;
  children: ReactNode;
  role?: string;
  className?: string;
  title?: string;
  disabled?: boolean;
}) {
  const interactive = typeof onClick === "function";
  const classes =
    "inline-flex min-h-9 items-center justify-center rounded-pill border px-4 text-caption whitespace-nowrap " +
    "outline-offset-2 transition-colors duration-150 active:scale-95 disabled:opacity-40 " +
    (active
      ? "border-action bg-canvas text-action"
      : "border-hairline bg-canvas text-ink-80") +
    ` ${className}`;
  if (!interactive) {
    return (
      <span className={classes} title={title}>
        {children}
      </span>
    );
  }
  return (
    <button
      type="button"
      role={role}
      aria-pressed={role === undefined || role === "button" ? active : undefined}
      aria-selected={role === "tab" ? active : undefined}
      onClick={onClick}
      title={title}
      disabled={disabled}
      className={classes}
    >
      {children}
    </button>
  );
}

/* -------------------------------------------------------------------------- */
/* Stat                                                                        */
/* -------------------------------------------------------------------------- */

export function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: ReactNode;
  tone?: "default" | "critical" | "success";
}) {
  const valueColor =
    tone === "critical" ? "text-critical" : tone === "success" ? "text-success" : "text-ink";
  return (
    <div className="flex flex-col gap-1">
      <span className="text-fine-print text-ink-48">{label}</span>
      <span className={`type-body font-normal tabular-nums ${valueColor}`}>{value}</span>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Table                                                                       */
/* -------------------------------------------------------------------------- */

export function TableHeadCell({
  children,
  align = "left",
}: {
  children?: ReactNode;
  align?: "left" | "right";
}) {
  return (
    <th
      scope="col"
      className={`pb-3 text-fine-print font-normal text-ink-48 ${align === "right" ? "text-right" : "text-left"}`}
    >
      {children}
    </th>
  );
}

export function Table({
  caption,
  head,
  children,
  minWidth,
}: {
  caption?: ReactNode;
  head: ReactNode;
  children: ReactNode;
  minWidth?: number | string;
}) {
  return (
    <div className="-mx-4 overflow-x-auto px-4 pb-1 sm:mx-0 sm:px-0">
      <table className="w-full border-collapse text-left" style={minWidth ? { minWidth } : undefined}>
        {caption ? <caption className="type-caption pb-3 text-left text-ink-48">{caption}</caption> : null}
        <thead className="border-b border-hairline">{head}</thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* StickyActionBar                                                             */
/* -------------------------------------------------------------------------- */

export function StickyActionBar({ children }: { children: ReactNode }) {
  return (
    <div
      className="sticky bottom-0 z-20 flex min-h-16 flex-wrap items-center justify-between gap-3 border-t border-hairline
        bg-parchment/80 px-4 py-3 backdrop-blur-xl backdrop-saturate-150 sm:px-8"
    >
      {children}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Progress                                                                   */
/* -------------------------------------------------------------------------- */

function clampRatio(value: number, max: number): number {
  if (!Number.isFinite(value) || max <= 0) return 0;
  return Math.min(1, Math.max(0, value / max));
}

export function ProgressRing({
  value,
  max,
  label,
  size = 44,
}: {
  value: number;
  max: number;
  label?: string;
  size?: number;
}) {
  const ratio = clampRatio(value, max);
  const stroke = 3;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const percent = Math.round(ratio * 100);
  const text = label ?? `${percent}%`;
  return (
    <span
      role="img"
      aria-label={`Tiến độ ${value}/${max}`}
      className="relative inline-flex shrink-0 items-center justify-center"
      style={{ width: size, height: size }}
    >
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="var(--color-hairline)"
          strokeWidth={stroke}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="var(--color-action)"
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={`${circumference * ratio} ${circumference}`}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
        />
      </svg>
      <span className="absolute text-[10px] leading-none tabular-nums text-ink-80">{text}</span>
    </span>
  );
}

export function Stepper({
  steps,
  current,
}: {
  steps: string[];
  current: number;
}) {
  return (
    <ol className="flex flex-wrap items-center gap-x-3 gap-y-2 list-none p-0">
      {steps.map((step, index) => {
        const done = index < current;
        const active = index === current;
        return (
          <li key={step} className="flex items-center gap-2">
            <span
              aria-hidden="true"
              className={`inline-flex h-5 w-5 items-center justify-center rounded-full border text-[10px] tabular-nums ${
                active
                  ? "border-action bg-action text-canvas"
                  : done
                    ? "border-action text-action"
                    : "border-hairline text-ink-48"
              }`}
            >
              {index + 1}
            </span>
            <span
              aria-current={active ? "step" : undefined}
              className={`type-caption ${active ? "text-ink" : "text-ink-48"}`}
            >
              {step}
            </span>
            {index < steps.length - 1 ? (
              <span aria-hidden="true" className="h-px w-4 bg-hairline" />
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}
