import type { ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";
import { ButtonLink, Container } from "./ui";

const productLinks = [
  { to: "/", label: "Danh sách lô hàng" },
];
const precisionLinks = [
  { to: "/", label: "Lô hàng" },
  { to: "/#tai-lieu", label: "Tài liệu" },
  { to: "/#xuat", label: "Xuất Excel" },
];
const supportLinks = [{ to: "/#ho-tro", label: "Hỗ trợ" }];

const navLinkClasses =
  "type-nav-link inline-flex min-h-11 items-center text-body-on-dark opacity-85 sm:min-h-0";

export function GlobalNav() {
  return (
    <nav aria-label="Điều hướng chính" className="bg-black text-body-on-dark">
      <Container width="content" className="px-4 sm:px-6">
        <div className="flex h-11 items-center justify-between gap-6">
          <Link to="/" className="flex shrink-0 items-center gap-2">
            <span aria-hidden="true" className="h-3.5 w-3.5 rounded-full border border-body-on-dark" />
            <span className="type-body-strong text-body-on-dark">ImportDoc AI</span>
          </Link>
          <ul className="hidden items-center gap-5 min-[834px]:flex">
            {productLinks.map((link) => (
              <li key={link.to}>
                <NavLink to={link.to} end className={navLinkClasses}>
                  {link.label}
                </NavLink>
              </li>
            ))}
            {precisionLinks.map((link) => (
              <li key={link.label}>
                <Link to={link.to} className={navLinkClasses}>
                  {link.label}
                </Link>
              </li>
            ))}
            {supportLinks.map((link) => (
              <li key={link.label}>
                <a href={link.to} className={navLinkClasses}>
                  {link.label}
                </a>
              </li>
            ))}
          </ul>
          <div className="shrink-0 min-[834px]:hidden">
            <ButtonLink to="/" size="sm" className="min-h-9 px-3 text-fine-print">
              Lô hàng
            </ButtonLink>
          </div>
        </div>
      </Container>
    </nav>
  );
}

export function SubNav({
  title,
  links = [],
  cta,
}: {
  title: ReactNode;
  links?: { label: string; to?: string; href?: string; current?: boolean }[];
  cta?: ReactNode;
}) {
  return (
    <div className="sticky top-0 z-30 border-b border-black/[0.08] bg-parchment/80 backdrop-blur-xl backdrop-saturate-150">
      <Container width="content" className="px-4 sm:px-6">
        <div className="flex h-[52px] items-center justify-between gap-6">
          <p className="type-tagline truncate text-ink">{title}</p>
          <div className="flex shrink-0 items-center gap-5">
            <nav aria-label="Điều hướng trên trang" className="hidden items-center gap-5 sm:flex">
              {links.map((link) =>
                link.to ? (
                  <Link
                    key={link.label}
                    to={link.to}
                    aria-current={link.current ? "page" : undefined}
                    className={`type-caption ${link.current ? "text-action" : "text-ink-80"}`}
                  >
                    {link.label}
                  </Link>
                ) : (
                  <a
                    key={link.label}
                    href={link.href ?? "#"}
                    aria-current={link.current ? "page" : undefined}
                    className={`type-caption ${link.current ? "text-action" : "text-ink-80"}`}
                  >
                    {link.label}
                  </a>
                ),
              )}
            </nav>
            {cta ? <div className="shrink-0">{cta}</div> : null}
          </div>
        </div>
      </Container>
    </div>
  );
}

const footerColumns: { heading: string; links: { label: string; to: string }[] }[] = [
  {
    heading: "Vận hành",
    links: [
      { label: "Danh sách lô hàng", to: "/" },
      { label: "Tải lên chứng từ", to: "/#tai-lieu" },
      { label: "Trích xuất dữ liệu", to: "/#trich-xuat" },
      { label: "Xem lại trường", to: "/#xem-lai" },
      { label: "Xuất Excel", to: "/#xuat" },
    ],
  },
  {
    heading: "Chứng từ",
    links: [
      { label: "Packing List", to: "/#packing-list" },
      { label: "Hoá đơn thương mại", to: "/#invoice" },
      { label: "Hợp đồng", to: "/#hop-dong" },
      { label: "Danh sách dòng hàng", to: "/#dong-hang" },
      { label: "Bên tham gia", to: "/#cac-ben" },
    ],
  },
  {
    heading: "Đối soát",
    links: [
      { label: "Trường quan trọng", to: "/#truong-quan-trong" },
      { label: "Nguồn và vị trí", to: "/#nguon" },
      { label: "Trường còn thiếu", to: "/#trong" },
      { label: "Báo cáo đối soát", to: "/#bao-cao" },
      { label: "Nhật ký xử lý", to: "/#nhat-ky" },
    ],
  },
  {
    heading: "Hỗ trợ",
    links: [
      { label: "Hướng dẫn sử dụng", to: "/#huong-dan" },
      { label: "Câu hỏi thường gặp", to: "/#faq" },
      { label: "Liên hệ", to: "/#lien-he" },
      { label: "Quyền riêng tư", to: "/#quyen-rieng-tu" },
      { label: "Điều khoản", to: "/#dieu-khoan" },
    ],
  },
];

export function Footer() {
  return (
    <footer className="bg-parchment text-ink-80">
      <Container width="content" className="px-4 py-16 sm:px-6">
        <div className="grid grid-cols-2 gap-8 sm:grid-cols-4">
          {footerColumns.map((column) => (
            <div key={column.heading}>
              <h2 className="type-caption-strong mb-2 text-ink">{column.heading}</h2>
              <ul className="list-none p-0">
                {column.links.map((link) => (
                  <li key={link.label}>
                    <Link to={link.to} className="type-dense-link text-ink-80">
                      {link.label}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
        <div className="mt-12 border-t border-hairline pt-6">
          <p className="text-fine-print text-ink-48">
            © ImportDoc AI. Bản quyền được bảo lưu.
          </p>
          <p className="mt-1 text-fine-print text-ink-48">
            Số liệu trích xuất chỉ mang tính hỗ trợ — luôn kiểm tra chứng từ gốc trước khi khai báo.
          </p>
        </div>
      </Container>
    </footer>
  );
}

export function Main({
  children,
  footer = true,
  nav = true,
}: {
  children: ReactNode;
  footer?: boolean;
  nav?: boolean;
}) {
  return (
    <div className="flex min-h-screen flex-col bg-parchment text-ink">
      {nav ? <GlobalNav /> : null}
      <div className="flex-1">{children}</div>
      {footer ? <Footer /> : null}
    </div>
  );
}

export interface ShellProps {
  children: ReactNode;
  subNav?: ReactNode;
  footer?: boolean;
}

export default function Shell({ children, subNav, footer = true }: ShellProps) {
  return (
    <div className="flex min-h-screen flex-col bg-parchment text-ink">
      <a
        href="#noi-dung"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:rounded-sm focus:bg-canvas focus:px-3 focus:py-2 focus:text-caption focus:text-action"
      >
        Bỏ qua điều hướng
      </a>
      <GlobalNav />
      {subNav}
      <main id="noi-dung" className="flex-1">
        {children}
      </main>
      {footer ? <Footer /> : null}
    </div>
  );
}
