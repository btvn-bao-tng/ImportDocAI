import { useEffect, useId, useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { ShipmentStatus, ShipmentSummary } from "../api/types";
import {
  Button,
  Callout,
  Chip,
  Container,
  EmptyState,
  LocalizedStatus,
  PageHeader,
  Section,
  Spinner,
  StatusBadge,
  Table,
  TableHeadCell,
} from "../components/ui";

const STATUS_OPTIONS: { value: "" | ShipmentStatus; label: string }[] = [
  { value: "", label: "Tất cả" },
  { value: "DRAFT", label: "DRAFT" },
  { value: "PROCESSING", label: "PROCESSING" },
  { value: "NEEDS_REVIEW", label: "NEEDS_REVIEW" },
  { value: "READY_TO_EXPORT", label: "READY_TO_EXPORT" },
  { value: "EXPORTED", label: "EXPORTED" },
];

const dateFormatter = new Intl.DateTimeFormat("vi-VN", {
  dateStyle: "short",
  timeStyle: "short",
});

function formatDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : dateFormatter.format(d);
}

function useDebounced<T>(value: T, delay = 250): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

const CARD = "rounded-lg border border-hairline bg-canvas";
const CELL = "py-4 pr-3 align-top";

function ShipmentRow({ shipment }: { shipment: ShipmentSummary }) {
  const critical = shipment.critical_unreviewed > 0;

  return (
    <tr className="border-t border-divider transition-colors duration-150 hover:bg-parchment">
      <td className={`${CELL} pl-6`}>
        <Link
          to={`/shipments/${shipment.id}/review`}
          className="type-body-strong text-action active:opacity-70"
        >
          {shipment.code}
        </Link>
      </td>
      <td className={`${CELL} type-body text-ink-80`}>
        {shipment.customer_name || <span className="text-ink-48">—</span>}
      </td>
      <td className={CELL}>
        <StatusBadge status={shipment.status} />
      </td>
      <td className={`${CELL} type-body text-right tabular-nums text-ink-80`}>{shipment.document_count}</td>
      <td className={`${CELL} type-body text-right tabular-nums text-ink-80`}>{shipment.extracted_count}</td>
      <td className={`${CELL} type-caption whitespace-nowrap tabular-nums text-ink-48`}>
        {formatDate(shipment.created_at)}
      </td>
      <td className={`${CELL} pr-6 text-right`}>
        {critical ? (
          <LocalizedStatus tone="red">{shipment.critical_unreviewed} cần xem lại</LocalizedStatus>
        ) : (
          <span className="type-caption text-ink-48">—</span>
        )}
      </td>
    </tr>
  );
}

function FormField({ label, htmlFor, children }: { label: string; htmlFor: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={htmlFor} className="type-caption-strong text-ink">
        {label}
      </label>
      {children}
    </div>
  );
}

const INPUT_CLASS =
  "h-11 w-full rounded-md border border-hairline bg-canvas px-3 type-body text-ink placeholder:text-ink-48 " +
  "transition-colors duration-150 focus:border-action focus:outline-none";

function CreateShipmentForm({ onDone }: { onDone: () => void }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const codeId = useId();
  const customerId = useId();
  const [code, setCode] = useState("");
  const [customerName, setCustomerName] = useState("");

  const mutation = useMutation({
    mutationFn: () =>
      api.createShipment({
        code: code.trim(),
        customer_name: customerName.trim() || undefined,
      }),
    onSuccess: async (created) => {
      await queryClient.invalidateQueries({ queryKey: ["shipments"] });
      navigate(`/shipments/${created.id}/upload`);
    },
  });

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!code.trim() || mutation.isPending) return;
    mutation.mutate();
  }

  return (
    <form onSubmit={handleSubmit} className={`${CARD} mb-6 p-6`} aria-label="Tạo lô hàng mới">
      <div className="grid gap-5 sm:grid-cols-2">
        <FormField label="Mã lô hàng" htmlFor={codeId}>
          <input
            id={codeId}
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="VD: IMP-2026-001"
            autoFocus
            className={INPUT_CLASS}
          />
        </FormField>
        <FormField label="Khách hàng" htmlFor={customerId}>
          <input
            id={customerId}
            value={customerName}
            onChange={(e) => setCustomerName(e.target.value)}
            placeholder="Tên khách hàng (không bắt buộc)"
            className={INPUT_CLASS}
          />
        </FormField>
      </div>

      {mutation.isError && (
        <div className="mt-5">
          <Callout tone="critical" title="Không tạo được lô hàng">
            {mutation.error.message}
          </Callout>
        </div>
      )}

      <div className="mt-6 flex items-center justify-end gap-3">
        <Button variant="ghost" type="button" onClick={onDone} disabled={mutation.isPending}>
          Hủy
        </Button>
        <Button variant="primary" type="submit" disabled={!code.trim() || mutation.isPending}>
          {mutation.isPending ? "Đang tạo…" : "Tạo lô hàng"}
        </Button>
      </div>
    </form>
  );
}

export default function ShipmentsPage() {
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<"" | ShipmentStatus>("");
  const [showForm, setShowForm] = useState(false);
  const debouncedSearch = useDebounced(search.trim());

  const shipmentsQuery = useQuery({
    queryKey: ["shipments", debouncedSearch, status],
    queryFn: () => api.listShipments({ q: debouncedSearch || undefined, status: status || undefined }),
  });

  const shipments = shipmentsQuery.data;

  return (
    <>
      <Section tone="light">
        <Container width="content">
          <PageHeader
            title="Danh sách lô hàng"
            subtitle="Theo dõi và xử lý chứng từ nhập khẩu theo từng lô hàng."
            actions={
              <Button variant="primary" onClick={() => setShowForm((v) => !v)}>
                {showForm ? "Đóng" : "Tạo lô hàng"}
              </Button>
            }
          />

          {showForm && <CreateShipmentForm onDone={() => setShowForm(false)} />}

          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <div className="relative w-full sm:max-w-[420px]">
              <svg
                aria-hidden="true"
                viewBox="0 0 16 16"
                className="pointer-events-none absolute top-1/2 left-5 h-3.5 w-3.5 -translate-y-1/2 fill-none stroke-ink-48"
                strokeWidth="1.6"
              >
                <circle cx="7" cy="7" r="4.5" />
                <path d="M10.5 10.5 14 14" strokeLinecap="round" />
              </svg>
              <input
                type="search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Tìm theo mã lô hoặc khách hàng"
                aria-label="Tìm kiếm lô hàng"
                className="h-11 w-full rounded-pill border border-[rgba(0,0,0,0.08)] bg-canvas pr-5 pl-11 type-body text-ink placeholder:text-ink-48 transition-colors duration-150 focus:border-action focus:outline-none"
              />
            </div>
            <div
              role="group"
              aria-label="Lọc theo trạng thái"
              className="flex flex-wrap items-center gap-2 sm:ml-auto"
            >
              {STATUS_OPTIONS.map((opt) => (
                <Chip
                  key={opt.value || "all"}
                  active={status === opt.value}
                  onClick={() => setStatus(opt.value)}
                >
                  {opt.label}
                </Chip>
              ))}
            </div>
          </div>

          <div className="mt-6">
            {shipmentsQuery.isPending ? (
              <div className={`${CARD} flex items-center justify-center px-6 py-20`}>
                <Spinner />
              </div>
            ) : shipmentsQuery.isError ? (
              <Callout tone="critical" title="Không tải được danh sách lô hàng">
                {shipmentsQuery.error.message}
              </Callout>
            ) : !shipments || shipments.length === 0 ? (
              <EmptyState
                title="Chưa có lô hàng nào"
                description={
                  debouncedSearch || status
                    ? "Không có lô hàng phù hợp với bộ lọc hiện tại."
                    : "Tạo lô hàng đầu tiên để bắt đầu tải chứng từ."
                }
                action={
                  !debouncedSearch && !status ? (
                    <Button variant="primary" onClick={() => setShowForm(true)}>
                      Tạo lô hàng
                    </Button>
                  ) : undefined
                }
              />
            ) : (
              <div className={`${CARD} px-6 py-2`}>
                <Table
                  minWidth={880}
                  head={
                    <tr className="[&>th]:px-0 [&>th]:pr-3 [&>th:last-child]:pr-0">
                      <TableHeadCell>Mã lô</TableHeadCell>
                      <TableHeadCell>Khách hàng</TableHeadCell>
                      <TableHeadCell>Trạng thái</TableHeadCell>
                      <TableHeadCell align="right">Chứng từ</TableHeadCell>
                      <TableHeadCell align="right">Đã trích xuất</TableHeadCell>
                      <TableHeadCell>Thời gian tạo</TableHeadCell>
                      <TableHeadCell align="right">Xem lại</TableHeadCell>
                    </tr>
                  }
                >
                  {shipments.map((shipment) => (
                    <ShipmentRow key={shipment.id} shipment={shipment} />
                  ))}
                </Table>
              </div>
            )}
          </div>
        </Container>
      </Section>
      <Section tone="parchment" className="!py-12">
        <Container width="content">
          <span className="sr-only">Hết danh sách lô hàng.</span>
        </Container>
      </Section>
    </>
  );
}
